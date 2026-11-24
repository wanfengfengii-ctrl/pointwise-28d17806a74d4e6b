import json

from capture.receipts import receipt_id_for
from device.descriptors import RecordingSpec, SegmentSpec
from station import Station


SEGMENTS = (("head", (7, 3)), ("overlap", (11,)), ("tail", (-17,)))


def shared_input():
    layout = RecordingSpec(tuple(SegmentSpec(name, position, len(samples))
                                 for position, (name, samples) in enumerate(SEGMENTS)))
    return "day-shared", "load-41", "A", layout, {"X": (0, 2), "Y": (1, 3)}


def issue(station, recording, position):
    return station.board.write_segment(recording, position, SEGMENTS[position][1])


def deliver(station, *notifications):
    return tuple(station.event(station.board.deliver_event(notice.reference))
                 for notice in notifications)


def main():
    shift, _, lane, layout, _ = shared_input()
    with Station(":memory:", areas_per_lane=1) as station:
        work = station.open(shift, "ordinary-fixture", lane, layout)
        head, = issue(station, work.first, 0)
        overlap, = issue(station, work.first, 1)
        tail, end = issue(station, work.first, 2)
        deliver(station, end, tail, head)
        if station.capture.receipts.for_attempt(work.first) is not None:
            raise SystemExit("扣住的原段通知尚未投递，普通首拍不能实收")
        deliver(station, overlap)
        artifact, receipt = station.seal(work.first.ticket_id, receipt_id_for(work.first))
        if (artifact.samples, artifact.peak) != ((7, 3, 11, -17), 17):
            raise SystemExit("夹具投递的普通首拍与签件不一致")
        print(json.dumps({"samples": artifact.samples, "peak": artifact.peak,
                          "receipt": receipt.receipt_id, "references": [s.reference for s in artifact.segments]}))


if __name__ == "__main__":
    main()
