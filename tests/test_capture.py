from dataclasses import replace

from capture.receipts import receipt_id_for
from device.descriptors import Attempt, RecordingSpec, SegmentSpec
from handover.lookup import MissingArtifact
from orders.ledger import DuplicateTicket
from orders.lifecycle import TicketState
from station import Station
from tests.recording import StationTest, capture, confirm, deliver, open_two, scenario, write


class CaptureTests(StationTest):
    def test_first_peak(self):
        ticket = open_two(self.station, 'first')
        events = write(self.station, ticket.first, (-7,), (4,))
        deliver(self.station, events)
        receipt = self.station.capture.receipts.for_attempt(ticket.first)
        self.assertIsNotNone(receipt)
        pair = self.station.seal('first', receipt.receipt_id)
        artifact, success = pair
        self.assertEqual(artifact.samples, (-7, 4))
        self.assertEqual(artifact.peak, 7)
        self.assertEqual(artifact.attempt, Attempt('first', 'A', 1))
        self.assertEqual([(s.segment_id, s.start, s.stop, s.reference) for s in artifact.segments],
                         [('head', 0, 1, events[0].reference), ('tail', 1, 2, events[1].reference)])
        self.assertEqual(success.artifact_id, artifact.artifact_id)
        self.assertEqual(ticket.state, TicketState.SIGNED)
        collected, collected_receipt = self.station.get(artifact.artifact_id)
        self.assertEqual((collected, collected_receipt), pair)
        scenario('v1', inputs={'head': [-7], 'tail': [4]}, expected={'samples': [-7, 4], 'peak': 7},
                 actual={'samples': artifact.samples, 'peak': artifact.peak},
                 downstream_expected={'result': 'result:first', 'receipt': 'success:first', 'peak': 7},
                 downstream_actual={'result': collected.artifact_id, 'receipt': collected_receipt.receipt_id,
                                    'peak': collected.peak},
                 code_paths=['capture/receipts.py', 'publication/metrics.py', 'publication/seal.py', 'handover/lookup.py'],
                 oracle='布局先 head 后 tail。两点绝对值分别为 7 和 4。')

    def test_notice_order(self):
        ticket = open_two(self.station, 'reversed')
        head, tail, end = write(self.station, ticket.first, (-7,), (4,))
        deliver(self.station, (end, tail))
        self.assertEqual(deliver(self.station, (tail,)), (None,))
        self.assertIsNone(self.station.capture.receipts.for_attempt(ticket.first))
        deliver(self.station, (head,))
        artifact, _ = self.station.seal('reversed', receipt_id_for(ticket.first))
        self.assertEqual(artifact.samples, (-7, 4))
        self.assertEqual(artifact.peak, 7)
        self.assertEqual([(s.position, s.reference) for s in artifact.segments],
                         [(0, head.reference), (1, tail.reference)])

    def test_event_identity(self):
        first = open_two(self.station, 'a')
        other = open_two(self.station, 'b', 'B')
        head, tail, end = write(self.station, first.first, (7,), (9,))
        for impostor in (replace(head, attempt=other.first),
                         replace(head, attempt=Attempt('a', 'B', 1)),
                         replace(head, attempt=Attempt('a', 'A', 2)),
                         replace(head, position=1), replace(end, area_id='A:99')):
            self.assertIsNone(self.station.event(impostor))
        deliver(self.station, (tail, end))
        self.assertIsNone(self.station.capture.receipts.for_attempt(first.first))
        self.assertIsNone(self.station.capture.receipts.for_attempt(other.first))
        deliver(self.station, (head,))
        receipt = self.station.capture.receipts.for_attempt(first.first)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.samples, (7, 9))
        with self.assertRaises(ValueError):
            self.station.seal('b', receipt.receipt_id)
        self.assertIsNone(self.station.publications.get_for_ticket('b'))

    def test_missing_segment(self):
        ticket = open_two(self.station, 'missing')
        deliver(self.station, self.station.board.write_segment(ticket.first, 0, (7,)))
        self.assertIsNone(self.station.capture.receipts.for_attempt(ticket.first))
        with self.assertRaises(ValueError):
            self.station.seal('missing', receipt_id_for(ticket.first))
        with self.assertRaises(MissingArtifact):
            self.station.get('result:missing')
        self.assertEqual(self.station.connection.execute('SELECT count(*) FROM success_receipts').fetchone(), (0,))

    def test_stop_with_gap(self):
        with Station(':memory:', areas_per_lane=1) as station:
            ticket = open_two(station, 'stopped')
            late_head = station.board.write_segment(ticket.first, 0, (7,))
            self.assertTrue(station.expire_first('stopped'))
            end = confirm(station, ticket.first)
            update, = deliver(station, (end,))
            self.assertTrue(update.area_released)
            self.assertIsNone(update.receipt)
            self.assertEqual(deliver(station, late_head), (None,))
            self.assertIsNone(station.capture.receipts.for_attempt(ticket.first))
            with self.assertRaises(ValueError):
                station.board.write_segment(ticket.first, 1, (9,))
            with self.assertRaises(ValueError):
                station.seal('stopped', receipt_id_for(ticket.first))
            self.assertIsNone(station.publications.get_for_ticket('stopped'))

    def test_first_handoff_before_timeout(self):
        ticket = open_two(self.station, 'early')
        receipt = capture(self.station, ticket.first, (-7,), (4,))
        snapshot = self.station.board.trace.entries()
        self.assertEqual(ticket.state, TicketState.FIRST_READY)
        self.assertFalse(self.station.expire_first('early'))
        self.assertFalse(self.station.ledger.get('early').first_timed_out)
        self.assertEqual(self.station.board.trace.entries(), snapshot)
        with self.assertRaises(ValueError):
            self.station.redo('early')
        artifact, _ = self.station.seal('early', receipt.receipt_id)
        self.assertEqual((artifact.attempt.number, artifact.samples, artifact.peak), (1, (-7, 4), 7))

    def test_invalid_recording_and_exact_integer_peak(self):
        with self.assertRaises(ValueError):
            RecordingSpec(())
        with self.assertRaises(ValueError):
            RecordingSpec((SegmentSpec('gap', 1, 1),))
        with self.assertRaises(ValueError):
            RecordingSpec((SegmentSpec('same', 0, 1), SegmentSpec('same', 1, 1)))
        with self.assertRaises(ValueError):
            open_two(self.station, 'bad-lane', 'C')
        self.assertIsNone(self.station.ledger.get('bad-lane'))
        ticket = open_two(self.station, 'limits')
        with self.assertRaises(DuplicateTicket):
            open_two(self.station, 'limits', 'B')
        trace = self.station.board.trace.entries()
        for invalid in ((), (True,), (2**63,), (1.0,), (1, 2)):
            with self.subTest(samples=invalid), self.assertRaises(ValueError):
                self.station.board.write_segment(ticket.first, 0, invalid)
        self.assertEqual(self.station.board.trace.entries(), trace)
        receipt = capture(self.station, ticket.first, (-(2**63),), (0,))
        artifact, _ = self.station.seal('limits', receipt.receipt_id)
        self.assertEqual(artifact.samples, (-(2**63), 0))
        self.assertEqual(artifact.peak, 2**63)
        self.assertEqual(self.station.get(artifact.artifact_id)[0].peak, 2**63)

    def test_sample_intervals_follow_declared_lengths(self):
        spec = RecordingSpec((SegmentSpec('rise', 0, 2), SegmentSpec('fall', 1, 3)))
        ticket = self.station.open('day', 'uneven', 'B', spec)
        end = self.station.board.write_segment(ticket.first, 1, (4, -8, 0))
        start = self.station.board.write_segment(ticket.first, 0, (-2, 3))
        deliver(self.station, (*start, *end))
        artifact, _ = self.station.seal('uneven', receipt_id_for(ticket.first))
        self.assertEqual((artifact.samples, artifact.peak), ((-2, 3, 4, -8, 0), 8))
        self.assertEqual([(s.start, s.stop) for s in artifact.segments], [(0, 2), (2, 5)])
