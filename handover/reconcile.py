from capture.receipts import receipt_id_for
from handover.manifest import ManifestRow
from publication.metrics import absolute_peak
from publication.store import PublicationIntegrityError


def reconcile_artifact(row: ManifestRow) -> None:
    if row.publication is None:
        return
    artifact, receipt = row.publication
    entry = row.registration
    attempt = artifact.attempt
    if ((attempt.ticket_id, attempt.lane) != (entry.ticket_id, entry.lane)
            or (attempt.number == 2) != entry.first_timed_out
            or artifact.artifact_id != f'result:{entry.ticket_id}'
            or artifact.capture_receipt_id != receipt_id_for(attempt)
            or receipt.receipt_id != f'success:{entry.ticket_id}'
            or (receipt.artifact_id, receipt.attempt) != (artifact.artifact_id, attempt)):
        raise PublicationIntegrityError('签件或成功回执不属于登记工单的正式尝试')
    try:
        calculated_peak = absolute_peak(artifact.samples)
    except ValueError as exc:
        raise PublicationIntegrityError('固定样本无效') from exc
    if type(artifact.peak) is not int or calculated_peak != artifact.peak:
        raise PublicationIntegrityError('固定峰值与原序样本不符')
    cursor = 0
    names: set[str] = set()
    references: set[int] = set()
    areas: set[str] = set()
    for position, segment in enumerate(artifact.segments):
        if (segment.attempt != attempt or type(segment.position) is not int or segment.position != position
                or type(segment.start) is not int or type(segment.stop) is not int
                or segment.start != cursor or not cursor < segment.stop <= len(artifact.samples)
                or not isinstance(segment.segment_id, str) or not segment.segment_id.strip()
                or segment.segment_id in names or not isinstance(segment.area_id, str) or not segment.area_id
                or type(segment.reference) is not int or segment.reference <= 0
                or segment.reference in references):
            raise PublicationIntegrityError('固定原段出处身份或覆盖区间无效')
        names.add(segment.segment_id)
        references.add(segment.reference)
        areas.add(segment.area_id)
        cursor = segment.stop
    if cursor != len(artifact.samples) or len(areas) != 1:
        raise PublicationIntegrityError('固定出处未覆盖同一次完整实收')
