import sqlite3
from dataclasses import dataclass


class DuplicateTicket(ValueError):
    pass


class DuplicateShared(ValueError):
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
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS shared_captures (
                shared_id TEXT PRIMARY KEY NOT NULL,
                shift TEXT NOT NULL,
                lane TEXT NOT NULL CHECK (lane IN ('A', 'B'))
            )
        ''')

    def shared_known(self, shared_id: str) -> bool:
        row = self.connection.execute(
            'SELECT 1 FROM shared_captures WHERE shared_id = ?', (shared_id,),
        ).fetchone()
        return row is not None

    def register_shared(
        self, shared_id: str, shift: str, lane: str, ticket_ids: tuple[str, ...],
    ) -> tuple[RegistrationEntry, ...]:
        entries: list[RegistrationEntry] = []
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            self.connection.execute(
                'INSERT INTO shared_captures (shared_id, shift, lane) VALUES (?, ?, ?)',
                (shared_id, shift, lane),
            )
            for ticket_id in ticket_ids:
                cursor = self.connection.execute(
                    'INSERT INTO registrations (shift, ticket_id, lane) VALUES (?, ?, ?)',
                    (shift, ticket_id, lane),
                )
                entries.append(RegistrationEntry(cursor.lastrowid, shift, ticket_id, lane, False))
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise DuplicateShared(f'共录号或工单号已登记 {shared_id}') from exc
        except BaseException:
            self.connection.rollback()
            raise
        return tuple(entries)

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
