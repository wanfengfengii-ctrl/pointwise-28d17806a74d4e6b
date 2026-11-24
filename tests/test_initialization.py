import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from orders.ledger import DuplicateTicket
from station import Station
from tests.recording import capture, open_two


class InitializationTests(unittest.TestCase):
    def test_repeat_init(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'station.sqlite3'
            with Station(database) as first:
                original = open_two(first, 'original')
                expired = open_two(first, 'expired', 'B')
                self.assertTrue(first.expire_first(expired.first.ticket_id))
                receipt = capture(first, original.first, (-7,), (4,))
                signed = first.seal('original', receipt.receipt_id)
                registrations = first.registrations('day')
            for _ in range(2):
                with Station(database) as station:
                    self.assertEqual(station.registrations('day'), registrations)
                    self.assertTrue(station.ledger.get('expired').first_timed_out)
                    self.assertEqual(station.get(signed[0].artifact_id), signed)
                    self.assertEqual(station.board.trace.entries(), ())
                    with self.assertRaises(DuplicateTicket):
                        open_two(station, 'original')
            with Station(database) as station:
                following = open_two(station, 'following', 'B')
                self.assertEqual(following.registration.sequence, 3)
                self.assertEqual([entry.ticket_id for entry in station.registrations('day')],
                                 ['original', 'expired', 'following'])
