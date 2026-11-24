import json
import unittest

from device.descriptors import Attempt, RecordingSpec, SegmentSpec
from station import Station


TWO_SEGMENTS = RecordingSpec((SegmentSpec('head', 0, 1), SegmentSpec('tail', 1, 1)))


class StationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.station = Station(':memory:')
        self.addCleanup(self.station.close)


def open_two(station: Station, ticket_id: str, lane: str = 'A', shift: str = 'day'):
    return station.open(shift, ticket_id, lane, TWO_SEGMENTS)


def write(station: Station, attempt: Attempt, *pieces):
    return tuple(event for position, samples in enumerate(pieces)
                 for event in station.board.write_segment(attempt, position, samples))


def deliver(station: Station, events):
    return tuple(station.event(station.board.deliver_event(event.reference)) for event in events)


def capture(station: Station, attempt: Attempt, *pieces):
    deliver(station, write(station, attempt, *pieces))
    receipt = station.capture.receipts.for_attempt(attempt)
    if receipt is None:
        raise AssertionError(f'完整录制未接管 {attempt}')
    return receipt


def confirm(station: Station, attempt: Attempt):
    event = station.board.confirm_stop(attempt)
    if event is None:
        raise AssertionError(f'缺少本次尝试的停止确认 {attempt}')
    return event


def scenario(case_id, *, inputs, expected, actual, downstream_expected, downstream_actual, code_paths, oracle):
    print('POINTWISE_SCENARIO ' + json.dumps(
        dict(case_id=case_id, input=inputs, expected=expected, actual=actual,
             downstream_expected=downstream_expected, downstream_actual=downstream_actual,
             code_paths=code_paths, oracle=oracle), ensure_ascii=False, separators=(',', ':'),
    ))
