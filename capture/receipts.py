from dataclasses import dataclass

from capture.segments import SegmentCoverage, SegmentEvidence
from device.descriptors import Attempt
from device.receiver import ReceiveArea


def receipt_id_for(attempt: Attempt) -> str:
    return f'capture:{attempt.number}:{attempt.ticket_id}'


@dataclass(frozen=True)
class CaptureReceipt:
    receipt_id: str
    attempt: Attempt
    samples: tuple[int, ...]
    segments: tuple[SegmentEvidence, ...]


class ReceiptBook:
    def __init__(self) -> None:
        self._by_id: dict[str, CaptureReceipt] = {}

    def put(self, receipt: CaptureReceipt) -> None:
        if receipt.receipt_id != receipt_id_for(receipt.attempt):
            raise ValueError('实收号与尝试不匹配')
        existing = self.get(receipt.receipt_id)
        if existing is not None and existing != receipt:
            raise ValueError('相同实收号已有不同内容')
        self._by_id[receipt.receipt_id] = receipt

    def get(self, receipt_id: str) -> CaptureReceipt | None:
        return self._by_id.get(receipt_id)

    def for_attempt(self, attempt: Attempt) -> CaptureReceipt | None:
        receipt = self.get(receipt_id_for(attempt))
        if receipt is not None and receipt.attempt == attempt:
            return receipt
        return None


def snapshot_receipt(attempt: Attempt, area: ReceiveArea, coverage: SegmentCoverage) -> CaptureReceipt:
    if coverage.attempt != attempt or coverage.area_id != area.area_id or area.lane != attempt.lane:
        raise ValueError('实写覆盖与接收区归属不符')
    segments = coverage.ordered()
    samples = tuple(value for segment in segments for value in area.read_segment(segment.start, segment.stop))
    return CaptureReceipt(receipt_id_for(attempt), attempt, samples, segments)
