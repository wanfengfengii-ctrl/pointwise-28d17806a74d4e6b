import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from capture.receipts import receipt_id_for
from orders.eligibility import IneligibleReceipt
from orders.retry import RetryStatus
from station import Station
from tests.recording import capture, deliver, open_two


class ShiftWorkflowTests(unittest.TestCase):
    def test_single_area_shift_from_wait_to_offline_collection(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'shift.sqlite3'
            with Station(database, areas_per_lane=1) as live:
                a = open_two(live, 'loss')
                deliver(live, live.board.write_segment(a.first, 0, (7,)))
                self.assertTrue(live.expire_first('loss'))
                self.assertIs(live.redo('loss'), RetryStatus.WAITING_FOR_AREA)
                b = open_two(live, 'independent', 'B')
                other = capture(live, b.first, (4,), (-6,))
                live.seal('independent', other.receipt_id)
                deliver(live, live.board.write_segment(a.first, 1, (9,)))
                with self.assertRaises(IneligibleReceipt):
                    live.seal('loss', receipt_id_for(a.first))
                replacement = capture(live, a.retry_attempt, (11,), (13,))
                live.seal('loss', replacement.receipt_id)
                open_two(live, 'still-pending')

            with Station(database) as offline:
                rows = json.loads(offline.export('day', reversed(offline.registrations('day'))))['rows']
                self.assertEqual([(r['ticket_id'], r['status'], r['receipt_id']) for r in rows],
                                 [('loss', 'signed', 'success:loss'),
                                  ('independent', 'signed', 'success:independent'),
                                  ('still-pending', 'pending', None)])
                self.assertTrue(rows[0]['first_timed_out'])
                self.assertEqual((rows[0]['attempt'], rows[0]['peak'], rows[1]['peak']), (2, 13, 6))
                artifact, _ = offline.get(rows[0]['artifact_id'])
                self.assertEqual(artifact.samples, (11, 13))
                self.assertEqual([(s.attempt.number, s.segment_id, s.start, s.stop) for s in artifact.segments],
                                 [(2, 'head', 0, 1), (2, 'tail', 1, 2)])
