from collections.abc import Iterable

from device.descriptors import valid_sample


def absolute_peak(samples: Iterable[int]) -> int:
    values = tuple(samples)
    if not values or not all(map(valid_sample, values)):
        raise ValueError('绝对峰值需要非空的有效整数微应变样本')
    return max(map(abs, values))
