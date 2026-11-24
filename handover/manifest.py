from collections.abc import Iterable
from dataclasses import dataclass

from orders.ledger import RegistrationEntry
from publication.artifact import SignedPair
from publication.store import PublicationStore


@dataclass(frozen=True)
class ManifestRow:
    registration: RegistrationEntry
    publication: SignedPair | None

    @property
    def status(self) -> str:
        return 'pending' if self.publication is None else 'signed'


@dataclass(frozen=True)
class ShiftManifest:
    shift: str
    rows: tuple[ManifestRow, ...]


def build_manifest(shift: str, registrations: Iterable[RegistrationEntry], store: PublicationStore) -> ShiftManifest:
    if not isinstance(shift, str) or not shift.strip():
        raise ValueError('班次不能为空')
    entries = tuple(registrations)
    if any(entry.shift != shift for entry in entries):
        raise ValueError('原登记清单混有其他班次工单')
    if len({entry.ticket_id for entry in entries}) != len(entries):
        raise ValueError('原登记清单包含重复工单')
    ordered = sorted(entries, key=lambda entry: entry.sequence)
    rows = tuple(ManifestRow(entry, store.get_for_ticket(entry.ticket_id)) for entry in ordered)
    return ShiftManifest(shift, rows)
