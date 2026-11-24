import json
from collections.abc import Iterable

from handover.manifest import ShiftManifest, build_manifest
from handover.reconcile import reconcile_artifact
from orders.ledger import RegistrationEntry
from publication.store import PublicationStore


def render_manifest(manifest: ShiftManifest) -> str:
    rows = []
    for row in manifest.rows:
        entry = row.registration
        values = dict(sequence=entry.sequence, ticket_id=entry.ticket_id, lane=entry.lane,
                      first_timed_out=entry.first_timed_out, status=row.status,
                      artifact_id=None, receipt_id=None)
        if row.publication is not None:
            artifact, receipt = row.publication
            values.update(artifact_id=artifact.artifact_id, receipt_id=receipt.receipt_id,
                          attempt=artifact.attempt.number, peak=artifact.peak, sample_count=len(artifact.samples))
        rows.append(values)
    return json.dumps({'shift': manifest.shift, 'rows': rows}, ensure_ascii=False, separators=(',', ':'))


def export(store: PublicationStore, shift: str, registrations: Iterable[RegistrationEntry]) -> str:
    manifest = build_manifest(shift, registrations, store)
    for row in manifest.rows:
        reconcile_artifact(row)
    return render_manifest(manifest)
