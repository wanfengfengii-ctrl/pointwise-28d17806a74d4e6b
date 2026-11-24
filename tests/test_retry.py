import json

from capture.receipts import receipt_id_for
from device.descriptors import Attempt, DescriptorState
from device.events import EventKind
from device.trace import TraceAction
from orders.eligibility import IneligibleReceipt
from orders.lifecycle import TicketState
from orders.retry import RetryStatus
from station import Station
from tests.recording import StationTest, capture, confirm, deliver, open_two, scenario, write


def expired_order(station):
    ticket = open_two(station, 'retry')
    deliver(station, station.board.write_segment(ticket.first, 0, (7,)))
    if not station.expire_first('retry'):
        raise AssertionError('首拍超时应先成立')
    return ticket, station.redo('retry')


class RetryTests(StationTest):
    def test_late_first_tail(self):
        station = self.station
        ticket, status = expired_order(station)
        replacement = ticket.retry_attempt
        self.assertEqual(replacement, Attempt('retry', 'A', 2))
        self.assertTrue(station.ledger.get('retry').first_timed_out)
        if status is RetryStatus.SCHEDULED:
            replacement_events = write(station, replacement, (11,), (13,))
            old_tail = station.board.write_segment(ticket.first, 1, (9,))
            deliver(station, replacement_events)
        else:
            self.assertIs(status, RetryStatus.WAITING_FOR_AREA)
            self.assertFalse(station.board.has_attempt(replacement))
            old_tail = station.board.write_segment(ticket.first, 1, (9,))
            deliver(station, old_tail)
            replacement_events = write(station, replacement, (11,), (13,))
            deliver(station, replacement_events)
        artifact, success = station.seal('retry', receipt_id_for(replacement))
        self.assertEqual(artifact.samples, (11, 13), 'retry-waveform-origin')
        self.assertEqual(artifact.peak, 13)
        self.assertEqual(artifact.attempt, replacement)
        expected_references = [e.reference for e in replacement_events if e.kind is EventKind.SEGMENT_WRITTEN]
        self.assertEqual([(s.attempt, s.segment_id, s.start, s.stop, s.reference) for s in artifact.segments],
                         [(replacement, 'head', 0, 1, expected_references[0]),
                          (replacement, 'tail', 1, 2, expected_references[1])])
        if status is RetryStatus.SCHEDULED:
            self.assertNotEqual(old_tail[0].area_id, replacement_events[0].area_id)
        deliver(station, old_tail)
        self.assertEqual(station.get(artifact.artifact_id), (artifact, success))
        self.assertEqual(station.connection.execute('SELECT count(*) FROM success_receipts').fetchone(), (1,))
        row, = json.loads(station.export('day', station.registrations('day')))['rows']
        self.assertEqual((row['attempt'], row['peak'], row['receipt_id'], row['first_timed_out']),
                         (2, 13, 'success:retry', True))
        scenario('v3', inputs={'first': [7, 9], 'retry': [11, 13], 'dispatch': status.value},
                 expected={'samples': [11, 13], 'peak': 13, 'attempt': 2},
                 actual={'samples': artifact.samples, 'peak': artifact.peak, 'attempt': artifact.attempt.number},
                 downstream_expected={'attempt': 2, 'peak': 13, 'receipt_id': 'success:retry', 'first_timed_out': True},
                 downstream_actual={key: row[key] for key in ('attempt', 'peak', 'receipt_id', 'first_timed_out')},
                 code_paths=['device/board.py', 'capture/session.py', 'orders/eligibility.py', 'publication/seal.py',
                             'handover/export.py'], oracle='补拍实写两点为 11、13。首拍超时先成立。')

    def test_late_first_ineligible(self):
        station = self.station
        ticket, _ = expired_order(station)
        deliver(station, station.board.write_segment(ticket.first, 1, (9,)))
        late = station.capture.receipts.for_attempt(ticket.first)
        self.assertIsNotNone(late)
        self.assertEqual(late.samples, (7, 9))
        with self.assertRaises(IneligibleReceipt, msg='expired-first-must-not-sign'):
            station.seal('retry', late.receipt_id)
        self.assertIsNone(station.publications.get_for_ticket('retry'))
        retry_receipt = capture(station, ticket.retry_attempt, (11,), (13,))
        with self.assertRaises(IneligibleReceipt):
            station.seal('retry', late.receipt_id)
        artifact, success = station.seal('retry', retry_receipt.receipt_id)
        self.assertEqual((artifact.attempt.number, artifact.samples, success.attempt.number), (2, (11, 13), 2))
        self.assertTrue(station.ledger.get('retry').first_timed_out)

    def test_physical_completion_before_timeout_with_delayed_handoff(self):
        station = self.station
        ticket = open_two(station, 'delayed')
        notices = write(station, ticket.first, (7,), (9,))
        self.assertIs(station.board.state(ticket.first), DescriptorState.COMPLETE)
        self.assertIs(ticket.state, TicketState.FIRST_PENDING)
        self.assertTrue(station.expire_first('delayed'))
        deliver(station, reversed(notices))
        first_receipt = station.capture.receipts.for_attempt(ticket.first)
        self.assertIsNotNone(first_receipt)
        with self.assertRaises(IneligibleReceipt):
            station.seal('delayed', first_receipt.receipt_id)
        self.assertIs(station.redo('delayed'), RetryStatus.SCHEDULED)
        receipt = capture(station, ticket.retry_attempt, (11,), (13,))
        artifact, _ = station.seal('delayed', receipt.receipt_id)
        self.assertEqual((artifact.attempt.number, artifact.samples), (2, (11, 13)))

    def test_write_right(self):
        with Station(':memory:', areas_per_lane=1) as station:
            ticket, status = expired_order(station)
            self.assertIs(status, RetryStatus.WAITING_FOR_AREA)
            replacement = ticket.retry_attempt
            self.assertIs(station.board.state(ticket.first), DescriptorState.STOP_PENDING)
            self.assertFalse(station.board.has_attempt(replacement))
            stop = confirm(station, ticket.first)
            self.assertFalse(station.board.has_attempt(replacement))
            with self.assertRaises(ValueError):
                station.board.write_segment(ticket.first, 1, (9,))
            deliver(station, (stop,))
            self.assertTrue(station.board.has_attempt(replacement))
            accepted = [entry for entry in station.board.trace.entries() if entry.action is TraceAction.ACCEPTED]
            self.assertEqual([(entry.attempt, entry.area_id) for entry in accepted],
                             [(ticket.first, stop.area_id), (replacement, stop.area_id)])
            self.assertEqual(deliver(station, (stop,)), (None,))
            receipt = capture(station, replacement, (11,), (13,))
            artifact, _ = station.seal('retry', receipt.receipt_id)
            self.assertEqual(artifact.samples, (11, 13))

    def test_other_lane_progress(self):
        with Station(':memory:', areas_per_lane=1) as station:
            ticket, status = expired_order(station)
            self.assertIs(status, RetryStatus.WAITING_FOR_AREA)
            other = open_two(station, 'other', 'B')
            receipt = capture(station, other.first, (-5,), (3,))
            pair = station.seal('other', receipt.receipt_id)
            self.assertEqual(station.get(pair[0].artifact_id), pair)
            self.assertEqual((pair[0].samples, pair[0].peak), ((-5, 3), 5))
            self.assertIs(station.board.state(ticket.first), DescriptorState.STOP_PENDING)
            self.assertIs(ticket.state, TicketState.RETRY_RESERVED)
            self.assertIsNone(station.publications.get_for_ticket('retry'))
            deliver(station, (confirm(station, ticket.first),))
            self.assertIs(ticket.state, TicketState.RETRY_PENDING)

    def test_duplicate_retry(self):
        station = self.station
        ticket, status = expired_order(station)
        if status is RetryStatus.WAITING_FOR_AREA:
            deliver(station, (confirm(station, ticket.first),))
        self.assertIs(ticket.state, TicketState.RETRY_PENDING)
        with self.assertRaises(ValueError):
            station.redo('retry')
        receipt = capture(station, ticket.retry_attempt, (11,), (13,))
        station.seal('retry', receipt.receipt_id)
        with self.assertRaises(ValueError):
            station.redo('retry')
        self.assertEqual([entry.attempt.number for entry in station.board.trace.entries()
                          if entry.action is TraceAction.ACCEPTED], [1, 2])

    def test_pending_repeat(self):
        with Station(':memory:', areas_per_lane=1) as station:
            ticket, status = expired_order(station)
            self.assertIs(status, RetryStatus.WAITING_FOR_AREA)
            reserved = ticket.retry_attempt
            before = station.board.trace.entries()
            with self.assertRaises(ValueError):
                station.redo('retry')
            self.assertEqual(station.board.trace.entries(), before)
            self.assertEqual(ticket.retry_attempt, reserved)
            deliver(station, (confirm(station, ticket.first),))
            self.assertEqual([entry.attempt for entry in station.board.trace.entries()
                              if entry.action is TraceAction.ACCEPTED], [ticket.first, reserved])
