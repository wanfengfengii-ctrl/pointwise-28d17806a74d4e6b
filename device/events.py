from dataclasses import dataclass
from enum import Enum

from device.descriptors import Attempt


class EventKind(Enum):
    SEGMENT_WRITTEN = 'segment_written'
    COMPLETED = 'completed'
    STOP_CONFIRMED = 'stop_confirmed'


@dataclass(frozen=True)
class RecordingEvent:
    kind: EventKind
    attempt: Attempt
    area_id: str
    reference: int
    position: int | None = None
    segment_id: str | None = None
    start: int | None = None
    stop: int | None = None
