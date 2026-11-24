from collections.abc import Iterable

from device.descriptors import Attempt, Descriptor, DescriptorState, RecordingSpec
from device.events import EventKind, RecordingEvent
from device.receiver import ReceiveArea
from device.trace import DeviceTrace, TraceAction, TraceEntry


class Board:
    def __init__(self) -> None:
        self.trace = DeviceTrace()
        self._descriptors: dict[Attempt, Descriptor] = {}
        self._notifications: dict[int, RecordingEvent] = {}

    def accept(self, attempt: Attempt, spec: RecordingSpec, area: ReceiveArea) -> None:
        if not isinstance(attempt, Attempt) or not isinstance(spec, RecordingSpec):
            raise ValueError('板卡指派不含有效尝试或布局')
        if not isinstance(area, ReceiveArea) or area.lane != attempt.lane:
            raise ValueError('接收区测道与尝试不同')
        if self.has_attempt(attempt):
            raise ValueError('板卡已接受该尝试')
        self._descriptors[attempt] = Descriptor(attempt, spec, area)
        self.trace.record(TraceAction.ACCEPTED, attempt, area.area_id)

    def has_attempt(self, attempt: Attempt) -> bool:
        return attempt in self._descriptors

    def _descriptor(self, attempt: Attempt) -> Descriptor:
        try:
            return self._descriptors[attempt]
        except KeyError as exc:
            raise ValueError('板卡未接受该尝试') from exc

    def state(self, attempt: Attempt) -> DescriptorState:
        return self._descriptor(attempt).state

    def _notify(self, kind: EventKind, entry: TraceEntry) -> RecordingEvent:
        event = RecordingEvent(kind, entry.attempt, entry.area_id, entry.reference,
                               entry.position, entry.segment_id, entry.start, entry.stop)
        self._notifications[event.reference] = event
        return event

    def write_segment(self, attempt: Attempt, position: int, samples: Iterable[int]) -> tuple[RecordingEvent, ...]:
        descriptor = self._descriptor(attempt)
        if not descriptor.state.can_write:
            raise ValueError('该尝试的设备写入权已解除')
        segment = descriptor.spec.segment_at(position)
        if position in descriptor.written:
            raise ValueError('原段已实写')
        descriptor.area.write_segment(descriptor.spec, position, samples)
        descriptor.written.add(position)
        start, stop = descriptor.spec.interval(position)
        entry = self.trace.record(TraceAction.WRITTEN, attempt, descriptor.area.area_id,
                                  position=position, segment_id=segment.segment_id, start=start, stop=stop)
        written = self._notify(EventKind.SEGMENT_WRITTEN, entry)
        if len(descriptor.written) < len(descriptor.spec.segments):
            return (written,)
        descriptor.state = DescriptorState.COMPLETE
        finished = self.trace.record(TraceAction.COMPLETED, attempt, descriptor.area.area_id)
        return written, self._notify(EventKind.COMPLETED, finished)

    def request_stop(self, attempt: Attempt) -> bool:
        descriptor = self._descriptor(attempt)
        if descriptor.state is not DescriptorState.WRITING:
            return False
        descriptor.state = DescriptorState.STOP_PENDING
        self.trace.record(TraceAction.STOP_REQUESTED, attempt, descriptor.area.area_id)
        return True

    def confirm_stop(self, attempt: Attempt) -> RecordingEvent | None:
        descriptor = self._descriptor(attempt)
        if not descriptor.state.can_write:
            return None
        if descriptor.state is not DescriptorState.STOP_PENDING:
            raise ValueError('该尝试未收到停止请求')
        descriptor.state = DescriptorState.STOPPED
        entry = self.trace.record(TraceAction.STOP_CONFIRMED, attempt, descriptor.area.area_id)
        return self._notify(EventKind.STOP_CONFIRMED, entry)

    def deliver_event(self, reference: int) -> RecordingEvent:
        if type(reference) is not int or reference not in self._notifications:
            raise ValueError('该设备事实没有可投递通知')
        return self._notifications[reference]

    def issued(self, event: RecordingEvent) -> bool:
        return (isinstance(event, RecordingEvent) and type(event.reference) is int
                and self._notifications.get(event.reference) == event)
