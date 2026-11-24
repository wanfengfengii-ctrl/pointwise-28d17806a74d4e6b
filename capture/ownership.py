from collections.abc import Iterable

from device.descriptors import Attempt, DescriptorState
from device.receiver import ReceiveArea


class AreaOwnership:
    def __init__(self, areas: Iterable[ReceiveArea]) -> None:
        self._areas: dict[str, ReceiveArea] = {}
        self._holders: dict[str, Attempt] = {}
        for area in areas:
            if area.area_id in self._areas:
                raise ValueError('接收区标识重复')
            self._areas[area.area_id] = area

    def claim(self, attempt: Attempt) -> ReceiveArea | None:
        if attempt in self._holders.values():
            raise ValueError('该尝试已持有接收区')
        for area_id, area in self._areas.items():
            if area.lane == attempt.lane and area_id not in self._holders:
                self._holders[area_id] = attempt
                return area
        return None

    def holder(self, area_id: str) -> Attempt | None:
        return self._holders.get(area_id)

    def release(self, area: ReceiveArea, attempt: Attempt, terminal: DescriptorState) -> None:
        if self._areas.get(area.area_id) is not area or self.holder(area.area_id) != attempt:
            raise ValueError('只能归还本次尝试持有的接收区')
        if terminal not in (DescriptorState.COMPLETE, DescriptorState.STOPPED):
            raise ValueError('设备写入权尚未解除')
        area.clear()
        del self._holders[area.area_id]
