from dataclasses import dataclass
from enum import Enum

from device.descriptors import Attempt


class TraceAction(Enum):
    ACCEPTED = 'accepted'
    WRITTEN = 'written'
    STOP_REQUESTED = 'stop_requested'
    COMPLETED = 'completed'
    STOP_CONFIRMED = 'stop_confirmed'


@dataclass(frozen=True)
class TraceEntry:
    reference: int
    action: TraceAction
    attempt: Attempt
    area_id: str
    position: int | None = None
    segment_id: str | None = None
    start: int | None = None
    stop: int | None = None


class DeviceTrace:
    def __init__(self) -> None:
        self._entries: list[TraceEntry] = []

    def record(
        self, action: TraceAction, attempt: Attempt, area_id: str, *,
        position: int | None = None, segment_id: str | None = None,
        start: int | None = None, stop: int | None = None,
    ) -> TraceEntry:
        entry = TraceEntry(len(self._entries) + 1, action, attempt, area_id,
                           position, segment_id, start, stop)
        self._entries.append(entry)
        return entry

    def entries(self) -> tuple[TraceEntry, ...]:
        return tuple(self._entries)
