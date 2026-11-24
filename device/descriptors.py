from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from device.receiver import ReceiveArea

LANES = ('A', 'B')
SAMPLE_MIN = -(1 << 63)
SAMPLE_MAX = (1 << 63) - 1


def valid_sample(value: object) -> bool:
    return type(value) is int and SAMPLE_MIN <= value <= SAMPLE_MAX


@dataclass(frozen=True)
class Attempt:
    ticket_id: str
    lane: str
    number: int

    def __post_init__(self) -> None:
        if not isinstance(self.ticket_id, str) or not self.ticket_id.strip():
            raise ValueError('工单号不能为空')
        if self.lane not in LANES:
            raise ValueError('无效测道')
        if type(self.number) is not int or self.number not in (1, 2):
            raise ValueError('尝试号必须为 1 或 2')


@dataclass(frozen=True)
class SegmentSpec:
    segment_id: str
    position: int
    sample_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.segment_id, str) or not self.segment_id.strip():
            raise ValueError('段标识不能为空')
        if type(self.position) is not int or self.position < 0:
            raise ValueError('段位置必须为非负整数')
        if type(self.sample_count) is not int or self.sample_count <= 0:
            raise ValueError('每段须声明正整数样本数')


@dataclass(frozen=True)
class RecordingSpec:
    segments: tuple[SegmentSpec, ...]
    _offsets: tuple[int, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        segments = tuple(self.segments)
        if not segments:
            raise ValueError('录制布局不能为空')
        names: set[str] = set()
        offsets = [0]
        for position, segment in enumerate(segments):
            if not isinstance(segment, SegmentSpec) or segment.position != position:
                raise ValueError('布局须按从零开始的连续段位置声明')
            if segment.segment_id in names:
                raise ValueError('布局存在重复段标识')
            names.add(segment.segment_id)
            offsets.append(offsets[-1] + segment.sample_count)
        object.__setattr__(self, 'segments', segments)
        object.__setattr__(self, '_offsets', tuple(offsets))

    def segment_at(self, position: int) -> SegmentSpec:
        if type(position) is not int or not 0 <= position < len(self.segments):
            raise ValueError('段位置不在录制布局内')
        return self.segments[position]

    def interval(self, position: int) -> tuple[int, int]:
        self.segment_at(position)
        return self._offsets[position], self._offsets[position + 1]


class DescriptorState(Enum):
    WRITING = auto()
    STOP_PENDING = auto()
    COMPLETE = auto()
    STOPPED = auto()

    @property
    def can_write(self) -> bool:
        return self in (DescriptorState.WRITING, DescriptorState.STOP_PENDING)


@dataclass
class Descriptor:
    attempt: Attempt
    spec: RecordingSpec
    area: ReceiveArea
    state: DescriptorState = DescriptorState.WRITING
    written: set[int] = field(default_factory=set)
