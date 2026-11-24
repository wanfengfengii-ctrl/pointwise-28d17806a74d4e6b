from collections.abc import Iterable

from device.descriptors import Attempt, Descriptor, DescriptorState, RecordingSpec
from device.events import EventKind, RecordingEvent
from device.receiver import ReceiveArea
from device.trace import DeviceTrace, TraceAction, TraceEntry


class Board:
    """板卡录制。尝试（Attempt）与双工单共录号（str）都是录制身份。"""

    def __init__(self) -> None:
        self.trace = DeviceTrace()
        self._descriptors: dict[object, Descriptor] = {}
        self._notifications: dict[int, RecordingEvent] = {}

    @staticmethod
    def _identity(value: object) -> object:
        if isinstance(value, Attempt):
            return value
        if isinstance(value, str) and value.strip():
            return value
        raise ValueError('板卡录制身份须为尝试或非空共录号')

    def accept(self, attempt: Attempt | str, spec: RecordingSpec, area: ReceiveArea,
               *, lane: str | None = None) -> None:
        key = self._identity(attempt)
        if not isinstance(spec, RecordingSpec):
            raise ValueError('板卡指派不含有效布局')
        lane = lane if lane is not None else getattr(attempt, 'lane', None)
        if lane not in ('A', 'B'):
            raise ValueError('板卡指派缺少有效测道')
        if not isinstance(area, ReceiveArea) or area.lane != lane:
            raise ValueError('接收区测道与录制身份不同')
        if key in self._descriptors:
            raise ValueError('板卡已接受该录制身份')
        self._descriptors[key] = Descriptor(attempt, spec, area)
        self.trace.record(TraceAction.ACCEPTED, attempt, area.area_id)

    def has_attempt(self, attempt: object) -> bool:
        try:
            return self._identity(attempt) in self._descriptors
        except ValueError:
            return False

    def _descriptor(self, attempt: object) -> Descriptor:
        try:
            return self._descriptors[self._identity(attempt)]
        except KeyError as exc:
            raise ValueError('板卡未接受该尝试') from exc

    def state(self, attempt: object) -> DescriptorState:
        return self._descriptor(attempt).state

    def _notify(self, kind: EventKind, entry: TraceEntry) -> RecordingEvent:
        event = RecordingEvent(kind, entry.attempt, entry.area_id, entry.reference,
                               entry.position, entry.segment_id, entry.start, entry.stop)
        self._notifications[event.reference] = event
        return event

    def write_segment(self, attempt: object, position: int, samples: Iterable[int]) -> tuple[RecordingEvent, ...]:
        descriptor = self._descriptor(attempt)
        if not descriptor.state.can_write:
            raise ValueError('该尝试的设备写入权已解除')
        segment = descriptor.spec.segment_at(position)
        if position in descriptor.written:
            raise ValueError('原段已实写')
        descriptor.area.write_segment(descriptor.spec, position, samples)
        descriptor.written.add(position)
        start, stop = descriptor.spec.interval(position)
        entry = self.trace.record(TraceAction.WRITTEN, descriptor.attempt, descriptor.area.area_id,
                                  position=position, segment_id=segment.segment_id, start=start, stop=stop)
        written = self._notify(EventKind.SEGMENT_WRITTEN, entry)
        if len(descriptor.written) < len(descriptor.spec.segments):
            return (written,)
        descriptor.state = DescriptorState.COMPLETE
        finished = self.trace.record(TraceAction.COMPLETED, descriptor.attempt, descriptor.area.area_id)
        return written, self._notify(EventKind.COMPLETED, finished)

    def request_stop(self, attempt: object) -> bool:
        descriptor = self._descriptor(attempt)
        if descriptor.state is not DescriptorState.WRITING:
            return False
        descriptor.state = DescriptorState.STOP_PENDING
        self.trace.record(TraceAction.STOP_REQUESTED, descriptor.attempt, descriptor.area.area_id)
        return True

    def confirm_stop(self, attempt: object) -> RecordingEvent | None:
        descriptor = self._descriptor(attempt)
        if not descriptor.state.can_write:
            return None
        if descriptor.state is not DescriptorState.STOP_PENDING:
            raise ValueError('该尝试未收到停止请求')
        descriptor.state = DescriptorState.STOPPED
        entry = self.trace.record(TraceAction.STOP_CONFIRMED, descriptor.attempt, descriptor.area.area_id)
        return self._notify(EventKind.STOP_CONFIRMED, entry)

    def deliver_event(self, reference: int) -> RecordingEvent:
        if type(reference) is not int or reference not in self._notifications:
            raise ValueError('该设备事实没有可投递通知')
        return self._notifications[reference]

    def issued(self, event: RecordingEvent) -> bool:
        return (isinstance(event, RecordingEvent) and type(event.reference) is int
                and self._notifications.get(event.reference) == event)
