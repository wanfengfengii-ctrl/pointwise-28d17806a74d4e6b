from collections.abc import Iterable
from struct import Struct

from device.descriptors import LANES, RecordingSpec, valid_sample

_CELL = Struct('<q')


class ReceiveArea:
    def __init__(self, area_id: str, lane: str) -> None:
        if not isinstance(area_id, str) or not area_id or lane not in LANES:
            raise ValueError('接收区身份无效')
        self.area_id = area_id
        self.lane = lane
        self._cells: dict[int, bytes] = {}

    def write_segment(self, spec: RecordingSpec, position: int, samples: Iterable[int]) -> None:
        segment = spec.segment_at(position)
        values = tuple(samples)
        if len(values) != segment.sample_count or not all(map(valid_sample, values)):
            raise ValueError('实写样本数或整数范围与布局不符')
        start, _ = spec.interval(position)
        # 按实际样本位置存储定长字节。空位置与实写的零保持可区分。
        self._cells.update((start + offset, _CELL.pack(value)) for offset, value in enumerate(values))

    def read_segment(self, start: int, stop: int) -> tuple[int, ...]:
        if type(start) is not int or type(stop) is not int or not 0 <= start < stop:
            raise ValueError('读取区间无效')
        try:
            return tuple(_CELL.unpack(self._cells[index])[0] for index in range(start, stop))
        except KeyError as exc:
            raise ValueError('读取区间含未实写位置') from exc

    def clear(self) -> None:
        self._cells.clear()
