from dataclasses import dataclass

from capture.receipts import CaptureReceipt
from capture.segments import SegmentEvidence
from device.descriptors import Attempt
from publication.metrics import absolute_peak


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    capture_receipt_id: str
    attempt: Attempt
    samples: tuple[int, ...]
    peak: int
    segments: tuple[SegmentEvidence, ...]


@dataclass(frozen=True)
class SuccessReceipt:
    receipt_id: str
    artifact_id: str
    attempt: Attempt


SignedPair = tuple[Artifact, SuccessReceipt]


def create_artifact(receipt: CaptureReceipt) -> SignedPair:
    ticket_id = receipt.attempt.ticket_id
    artifact_id = f'result:{ticket_id}'
    artifact = Artifact(artifact_id, receipt.receipt_id, receipt.attempt, receipt.samples,
                        absolute_peak(receipt.samples), receipt.segments)
    return artifact, SuccessReceipt(f'success:{ticket_id}', artifact_id, receipt.attempt)
