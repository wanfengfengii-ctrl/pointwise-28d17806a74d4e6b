from collections.abc import Mapping
from dataclasses import dataclass, field

from capture.ownership import AreaOwnership
from capture.receipts import CaptureReceipt, ReceiptBook, receipt_id_for
from capture.segments import SegmentEvidence
from device.board import Board
from device.descriptors import DescriptorState, RecordingSpec, SegmentSpec
from device.events import EventKind, RecordingEvent
from device.receiver import ReceiveArea
from orders.ledger import RegistrationLedger
from orders.lifecycle import Ticket


@dataclass(frozen=True)
class WindowPlan:
    ticket_id: str
    start: int
    stop: int

    def contains(self, position: int) -> bool:
        return self.start <= position < self.stop


def validate_shared_input(
    shift: str, shared_id: str, lane: str, spec: RecordingSpec, windows: Mapping,
) -> tuple[WindowPlan, ...]:
    if not isinstance(shift, str) or not shift.strip():
        raise ValueError('班次不能为空')
    if not isinstance(shared_id, str) or not shared_id.strip():
        raise ValueError('共录号不能为空')
    if lane not in ('A', 'B'):
        raise ValueError('无效测道')
    if not isinstance(spec, RecordingSpec):
        raise ValueError('共录需要有效的非空录制布局')
    if not isinstance(windows, Mapping) or len(windows) != 2:
        raise ValueError('共录窗口须恰含两个工单号')
    plans: list[WindowPlan] = []
    for ticket_id, interval in windows.items():
        if not isinstance(ticket_id, str) or not ticket_id.strip():
            raise ValueError('共录工单号不能为空')
        if not isinstance(interval, (tuple, list)) or len(interval) != 2:
            raise ValueError('共录窗口须为半开区间')
        start, stop = interval
        if type(start) is not int or type(stop) is not int:
            raise ValueError('共录窗口端点须为整数')
        if not 0 <= start < stop <= len(spec.segments):
            raise ValueError('共录窗口须为非空布局区间')
        plans.append(WindowPlan(ticket_id, start, stop))
    first, second = plans
    if first.ticket_id == second.ticket_id:
        raise ValueError('共录窗口工单号不能重复')
    if max(first.start, second.start) >= min(first.stop, second.stop):
        raise ValueError('两个窗口须互相重叠')
    ordered = sorted(plans, key=lambda plan: plan.start)
    cursor = 0
    for plan in ordered:
        if plan.start > cursor:
            raise ValueError('两个窗口的并集须覆盖全布局')
        cursor = max(cursor, plan.stop)
    if cursor != len(spec.segments):
        raise ValueError('两个窗口的并集须覆盖全布局')
    return (first, second)


def window_layout(spec: RecordingSpec, plan: WindowPlan) -> RecordingSpec:
    return RecordingSpec(tuple(
        SegmentSpec(segment.segment_id, offset, segment.sample_count)
        for offset, segment in enumerate(spec.segments[plan.start:plan.stop])
    ))


@dataclass
class _Window:
    ticket: Ticket
    plan: WindowPlan
    layout: RecordingSpec
    evidence: dict[int, SegmentEvidence] = field(default_factory=dict)
    timed_out: bool = False
    receipt_done: bool = False

    @property
    def settled(self) -> bool:
        return self.receipt_done or self.timed_out

    def accepts(self, position: int) -> bool:
        # 超时窗口仍可积累迟到证据，供当前会话核对；只是保留签发资格给补拍。
        return not self.receipt_done and self.plan.contains(position)

    def is_complete(self) -> bool:
        return len(self.evidence) == len(self.layout.segments)


@dataclass
class _Shared:
    shared_id: str
    lane: str
    spec: RecordingSpec
    area: ReceiveArea
    windows: tuple[_Window, _Window]
    terminated: bool = False
    stop_requested: bool = False
    released: bool = False

    def all_settled(self) -> bool:
        return all(window.settled for window in self.windows)


@dataclass(frozen=True)
class SharedUpdate:
    shared_id: str
    receipts: tuple[CaptureReceipt, ...] = ()
    area_released: bool = False


