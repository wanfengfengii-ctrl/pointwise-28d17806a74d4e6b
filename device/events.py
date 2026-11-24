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
    # 普通录制用 Attempt，双工单共录号为共录号字符串；两者都是板卡录制身份。
    attempt: Attempt | str
    area_id: str
    reference: int
    position: int | None = None
    segment_id: str | None = None
    start: int | None = None
    stop: int | None = None
