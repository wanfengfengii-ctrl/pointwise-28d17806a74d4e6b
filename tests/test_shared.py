import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from capture.receipts import receipt_id_for
from device.descriptors import DescriptorState, RecordingSpec, SegmentSpec
from device.trace import TraceAction
from handover.lookup import MissingArtifact
from orders.eligibility import IneligibleReceipt
from orders.lifecycle import TicketState
from orders.retry import RetryStatus
from publication.store import PublicationIntegrityError
from station import Station
from tests.recording import capture, confirm, deliver

SEGMENTS = (('head', (7, 3)), ('overlap', (11,)), ('tail', (-17,)))
LAYOUT = RecordingSpec(tuple(SegmentSpec(name, position, len(samples))
                            for position, (name, samples) in enumerate(SEGMENTS)))
WINDOWS = {'X': (0, 2), 'Y': (1, 3)}


def open_shared(station, windows=WINDOWS, shared_id='load-41', lane='A', shift='day', layout=LAYOUT):
    opened = station.open_shared(shift, shared_id, lane, layout, windows)
    if opened == 'busy':
        raise AssertionError('共录未能取得接收区')
    return opened


def issue(station, shared_id, position):
    return station.board.write_segment(shared_id, position, SEGMENTS[position][1])


def deliver_shared(station, *notifications):
    return tuple(station.event(station.board.deliver_event(notice.reference))
                 for notice in notifications)


class SharedCaptureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.station = Station(':memory:', areas_per_lane=1)
        self.addCleanup(self.station.close)

    # --- 登记与输入 ---
    def test_open_registers_in_window_order(self):
        shared_id, tickets = open_shared(self.station)
        self.assertEqual(shared_id, 'load-41')
        self.assertEqual(list(tickets), ['X', 'Y'])
        self.assertEqual([entry.ticket_id for entry in self.station.registrations('day')], ['X', 'Y'])
        self.assertEqual((tickets['X'].state, tickets['Y'].state),
                         (TicketState.FIRST_PENDING, TicketState.FIRST_PENDING))
        self.assertEqual([(s.segment_id, s.sample_count) for s in tickets['X'].spec.segments],
                         [('head', 2), ('overlap', 1)])
        self.assertEqual([(s.segment_id, s.sample_count) for s in tickets['Y'].spec.segments],
                         [('overlap', 1), ('tail', 1)])
        # 登记顺序沿 windows 迭代顺序，即使键排序不同。
        other = Station(':memory:')
        self.addCleanup(other.close)
        _, ordered = other.open_shared('day', 'l2', 'A', LAYOUT, {'Y': (1, 3), 'X': (0, 2)})
        self.assertEqual(list(ordered), ['Y', 'X'])
        self.assertEqual([e.ticket_id for e in other.registrations('day')], ['Y', 'X'])

    def test_invalid_input_rejected_without_registration(self):
        station = self.station
        explicit = [
            ('day', '', 'A', LAYOUT, WINDOWS),
            ('day', 's', 'A', LAYOUT, {'X': (0, 1), 'Y': (1, 3)}),
            ('day', 's', 'A', LAYOUT, {'X': (0, 2), 'Y': (2, 3)}),
            ('day', 's', 'A', LAYOUT, {'X': (0, 3), 'Y': (2, 4)}),
            ('day', 's', 'A', LAYOUT, {'X': (0, 3)}),
            ('day', 's', 'A', LAYOUT, {'X': (0, 2)}),
            ('day', 's', 'C', LAYOUT, WINDOWS),
            ('day', 's', 'A', LAYOUT, {'X': (2, 0), 'Y': (1, 3)}),
            ('day', 's', 'A', LAYOUT, {'X': (0, 2), 'Y': (1.0, 3)}),
            (' ', 's', 'A', LAYOUT, WINDOWS),
        ]
        for args in explicit:
            with self.subTest(args=args), self.assertRaises(ValueError):
                station.open_shared(*args)
        self.assertEqual(station.registrations('day'), ())
        self.assertIsNone(station.ledger.get('X'))

    def test_duplicate_shared_and_ticket_rejected(self):
        station = self.station
        open_shared(station)
        with self.assertRaises(ValueError):
            station.open_shared('day', 'load-41', 'A', LAYOUT, {'P': (0, 2), 'Q': (1, 3)})
        with self.assertRaises(ValueError):
            station.open_shared('day', 'other', 'A', LAYOUT, {'X2': (0, 2), 'X': (1, 3)})
        self.assertEqual([e.ticket_id for e in station.registrations('day')], ['X', 'Y'])

    def test_busy_when_lane_full(self):
        station = self.station
        ticket = station.open('day', 'blocker', 'A', LAYOUT)
        self.assertEqual(station.open_shared('day', 'load-41', 'A', LAYOUT, WINDOWS), 'busy')
        self.assertEqual([e.ticket_id for e in station.registrations('day')], ['blocker'])
        self.assertNotIn('load-41', station._shared_of.values())
        # 另一道仍有空区可共录。
        shared_id, _ = open_shared(station, lane='B', shared_id='b-load')
        self.assertEqual(shared_id, 'b-load')
        # 不用旧 ticket 干扰，避免未使用变量告警。
        self.assertIsNotNone(ticket)

    # --- 一次触发只占一道一个区，每段只实写一次 ---
    def test_single_area_and_single_write(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        accepted = [e for e in station.board.trace.entries() if e.action is TraceAction.ACCEPTED]
        self.assertEqual([(e.attempt, e.area_id) for e in accepted], [(shared_id, 'A:0')])
        issue(station, shared_id, 0)
        with self.assertRaises(ValueError):
            issue(station, shared_id, 0)

    # --- 实写证据齐备即可独立签发 ---
    def test_each_window_signs_independently(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        overlap = issue(station, shared_id, 1)
        deliver_shared(station, *head, *overlap)
        x_receipt = station.capture.receipts.for_attempt(tickets['X'].first)
        self.assertIsNotNone(x_receipt)
        self.assertIsNone(station.capture.receipts.for_attempt(tickets['Y'].first))
        artifact, success = station.seal('X', receipt_id_for(tickets['X'].first))
        self.assertEqual((artifact.samples, artifact.peak), ((7, 3, 11), 11))
        self.assertEqual([(s.position, s.start, s.stop, s.source_position) for s in artifact.segments],
                         [(0, 0, 2, 0), (1, 2, 3, 1)])
        self.assertEqual(success.receipt_id, 'success:X')
        tail = issue(station, shared_id, 2)
        deliver_shared(station, *reversed(tail))
        y_artifact, _ = station.seal('Y', receipt_id_for(tickets['Y'].first))
        self.assertEqual((y_artifact.samples, y_artifact.peak), ((11, -17), 17))
        # 共享段指向同一实写引用。
        self.assertEqual(artifact.segments[1].reference, y_artifact.segments[0].reference)
        self.assertEqual((artifact.segments[1].shared_id, y_artifact.segments[0].shared_id), ('load-41', 'load-41'))
        self.assertEqual((artifact.segments[1].source_position, y_artifact.segments[0].source_position), (1, 1))

    # --- 先超时者失格，只补拍本窗 ---
    def test_timeout_before_first_complete_loses_sign_eligibility(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        overlap = issue(station, shared_id, 1)
        deliver_shared(station, *head)  # 只投递首段通知，X、Y 均欠证
        self.assertTrue(station.expire_first('X'))
        self.assertIs(tickets['X'].state, TicketState.FIRST_EXPIRED)
        self.assertTrue(station.ledger.get('X').first_timed_out)
        # 超时成立后迟到的完整实收仍可交接，但无签发资格；Y 仍是有效窗。
        deliver_shared(station, *overlap)
        late = station.capture.receipts.for_attempt(tickets['X'].first)
        self.assertIsNotNone(late)
        self.assertEqual(late.samples, (7, 3, 11))
        with self.assertRaises(IneligibleReceipt):
            station.seal('X', late.receipt_id)
        self.assertIsNone(station.publications.get_for_ticket('X'))
        self.assertIs(station.redo('X'), RetryStatus.WAITING_FOR_AREA)
        self.assertIsNotNone(tickets['X'].retry_attempt)
        # 仍有有效窗 Y 欠证，不得请求停写。
        self.assertIs(station.board.state(shared_id), DescriptorState.WRITING)
        tail = issue(station, shared_id, 2)
        deliver_shared(station, *tail)
        y_receipt = station.capture.receipts.for_attempt(tickets['Y'].first)
        self.assertIsNotNone(y_receipt)
        y_artifact, _ = station.seal('Y', y_receipt.receipt_id)
        self.assertEqual((y_artifact.samples, y_artifact.peak), ((11, -17), 17))

    # --- 全部窗口接管后请求停写，真实终止后归还 ---
    def test_stop_requested_when_all_settled_then_release_after_terminal(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        deliver_shared(station, *head)
        station.expire_first('X')
        station.expire_first('Y')
        # 两窗均超时，无有效窗欠证，请求停写。
        self.assertIs(station.board.state(shared_id), DescriptorState.STOP_PENDING)
        self.assertIs(station.redo('X'), RetryStatus.WAITING_FOR_AREA)
        self.assertIs(station.redo('Y'), RetryStatus.WAITING_FOR_AREA)
        stop = confirm(station, shared_id)
        self.assertIs(station.board.state(shared_id), DescriptorState.STOPPED)
        update, = deliver_shared(station, stop)
        self.assertTrue(update.area_released)
        with self.assertRaises(ValueError):
            station.board.write_segment(shared_id, 1, SEGMENTS[1][1])

    # --- 停源后按成果号领取，共享段指向同一实写 ---
    def test_reopen_collects_shared_results_and_manifest(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'shared.sqlite3'
            with Station(database, areas_per_lane=1) as live:
                shared_id, tickets = open_shared(live)
                head = issue(live, shared_id, 0)
                overlap = issue(live, shared_id, 1)
                deliver_shared(live, *head, *overlap)
                x_artifact, x_receipt = live.seal('X', receipt_id_for(tickets['X'].first))
                tail = issue(live, shared_id, 2)
                deliver_shared(live, *reversed(tail))
                y_artifact, y_receipt = live.seal('Y', receipt_id_for(tickets['Y'].first))
                registrations = live.registrations('day')
            with Station(database) as offline:
                self.assertEqual(offline.board.trace.entries(), ())
                collected_x = offline.get('result:X')
                collected_y = offline.get('result:Y')
                self.assertEqual(collected_x, (x_artifact, x_receipt))
                self.assertEqual(collected_y, (y_artifact, y_receipt))
                with self.assertRaises(MissingArtifact):
                    offline.get('result:unknown')
                manifest = json.loads(offline.export('day', registrations))
                self.assertEqual([(row['ticket_id'], row['status'], row['attempt'])
                                  for row in manifest['rows']],
                                 [('X', 'signed', 1), ('Y', 'signed', 1)])
                self.assertEqual([s.shared_id for s in collected_y[0].segments], ['load-41', 'load-41'])
                self.assertEqual([(s.start, s.stop) for s in collected_y[0].segments], [(0, 1), (1, 2)])

    # --- 迟到、重复、错源通知不复活旧窗，也不影响复用后的录制 ---
    def test_stale_and_wrong_source_notifications(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        overlap = issue(station, shared_id, 1)
        deliver_shared(station, *head, *overlap)
        late_head = head[0]
        self.assertIsNone(station.event(station.board.deliver_event(late_head.reference)))
        # 错源：把首段通知改成别的共录号、错接收区；均无进展。
        self.assertIsNone(station.event(replace(overlap[0], attempt='other-load')))
        self.assertIsNone(station.event(replace(head[0], area_id='A:1')))
        self.assertIsNotNone(station.capture.receipts.for_attempt(tickets['X'].first))
        self.assertIsNone(station.capture.receipts.for_attempt(tickets['Y'].first))

    def test_stale_notifications_do_not_affect_reused_area(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        deliver_shared(station, *head)
        station.expire_first('X')
        station.expire_first('Y')
        self.assertIs(station.redo('X'), RetryStatus.WAITING_FOR_AREA)
        self.assertIs(station.redo('Y'), RetryStatus.WAITING_FOR_AREA)
        stop = confirm(station, shared_id)
        deliver_shared(station, stop)
        self.assertTrue(station.shared._live[shared_id].released)
        # 补拍接管复用后同一接收区，录自己的窗口。
        retry = tickets['X'].retry_attempt
        self.assertIsNotNone(retry)
        retry_events = station.board.write_segment(retry, 0, SEGMENTS[0][1])
        retry_events += station.board.write_segment(retry, 1, SEGMENTS[1][1])
        deliver(station, retry_events)
        # 旧共录通知重投：不复活旧窗，也不污染补拍实收。
        self.assertIsNone(station.event(station.board.deliver_event(head[0].reference)))
        self.assertIsNone(station.event(station.board.deliver_event(stop.reference)))
        receipt = station.capture.receipts.for_attempt(retry)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.samples, (7, 3, 11))
        artifact, _ = station.seal('X', receipt.receipt_id)
        self.assertEqual([s.shared_id for s in artifact.segments], [None, None])
        self.assertEqual([s.source_position for s in artifact.segments], [None, None])

    # --- 一次实写通知可同时使两窗齐证 ---
    def test_shared_notice_can_prove_both_windows(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        overlap = issue(station, shared_id, 1)
        tail = issue(station, shared_id, 2)
        # 公共段通知放在自然终止和两条独占段通知之后。
        deliver_shared(station, *head, *reversed(tail))
        self.assertIsNone(station.capture.receipts.for_attempt(tickets['X'].first))
        self.assertIsNone(station.capture.receipts.for_attempt(tickets['Y'].first))
        deliver_shared(station, *overlap)
        self.assertIsNotNone(station.capture.receipts.for_attempt(tickets['X'].first))
        self.assertIsNotNone(station.capture.receipts.for_attempt(tickets['Y'].first))

    # --- 签发失败不留签件或首执，同实收续签 ---
    def test_seal_failure_keeps_receipt_for_retry(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        overlap = issue(station, shared_id, 1)
        deliver_shared(station, *head, *overlap)
        receipt = station.capture.receipts.for_attempt(tickets['X'].first)
        station.connection.executescript('''
            CREATE TEMP TRIGGER reject_shared_receipt BEFORE INSERT ON success_receipts
            BEGIN SELECT RAISE(ABORT, 'receipt insert blocked'); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt insert blocked'):
            station.seal('X', receipt.receipt_id)
        self.assertEqual(station.connection.execute('SELECT count(*) FROM artifacts').fetchone(), (0,))
        self.assertEqual(station.connection.execute('SELECT count(*) FROM success_receipts').fetchone(), (0,))
        self.assertIs(tickets['X'].state, TicketState.FIRST_READY)
        self.assertEqual(station.capture.receipts.get(receipt.receipt_id), receipt)
        station.connection.execute('DROP TRIGGER reject_shared_receipt')
        pair = station.seal('X', receipt.receipt_id)
        self.assertEqual(pair[0].samples, (7, 3, 11))
        self.assertEqual(station.get(pair[0].artifact_id), pair)

    # --- 抓拍窗口接管后不接受首拍超时 ---
    def test_taken_over_window_rejects_timeout(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        overlap = issue(station, shared_id, 1)
        deliver_shared(station, *head, *overlap)
        self.assertIs(tickets['X'].state, TicketState.FIRST_READY)
        self.assertFalse(station.expire_first('X'))
        self.assertFalse(station.ledger.get('X').first_timed_out)
        with self.assertRaises(ValueError):
            station.redo('X')

    # --- 物理终止先于段通知，且接收区须等证据齐备才复用 ---
    def test_terminal_before_segment_notices_waits_for_evidence(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        overlap = issue(station, shared_id, 1)
        tail = issue(station, shared_id, 2)
        self.assertIs(station.board.state(shared_id), DescriptorState.COMPLETE)
        # 先投递物理终止，再投递仍缺的段通知。
        deliver_shared(station, tail[1])
        self.assertIsNone(station.capture.receipts.for_attempt(tickets['Y'].first))
        self.assertFalse(station.shared._live[shared_id].released)
        # 补齐末段通知：Y 仍欠公共段，故既无实收也不能复用区。
        deliver_shared(station, tail[0])
        self.assertIsNone(station.capture.receipts.for_attempt(tickets['Y'].first))
        self.assertFalse(station.shared._live[shared_id].released)
        self.assertIs(station.board.state(shared_id), DescriptorState.COMPLETE)
        # 公共段通知补足 Y；X 仍欠首段，故仅 Y 接管。
        deliver_shared(station, *overlap)
        self.assertIsNotNone(station.capture.receipts.for_attempt(tickets['Y'].first))
        self.assertIsNone(station.capture.receipts.for_attempt(tickets['X'].first))
        # 首段通知补齐 X，两窗均了结后接收区复用。
        self.assertFalse(station.shared._live[shared_id].released)
        deliver_shared(station, *head)
        self.assertIsNotNone(station.capture.receipts.for_attempt(tickets['X'].first))
        self.assertTrue(station.shared._live[shared_id].released)
        # 多余的首段通知为重复，不影响既有实收。
        self.assertIsNone(station.event(station.board.deliver_event(head[0].reference)))

    # --- 仍有有效窗时不得请求停写 ---
    def test_no_stop_while_valid_window_remains(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        deliver_shared(station, *head)
        self.assertTrue(station.expire_first('X'))
        self.assertIs(station.board.state(shared_id), DescriptorState.WRITING)
        self.assertFalse(station.shared._live[shared_id].stop_requested)
        # Y 是有效窗，即便中途超时但尚欠证，也仍可继续采；X 的补拍等区。
        self.assertIs(station.redo('X'), RetryStatus.WAITING_FOR_AREA)
        self.assertIs(station.board.state(shared_id), DescriptorState.WRITING)
        self.assertFalse(station.board.has_attempt(tickets['X'].retry_attempt))
        # Y 的证据补齐后，两窗均了结才请求停写（此处自然完成，无需停写）。
        overlap = issue(station, shared_id, 1)
        tail = issue(station, shared_id, 2)
        deliver_shared(station, *overlap, *reversed(tail))
        self.assertIsNotNone(station.capture.receipts.for_attempt(tickets['Y'].first))
        self.assertIs(station.board.state(shared_id), DescriptorState.COMPLETE)
        self.assertFalse(station.shared._live[shared_id].stop_requested)

    # --- 同道补拍等区，另一道照常推进 ---
    def test_other_lane_progress_while_shared_holds_area(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        head = issue(station, shared_id, 0)
        deliver_shared(station, *head)
        station.expire_first('X')
        station.expire_first('Y')
        self.assertIs(station.redo('X'), RetryStatus.WAITING_FOR_AREA)
        blocker = station.open('day', 'blocker', 'A', LAYOUT)
        self.assertIs(blocker.state, TicketState.FIRST_PENDING)
        self.assertFalse(station.board.has_attempt(blocker.first))
        other = station.open('day', 'other', 'B', LAYOUT)
        receipt = capture(station, other.first, (-5, 3), (4,), (-2,))
        artifact, _ = station.seal('other', receipt.receipt_id)
        self.assertEqual((artifact.samples, artifact.peak), ((-5, 3, 4, -2), 5))
        # 另一道独立签发时，共录仍在等待真实停写确认，接收区未复用。
        self.assertIs(station.board.state(shared_id), DescriptorState.STOP_PENDING)
        self.assertFalse(station.shared._live[shared_id].released)
        # 真实终止通知结清后，A 道空区可让补拍开始。
        deliver_shared(station, confirm(station, shared_id))
        self.assertIs(tickets['X'].state, TicketState.RETRY_PENDING)
        self.assertIs(station.board.state(shared_id), DescriptorState.STOPPED)


    # --- 交班核验能识别被篡改的共享出处 ---
    def test_export_rejects_tampered_shared_provenance(self):
        station = self.station
        shared_id, tickets = open_shared(station)
        for position in range(3):
            deliver_shared(station, *issue(station, shared_id, position))
        ticket = tickets['X']
        station.seal('X', receipt_id_for(ticket.first))
        registrations = station.registrations('day')
        db = station.connection
        saved, = db.execute('SELECT body FROM artifacts WHERE artifact_id = ?',
                            ('result:X',)).fetchone()
        for edit in ('source_position', 'shared_id'):
            tampered = json.loads(saved)
            target = next(s for s in tampered['segments'] if s['shared_id'] is not None)
            target[edit] = 5 if edit == 'source_position' else None
            if edit == 'shared_id':
                target['source_position'] = None
            with self.subTest(edit=edit):
                db.execute('UPDATE artifacts SET body = ? WHERE artifact_id = ?',
                           (json.dumps(tampered), 'result:X'))
                with self.assertRaises(PublicationIntegrityError):
                    station.export('day', registrations)


class SharedDemoTest(unittest.TestCase):
    def test_demo_shared_handover(self):
        from station import demo
        result = demo(Path(__file__).resolve().parent.parent / 'samples')
        shared = {row['ticket_id']: row for row in result['shared_manifest']['rows']}
        self.assertEqual((shared['F05']['status'], shared['F06']['status']), ('signed', 'signed'))
        publications = {pair['artifact']['artifact_id']: pair['artifact'] for pair in result['publications']}
        x = publications['result:F05']
        y = publications['result:F06']
        self.assertEqual((tuple(x['samples']), x['peak']), ((7, 3, 11), 11))
        self.assertEqual((tuple(y['samples']), y['peak']), ((11, -17), 17))
        shared_x = next(s for s in x['segments'] if s['source_position'] == 1)
        shared_y = next(s for s in y['segments'] if s['source_position'] == 1)
        self.assertEqual(shared_x['reference'], shared_y['reference'])
        self.assertEqual((shared_x['start'], shared_x['stop']), (2, 3))
        self.assertEqual((shared_y['start'], shared_y['stop']), (0, 1))
        # 原班表回归仍在。
        self.assertEqual([row['ticket_id'] for row in result['shift_manifest']['rows']],
                         ['F01', 'F02', 'F03', 'F04'])


if __name__ == '__main__':
    unittest.main()
