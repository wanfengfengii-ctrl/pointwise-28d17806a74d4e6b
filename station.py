import argparse
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self

from capture.ownership import AreaOwnership
from capture.receipts import receipt_id_for
from capture.session import CaptureSession, CaptureUpdate
from capture.shared import (
    SharedCapture, SharedUpdate, validate_shared_input, window_layout,
)
from device.board import Board
from device.descriptors import LANES, Attempt, RecordingSpec, SegmentSpec
from device.events import RecordingEvent
from device.receiver import ReceiveArea
from handover.export import export as export_shift
from handover.lookup import get as get_artifact
from orders.dispatch import LaneDispatch
from orders.intake import open as open_ticket
from orders.ledger import RegistrationEntry, RegistrationLedger
from orders.lifecycle import Ticket
from orders.retry import RetryStatus, redo as redo_ticket
from publication.artifact import SignedPair
from publication.seal import seal as seal_ticket
from publication.store import PublicationStore


class Station:
    def __init__(self, database: str | Path, *, areas_per_lane: int = 2) -> None:
        if type(areas_per_lane) is not int or areas_per_lane < 1:
            raise ValueError('每道接收区数量须为正整数')
        areas = [ReceiveArea(f'{lane}:{slot}', lane) for lane in LANES for slot in range(areas_per_lane)]
        self.board = Board()
        self.ownership = AreaOwnership(areas)
        self.capture = CaptureSession(self.board, self.ownership)
        self.connection = sqlite3.connect(database, isolation_level=None)
        try:
            self.connection.execute('PRAGMA foreign_keys = ON')
            self.ledger = RegistrationLedger(self.connection)
            self.publications = PublicationStore(self.connection)
            self.ledger.initialize()
            self.publications.initialize()
        except BaseException:
            self.connection.close()
            raise
        self.dispatch = LaneDispatch(self.capture, self.ledger)
        self.shared = SharedCapture(self.board, self.ownership, self.capture.receipts, self.ledger)
        self.shared.on_area_freed = self.dispatch.wake
        self._tickets: dict[str, Ticket] = {}
        self._shared_of: dict[str, str] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def open(self, shift: str, ticket_id: str, lane: str, spec: RecordingSpec) -> Ticket:
        ticket = open_ticket(self.ledger, self.dispatch, shift, ticket_id, lane, spec)
        self._tickets[ticket_id] = ticket
        return ticket

    def open_shared(
        self, shift: str, shared_id: str, lane: str, spec: RecordingSpec, windows: dict,
    ) -> tuple[str, dict[str, Ticket]] | str:
        plans = validate_shared_input(shift, shared_id, lane, spec, windows)
        if self.ledger.shared_known(shared_id):
            raise ValueError(f'共录号已登记 {shared_id}')
        for plan in plans:
            if self.ledger.get(plan.ticket_id) is not None:
                raise ValueError(f'工单号已登记 {plan.ticket_id}')
        if not self.shared.has_free_area(lane):
            return 'busy'
        entries = self.ledger.register_shared(shared_id, shift, lane, tuple(plan.ticket_id for plan in plans))
        placements = []
        tickets: dict[str, Ticket] = {}
        for entry, plan in zip(entries, plans):
            layout = window_layout(spec, plan)
            ticket = Ticket(entry, layout)
            placements.append((ticket, plan, layout))
            tickets[plan.ticket_id] = ticket
        self.shared.start(shared_id, lane, spec, tuple(placements))
        self._tickets.update(tickets)
        for ticket_id in tickets:
            self._shared_of[ticket_id] = shared_id
        return shared_id, tickets

    def _live_ticket(self, ticket_id: str) -> Ticket:
        try:
            return self._tickets[ticket_id]
        except KeyError as exc:
            raise LookupError(f'当前会话无该工单 {ticket_id}') from exc

    def expire_first(self, ticket_id: str) -> bool:
        shared_id = self._shared_of.get(ticket_id)
        if shared_id is not None:
            return self.shared.expire_first(shared_id, ticket_id)
        return self.dispatch.expire_first(self._live_ticket(ticket_id))

    def event(self, event: RecordingEvent) -> CaptureUpdate | SharedUpdate | None:
        update = self.capture.event(event)
        if update is not None:
            self.dispatch.on_capture(update)
            return update
        return self.shared.event(event)

    def redo(self, ticket_id: str) -> RetryStatus:
        return redo_ticket(self._live_ticket(ticket_id), self.dispatch)

    def seal(self, ticket_id: str, receipt_id: str) -> SignedPair:
        return seal_ticket(self._live_ticket(ticket_id), receipt_id, self.capture.receipts, self.publications)

    def get(self, artifact_id: str) -> SignedPair:
        return get_artifact(self.publications, artifact_id)

    def registrations(self, shift: str) -> tuple[RegistrationEntry, ...]:
        return self.ledger.for_shift(shift)

    def export(self, shift: str, registrations: Iterable[RegistrationEntry]) -> str:
        current_entries = []
        for entry in registrations:
            current = self.ledger.get(entry.ticket_id)
            if current is None or current.identity != entry.identity:
                raise ValueError(f'工单不匹配原登记 {entry.ticket_id}')
            current_entries.append(current)
        return export_shift(self.publications, shift, current_entries)


