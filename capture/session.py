from dataclasses import dataclass

from capture.ownership import AreaOwnership
from capture.receipts import CaptureReceipt, ReceiptBook, snapshot_receipt
from capture.segments import SegmentCoverage
from device.board import Board
from device.descriptors import Attempt, DescriptorState, RecordingSpec
from device.events import EventKind, RecordingEvent
from device.receiver import ReceiveArea


@dataclass(frozen=True)
class CaptureUpdate:
    attempt: Attempt
    receipt: CaptureReceipt | None = None
    area_released: bool = False


@dataclass
class _Collection:
    area: ReceiveArea
    coverage: SegmentCoverage
    natural_end_seen: bool = False


class CaptureSession:
    def __init__(self, board: Board, ownership: AreaOwnership) -> None:
        self.board = board
        self.ownership = ownership
        self.receipts = ReceiptBook()
        self._active: dict[Attempt, _Collection] = {}

    def submit(self, attempt: Attempt, spec: RecordingSpec) -> bool:
        if not isinstance(attempt, Attempt) or not isinstance(spec, RecordingSpec):
            raise ValueError('采集提交需要尝试和录制布局')
        if self.board.has_attempt(attempt):
            raise ValueError('该尝试已提交采集')
        area = self.ownership.claim(attempt)
        if area is None:
            return False
        self.board.accept(attempt, spec, area)
        self._active[attempt] = _Collection(area, SegmentCoverage(attempt, spec, area.area_id))
        return True

    def request_stop(self, attempt: Attempt) -> bool:
        return attempt in self._active and self.board.request_stop(attempt)

    def _close(
        self, attempt: Attempt, collection: _Collection, state: DescriptorState,
        receipt: CaptureReceipt | None = None,
    ) -> CaptureUpdate:
        self.ownership.release(collection.area, attempt, state)
        del self._active[attempt]
        return CaptureUpdate(attempt, receipt, area_released=True)

    def event(self, event: RecordingEvent) -> CaptureUpdate | None:
        if not self.board.issued(event):
            return None
        attempt = event.attempt
        collection = self._active.get(attempt)
        if (collection is None or collection.area.area_id != event.area_id
                or self.ownership.holder(event.area_id) != attempt):
            return None
        if event.kind is EventKind.STOP_CONFIRMED:
            return self._close(attempt, collection, self.board.state(attempt))
        if event.kind is EventKind.COMPLETED:
            if collection.natural_end_seen:
                return None
            collection.natural_end_seen = True
        elif not collection.coverage.record(event):
            return None
        if not collection.natural_end_seen or not collection.coverage.is_complete():
            return CaptureUpdate(attempt)
        terminal = self.board.state(attempt)
        if terminal is not DescriptorState.COMPLETE:
            raise RuntimeError('自然完成通知与板卡状态不一致')
        receipt = snapshot_receipt(attempt, collection.area, collection.coverage)
        self.receipts.put(receipt)
        return self._close(attempt, collection, terminal, receipt)