class SharedCapture:
    """双工单共录：一次加载的真实实写由两个窗口各取所需，共用同一接收区。"""

    def __init__(
        self, board: Board, ownership: AreaOwnership, receipts: ReceiptBook,
        ledger: RegistrationLedger,
    ) -> None:
        self.board = board
        self.ownership = ownership
        self.receipts = receipts
        self.ledger = ledger
        self._live: dict[str, _Shared] = {}
        self.on_area_freed = lambda lane: None

    def has_free_area(self, lane: str) -> bool:
        return self.ownership.free_for(lane) is not None

    def start(
        self, shared_id: str, lane: str, spec: RecordingSpec,
        placements: tuple[tuple[Ticket, WindowPlan, RecordingSpec], ...],
    ) -> None:
        area = self.ownership.claim(shared_id, lane=lane)
        if area is None:
            raise RuntimeError('共录启动时同道已无接收区')
        self.board.accept(shared_id, spec, area, lane=lane)
        windows = tuple(_Window(ticket, plan, layout) for ticket, plan, layout in placements)
        self._live[shared_id] = _Shared(shared_id, lane, spec, area, windows)

    def _find_window(self, state: _Shared, ticket_id: str) -> _Window | None:
        for window in state.windows:
            if window.ticket.first.ticket_id == ticket_id:
                return window
        return None

    def expire_first(self, shared_id: str, ticket_id: str) -> bool:
        state = self._live.get(shared_id)
        if state is None or state.released:
            return False
        window = self._find_window(state, ticket_id)
        if window is None or not window.ticket.expire_first(self.ledger):
            return False
        window.timed_out = True
        self._advance(state, ())
        return True

    def event(self, event: RecordingEvent) -> SharedUpdate | None:
        if not isinstance(event, RecordingEvent) or not isinstance(event.attempt, str):
            return None
        if not self.board.issued(event):
            return None
        state = self._live.get(event.attempt)
        if state is None or state.released:
            return None
        if (event.area_id != state.area.area_id
                or self.ownership.holder(state.area.area_id) != state.shared_id):
            return None
        if event.kind is EventKind.SEGMENT_WRITTEN:
            return self._segment(state, event)
        if event.kind in (EventKind.COMPLETED, EventKind.STOP_CONFIRMED):
            if state.terminated:
                return None
            state.terminated = True
            return self._advance(state, ())
        return None

    def _segment(self, state: _Shared, event: RecordingEvent) -> SharedUpdate | None:
        position = event.position
        if type(position) is not int or not 0 <= position < len(state.spec.segments):
            return None
        segment = state.spec.segment_at(position)
        start, stop = state.spec.interval(position)
        if (event.segment_id != segment.segment_id or type(event.start) is not int
                or type(event.stop) is not int or (event.start, event.stop) != (start, stop)
                or type(event.reference) is not int or event.reference <= 0):
            return None
        receipts: list[CaptureReceipt] = []
        progressed = False
        for window in state.windows:
            if not window.accepts(position):
                continue
            offset = position - window.plan.start
            if offset in window.evidence:
                continue
            window_start, window_stop = window.layout.interval(offset)
            window.evidence[offset] = SegmentEvidence(
                window.ticket.first, state.area.area_id, segment.segment_id, offset,
                window_start, window_stop, event.reference,
                shared_id=state.shared_id, source_position=position,
            )
            progressed = True
            if window.is_complete():
                receipts.append(self._take_over(state, window))
        if not progressed:
            return None
        return self._advance(state, tuple(receipts))

    def _take_over(self, state: _Shared, window: _Window) -> CaptureReceipt:
        segments = tuple(window.evidence[offset] for offset in range(len(window.layout.segments)))
        samples = tuple(
            value
            for position in range(window.plan.start, window.plan.stop)
            for value in state.area.read_segment(*state.spec.interval(position))
        )
        receipt = CaptureReceipt(receipt_id_for(window.ticket.first), window.ticket.first, samples, segments)
        self.receipts.put(receipt)
        window.receipt_done = True
        window.ticket.accept_handoff(receipt)
        return receipt

    def _advance(self, state: _Shared, receipts: tuple[CaptureReceipt, ...]) -> SharedUpdate:
        if state.all_settled() and not state.stop_requested and not state.released:
            if not state.terminated and self.board.state(state.shared_id) is DescriptorState.WRITING:
                state.stop_requested = self.board.request_stop(state.shared_id)
        released = False
        if state.terminated and state.all_settled() and not state.released:
            self.ownership.release(state.area, state.shared_id, self.board.state(state.shared_id))
            state.released = True
            released = True
            self.on_area_freed(state.lane)
        return SharedUpdate(state.shared_id, receipts, released)
