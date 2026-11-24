from collections.abc import Iterable

from device.descriptors import Attempt, DescriptorState
from device.receiver import ReceiveArea

RecordingIdentity = Attempt | str


class AreaOwnership:
    def __init__(self, areas: Iterable[ReceiveArea]) -> None:
        self._areas: dict[str, ReceiveArea] = {}
        self._holders: dict[str, RecordingIdentity] = {}
        for area in areas:
            if area.area_id in self._areas:
                raise ValueError('接收区标识重复')
            self._areas[area.area_id] = area

    def free_for(self, lane: str) -> ReceiveArea | None:
        for area_id, area in self._areas.items():
            if area.lane == lane and area_id not in self._holders:
                return area
        return None

    def claim(
        self, attempt: RecordingIdentity, area: ReceiveArea | None = None, *, lane: str | None = None,
    ) -> ReceiveArea | None:
        if attempt in self._holders.values():
            raise ValueError('该尝试已持有接收区')
        lane = lane if lane is not None else getattr(attempt, 'lane', None)
        if lane not in ('A', 'B'):
            raise ValueError('接收区申请缺少有效测道')
        if area is not None:
            if self._areas.get(area.area_id) is not area or area.lane != lane:
                raise ValueError('指定接收区不可用于该尝试')
            if area.area_id in self._holders:
                raise ValueError('指定接收区已被占用')
            self._holders[area.area_id] = attempt
            return area
        for area_id, candidate in self._areas.items():
            if candidate.lane == lane and area_id not in self._holders:
                self._holders[area_id] = attempt
                return candidate
        return None

    def holder(self, area_id: str) -> RecordingIdentity | None:
        return self._holders.get(area_id)

    def release(self, area: ReceiveArea, attempt: RecordingIdentity, terminal: DescriptorState) -> None:
        if self._areas.get(area.area_id) is not area or self.holder(area.area_id) != attempt:
            raise ValueError('只能归还本次尝试持有的接收区')
        if terminal not in (DescriptorState.COMPLETE, DescriptorState.STOPPED):
            raise ValueError('设备写入权尚未解除')
        area.clear()
        del self._holders[area.area_id]
