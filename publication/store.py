import sqlite3

from publication.artifact import Artifact, SignedPair, SuccessReceipt
from publication.encoding import (
    decode_artifact, decode_success_receipt, encode_artifact, encode_success_receipt,
)


class PublicationIntegrityError(ValueError):
    pass


_SELECT_PAIR = '''
    SELECT a.artifact_id, a.ticket_id, a.body, r.receipt_id, r.body
    FROM artifacts AS a
    LEFT JOIN success_receipts AS r ON a.artifact_id = r.artifact_id
'''


class PublicationStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def initialize(self) -> None:
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY NOT NULL,
                ticket_id TEXT UNIQUE NOT NULL REFERENCES registrations (ticket_id),
                body TEXT NOT NULL
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS success_receipts (
                receipt_id TEXT PRIMARY KEY NOT NULL,
                artifact_id TEXT UNIQUE NOT NULL REFERENCES artifacts (artifact_id),
                body TEXT NOT NULL
            )
        ''')

    def commit(self, artifact: Artifact, receipt: SuccessReceipt) -> None:
        if receipt.artifact_id != artifact.artifact_id or receipt.attempt != artifact.attempt:
            raise ValueError('签件与回执的身份不一致')
        encoded_artifact = encode_artifact(artifact)
        encoded_receipt = encode_success_receipt(receipt)
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            self.connection.execute(
                'INSERT INTO artifacts (artifact_id, ticket_id, body) VALUES (?, ?, ?)',
                (artifact.artifact_id, artifact.attempt.ticket_id, encoded_artifact),
            )
            self.connection.execute(
                'INSERT INTO success_receipts (receipt_id, artifact_id, body) VALUES (?, ?, ?)',
                (receipt.receipt_id, artifact.artifact_id, encoded_receipt),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _read_pair(self, query: str, key: str) -> SignedPair | None:
        row = self.connection.execute(query, (key,)).fetchone()
        if row is None:
            return None
        artifact_id, ticket_id, artifact_body, receipt_id, receipt_body = row
        if receipt_body is None:
            raise PublicationIntegrityError(f'签件缺少成功回执 {artifact_id}')
        artifact = decode_artifact(artifact_body)
        receipt = decode_success_receipt(receipt_body)
        if (artifact.artifact_id != artifact_id or artifact.attempt.ticket_id != ticket_id
                or receipt.receipt_id != receipt_id or receipt.artifact_id != artifact_id
                or receipt.attempt != artifact.attempt):
            raise PublicationIntegrityError(f'持久签件与回执关联不一致 {artifact_id}')
        return artifact, receipt

    def get(self, artifact_id: str) -> SignedPair | None:
        return self._read_pair(_SELECT_PAIR + ' WHERE a.artifact_id = ?', artifact_id)

    def get_for_ticket(self, ticket_id: str) -> SignedPair | None:
        return self._read_pair(_SELECT_PAIR + ' WHERE a.ticket_id = ?', ticket_id)
