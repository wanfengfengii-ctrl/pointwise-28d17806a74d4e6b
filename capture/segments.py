from dataclasses import dataclass

from device.descriptors import Attempt, RecordingSpec
from device.events import EventKind, RecordingEvent


@dataclass(frozen=True)
class SegmentEvidence:
    attempt: Attempt
    area_id: str
    segment_id: str
    position: int
    start: int
    stop: int
    reference: int
    # 双工单共录的首拍出处：共录号与物理布局原段位置。普通录制和独立补拍为空。
    shared_id: str | None = None
    source_position: int | None = None


class SegmentCoverage:
    def __init__(self, attempt: Attempt, spec: RecordingSpec, area_id: str) -> None:
        self.attempt = attempt
        self.spec = spec
        self.area_id = area_id
        self._by_position: dict[int, SegmentEvidence] = {}

    def record(self, event: RecordingEvent) -> bool:
        if (event.kind is not EventKind.SEGMENT_WRITTEN or event.attempt != self.attempt
                or event.area_id != self.area_id):
            return False
        position = event.position
        if type(position) is not int or not 0 <= position < len(self.spec.segments):
            return False
        segment = self.spec.segment_at(position)
        start, stop = self.spec.interval(position)
        if (event.segment_id != segment.segment_id or type(event.start) is not int
                or type(event.stop) is not int or (event.start, event.stop) != (start, stop)
                or type(event.reference) is not int or event.reference <= 0):
            return False
        if position in self._by_position:
            return False
        self._by_position[position] = SegmentEvidence(
            self.attempt, self.area_id, segment.segment_id, position, start, stop, event.reference,
        )
        return True

    def is_complete(self) -> bool:
        return len(self._by_position) == len(self.spec.segments)

    def ordered(self) -> tuple[SegmentEvidence, ...]:
        if not self.is_complete():
            raise ValueError('原段实写证据尚未齐备')
        return tuple(self._by_position[segment.position] for segment in self.spec.segments)
