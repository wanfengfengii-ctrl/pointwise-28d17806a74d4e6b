import sqlite3
from dataclasses import dataclass


class DuplicateTicket(ValueError):
    pass


@dataclass(frozen=True)
class RegistrationEntry:
    sequence: int
    shift: str
    ticket_id: str
    lane: str
    first_timed_out: bool

    @property
    def identity(self) -> tuple[int, str, str, str]:
        return self.sequence, self.shift, self.ticket_id, self.lane


_COLUMNS = 'sequence, shift, ticket_id, lane, first_timed_out'


def _entry(row: tuple[int, str, str, str, int]) -> RegistrationEntry:
    sequence, shift, ticket_id, lane, timed_out = row
    return RegistrationEntry(sequence, shift, ticket_id, lane, bool(timed_out))


class RegistrationLedger:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS registrations (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                shift TEXT NOT NULL,
                ticket_id TEXT NOT NULL UNIQUE,
                lane TEXT NOT NULL CHECK (lane IN ('A', 'B')),
                first_timed_out INTEGER NOT NULL DEFAULT 0 CHECK (first_timed_out IN (0, 1))
            )
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS registrations_shift_order ON registrations (shift, sequence)
        ''')

    def register(self, shift: str, ticket_id: str, lane: str) -> RegistrationEntry:
        try:
            with self.connection:
                cursor = self.connection.execute(
                    'INSERT INTO registrations (shift, ticket_id, lane) VALUES (?, ?, ?)',
                    (shift, ticket_id, lane),
                )
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                raise DuplicateTicket(f'工单号已登记 {ticket_id}') from exc
            raise
        return RegistrationEntry(cursor.lastrowid, shift, ticket_id, lane, False)

    def record_first_timeout(self, ticket_id: str) -> RegistrationEntry:
        with self.connection:
            self.connection.execute('UPDATE registrations SET first_timed_out = 1 WHERE ticket_id = ?', (ticket_id,))
            entry = self.get(ticket_id)
            if entry is None:
                raise LookupError(ticket_id)
        return entry

    def get(self, ticket_id: str) -> RegistrationEntry | None:
        row = self.connection.execute(
            f'SELECT {_COLUMNS} FROM registrations WHERE ticket_id = ?', (ticket_id,),
        ).fetchone()
        return None if row is None else _entry(row)

    def for_shift(self, shift: str) -> tuple[RegistrationEntry, ...]:
        rows = self.connection.execute(
            f'SELECT {_COLUMNS} FROM registrations WHERE shift = ? ORDER BY sequence', (shift,),
        )
        return tuple(map(_entry, rows))