def _layout(segments: list[dict]) -> RecordingSpec:
    return RecordingSpec(tuple(SegmentSpec(segment['id'], index, len(segment['samples']))
                               for index, segment in enumerate(segments)))


def _write(station: Station, attempt: Attempt, segments: list[dict]) -> tuple[RecordingEvent, ...]:
    return tuple(event for position, segment in enumerate(segments)
                 for event in station.board.write_segment(attempt, position, segment['samples']))


def _deliver(station: Station, events: Iterable[RecordingEvent]) -> None:
    for event in events:
        station.event(station.board.deliver_event(event.reference))


def _seal_attempt(station: Station, attempt: Attempt) -> str:
    artifact, _ = station.seal(attempt.ticket_id, receipt_id_for(attempt))
    return artifact.artifact_id


def _replay_first(station: Station, recording: dict) -> str:
    segments = recording['segments']
    ticket = station.open(recording['shift'], recording['ticket_id'], recording['lane'], _layout(segments))
    events = _write(station, ticket.first, segments)
    order = recording['delivery_order']
    if sorted(order) != list(range(len(events))):
        raise ValueError('样例通知顺序必须恰好覆盖产生的通知')
    _deliver(station, (events[index] for index in order))
    return _seal_attempt(station, ticket.first)


def _replay_retry(station: Station, recording: dict) -> tuple[str, str]:
    shift = recording['shift']
    first_segments = recording['first']
    retry_segments = recording['retry']
    if _layout(first_segments) != _layout(retry_segments) or len(first_segments) < 2:
        raise ValueError('补拍演示需要两拍相同且至少两段的布局')
    ticket = station.open(shift, recording['ticket_id'], recording['lane'], _layout(first_segments))
    _deliver(station, station.board.write_segment(ticket.first, 0, first_segments[0]['samples']))
    station.expire_first(ticket.first.ticket_id)
    retry_status = station.redo(ticket.first.ticket_id)
    retry = ticket.retry_attempt
    if retry is None:
        raise RuntimeError('补拍未分配身份')

    other = recording['other']
    independent = station.open(shift, other['ticket_id'], other['lane'], _layout(other['segments']))
    _deliver(station, _write(station, independent.first, other['segments']))
    other_result = _seal_attempt(station, independent.first)

    retry_events = ()
    if retry_status is RetryStatus.SCHEDULED:
        retry_events = _write(station, retry, retry_segments)
    old_events = tuple(event for position, segment in enumerate(first_segments[1:], 1)
                       for event in station.board.write_segment(ticket.first, position, segment['samples']))
    if retry_status is RetryStatus.WAITING_FOR_AREA:
        _deliver(station, old_events)
        retry_events = _write(station, retry, retry_segments)
    _deliver(station, retry_events)
    result = _seal_attempt(station, retry)
    if retry_status is RetryStatus.SCHEDULED:
        _deliver(station, old_events)
    return result, other_result


