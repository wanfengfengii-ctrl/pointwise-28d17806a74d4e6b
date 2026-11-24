import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from handover.lookup import MissingArtifact
from orders.lifecycle import TicketState
from publication.store import PublicationIntegrityError
from station import Station
from tests.recording import StationTest, capture, open_two, scenario


def counts(station):
    db = station.connection
    return (db.execute('SELECT count(*) FROM artifacts').fetchone()[0],
            db.execute('SELECT count(*) FROM success_receipts').fetchone()[0])


class HandoverTests(StationTest):
    def test_seal_retry(self):
        station = self.station
        ticket = open_two(station, 'commit')
        receipt = capture(station, ticket.first, (11,), (13,))
        station.connection.executescript('''
            CREATE TEMP TRIGGER reject_receipt BEFORE INSERT ON success_receipts
            BEGIN SELECT RAISE(ABORT, 'receipt insert blocked'); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'receipt insert blocked'):
            station.seal('commit', receipt.receipt_id)
        self.assertEqual(counts(station), (0, 0))
        with self.assertRaises(MissingArtifact):
            station.get('result:commit')
        self.assertIs(ticket.state, TicketState.FIRST_READY)
        self.assertEqual(station.capture.receipts.get(receipt.receipt_id), receipt)
        station.connection.execute('DROP TRIGGER reject_receipt')
        pair = station.seal('commit', receipt.receipt_id)
        self.assertEqual((pair[0].samples, pair[0].peak), ((11, 13), 13))
        self.assertEqual(station.get(pair[0].artifact_id), pair)
        with self.assertRaises(ValueError):
            station.seal('commit', receipt.receipt_id)
        self.assertEqual(counts(station), (1, 1))

    def test_atomic_boundary(self):
        station = self.station
        prior = open_two(station, 'prior', 'B')
        receipt = capture(station, prior.first, (3,), (-5,))
        prior_pair = station.seal('prior', receipt.receipt_id)
        ticket = open_two(station, 'deferred')
        receipt = capture(station, ticket.first, (11,), (13,))
        station.connection.executescript('''
            CREATE TABLE commit_parent (id INTEGER PRIMARY KEY);
            CREATE TABLE commit_child (
                id INTEGER REFERENCES commit_parent(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TEMP TRIGGER defer_failure AFTER INSERT ON success_receipts
            BEGIN INSERT INTO commit_child VALUES(1); END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            station.seal('deferred', receipt.receipt_id)
        self.assertFalse(station.connection.in_transaction)
        self.assertEqual(counts(station), (1, 1))
        self.assertEqual(station.connection.execute('SELECT count(*) FROM commit_child').fetchone(), (0,))
        self.assertEqual(station.get(prior_pair[0].artifact_id), prior_pair)
        self.assertIsNone(station.publications.get_for_ticket('deferred'))
        self.assertIs(ticket.state, TicketState.FIRST_READY)
        station.connection.execute('DROP TRIGGER defer_failure')
        pair = station.seal('deferred', receipt.receipt_id)
        self.assertEqual(station.get(pair[0].artifact_id), pair)
        self.assertEqual(counts(station), (2, 2))

    def test_reopen_receipt(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'station.sqlite3'
            with Station(database) as live:
                ticket = open_two(live, 'offline')
                receipt = capture(live, ticket.first, (-7,), (4,))
                original = live.seal('offline', receipt.receipt_id)
            with Station(database) as offline:
                self.assertEqual(offline.board.trace.entries(), ())
                collected = offline.get('result:offline')
                self.assertEqual(collected, original)
                artifact, success = collected
                self.assertEqual((artifact.samples, artifact.peak), ((-7, 4), 7))
                self.assertEqual([(s.attempt, s.segment_id, s.start, s.stop, s.reference) for s in artifact.segments],
                                 [(ticket.first, 'head', 0, 1, 2), (ticket.first, 'tail', 1, 2, 3)])
                self.assertEqual(success.receipt_id, 'success:offline')
                with self.assertRaises(MissingArtifact):
                    offline.get('result:unknown')

    def test_shift_manifest(self):
        station = self.station
        open_two(station, 'pending')
        signed = open_two(station, 'signed', 'B')
        receipt = capture(station, signed.first, (-7,), (4,))
        artifact, success = station.seal('signed', receipt.receipt_id)
        self.assertEqual((artifact.artifact_id, success.receipt_id), ('result:signed', 'success:signed'))
        manifest = json.loads(station.export('day', reversed(station.registrations('day'))))
        expected_rows = [dict(sequence=1, ticket_id='pending', lane='A', first_timed_out=False, status='pending',
                              artifact_id=None, receipt_id=None),
                         dict(sequence=2, ticket_id='signed', lane='B', first_timed_out=False, status='signed',
                              artifact_id='result:signed', receipt_id='success:signed', attempt=1, peak=7,
                              sample_count=2)]
        self.assertEqual(manifest, {'shift': 'day', 'rows': expected_rows})
        scenario('v8', inputs={'requested_order': ['signed', 'pending'], 'signed_samples': [-7, 4]},
                 expected={'samples': [-7, 4], 'peak': 7}, actual={'samples': artifact.samples, 'peak': artifact.peak},
                 downstream_expected=expected_rows, downstream_actual=manifest['rows'],
                 code_paths=['orders/ledger.py', 'publication/seal.py', 'handover/manifest.py', 'handover/export.py'],
                 oracle='pending 先登记。signed 是唯一已签工单，两点绝对值的较大值为 7。')

    def test_foreign_shift(self):
        station = self.station
        today = open_two(station, 'today')
        tomorrow = open_two(station, 'tomorrow', 'B', shift='next')
        with self.assertRaisesRegex(ValueError, '其他班次'):
            station.export('day', (today.registration, tomorrow.registration))
        self.assertEqual([row['ticket_id'] for row in json.loads(station.export('day', [today.registration]))['rows']],
                         ['today'])

    def test_registration_order(self):
        station = self.station
        first = open_two(station, 'Z')
        open_two(station, 'X', 'B', shift='night')
        last = open_two(station, 'A')
        rows = json.loads(station.export('day', (last.registration, first.registration)))['rows']
        self.assertEqual([(row['sequence'], row['ticket_id']) for row in rows], [(1, 'Z'), (3, 'A')])

    def test_export_rejects_invalid_fixed_content(self):
        station = self.station
        ticket = open_two(station, 'check')
        receipt = capture(station, ticket.first, (-7,), (4,))
        artifact, _ = station.seal('check', receipt.receipt_id)
        db = station.connection
        saved, = db.execute('SELECT body FROM artifacts WHERE artifact_id = ?', (artifact.artifact_id,)).fetchone()
        wrong_peak = json.loads(saved)
        wrong_peak['peak'] = 4
        wrong_source = json.loads(saved)
        wrong_source['segments'][1]['start'] = 0
        for edited in (wrong_peak, wrong_source):
            with self.subTest(body=edited):
                db.execute('UPDATE artifacts SET body = ? WHERE artifact_id = ?',
                           (json.dumps(edited), artifact.artifact_id))
                with self.assertRaises(PublicationIntegrityError):
                    station.export('day', station.registrations('day'))