def _replay_shared(station: Station, plan: dict) -> tuple[str, str]:
    segments = plan['segments']
    shared_id = plan['shared_id']
    opened = station.open_shared(plan['shift'], shared_id, plan['lane'], _layout(segments), plan['windows'])
    if opened == 'busy':
        raise RuntimeError('共录演示需要同道接收区')
    shared_id, tickets = opened
    left_id, right_id = plan['windows']
    left, right = tickets[left_id], tickets[right_id]

    first_events = [station.board.write_segment(shared_id, position, segments[position]['samples'])
                    for position in range(2)]
    for notices in first_events:
        _deliver(station, notices)
    if station.capture.receipts.for_attempt(left.first) is None:
        raise RuntimeError('先完成窗口的首拍并未实收')

    # 先签一窗；另一窗仍在采，同一加载的末段尚未实写。
    left_signed = _seal_attempt(station, left.first)
    if station.capture.receipts.for_attempt(right.first) is not None:
        raise RuntimeError('另一窗不应在末段实写前完成')

    tail_events = station.board.write_segment(shared_id, 2, segments[2]['samples'])
    _deliver(station, reversed(tail_events))
    right_signed = _seal_attempt(station, right.first)
    left_artifact = station.get(left_signed)[0]
    right_artifact = station.get(right_signed)[0]
    shared_positions = {segment.source_position for segment in left_artifact.segments}
    shared_positions &= {segment.source_position for segment in right_artifact.segments}
    if not shared_positions:
        raise RuntimeError('两窗签件没有共享的原段实写')
    for position in shared_positions:
        left_segment = next(s for s in left_artifact.segments if s.source_position == position)
        right_segment = next(s for s in right_artifact.segments if s.source_position == position)
        if left_segment.reference != right_segment.reference or left_segment.shared_id != right_segment.shared_id:
            raise RuntimeError('共享段未指向同一次实写')
    return left_signed, right_signed


def demo(sample_dir: Path) -> dict:
    first, retry, shift_plan, shared_plan = (
        json.loads((sample_dir / name).read_text(encoding='utf-8'))
        for name in ('first.json', 'retry.json', 'shift.json', 'shared.json')
    )
    with TemporaryDirectory(prefix='waveform-demo-') as directory:
        database = Path(directory) / 'station.sqlite3'
        with Station(database) as live:
            first_result = _replay_first(live, first)
            retry_results = _replay_retry(live, retry)
            pending = shift_plan['pending']
            segments = pending['segments']
            ticket = live.open(shift_plan['shift'], pending['ticket_id'], pending['lane'], _layout(segments))
            _deliver(live, live.board.write_segment(ticket.first, 0, segments[0]['samples']))
            # 共录最后登记，原班表 F01–F04 的登记序号保持不变。
            shared_results = _replay_shared(live, shared_plan)

        with Station(database) as offline:
            shift = shift_plan['shift']
            by_id = {entry.ticket_id: entry for entry in offline.registrations(shift)}
            selected = [by_id[ticket_id] for ticket_id in shift_plan['ticket_ids']]
            manifest = json.loads(offline.export(shift, selected))
            shared_shift = shared_plan['shift']
            shared_manifest = json.loads(
                offline.export(shared_shift, offline.registrations(shared_shift)))
            pairs = [offline.get(result_id) for result_id in (*shared_results, first_result, *retry_results)]
            return {'shift_manifest': manifest,
                    'shared_manifest': shared_manifest,
                    'publications': [{'artifact': asdict(artifact), 'receipt': asdict(receipt)}
                                     for artifact, receipt in pairs]}


def main() -> int:
    parser = argparse.ArgumentParser(description='恒川双测道波形采集站')
    commands = parser.add_subparsers(dest='command', required=True)
    demo_parser = commands.add_parser('demo', help='重放显式录制样例并交班')
    demo_parser.add_argument('--samples', type=Path, default=Path(__file__).with_name('samples'))
    init_parser = commands.add_parser('init', help='初始化或打开原站库')
    init_parser.add_argument('database', type=Path)
    args = parser.parse_args()
    if args.command == 'demo':
        print(json.dumps(demo(args.samples), ensure_ascii=False, indent=2))
    else:
        with Station(args.database):
            print(args.database)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
