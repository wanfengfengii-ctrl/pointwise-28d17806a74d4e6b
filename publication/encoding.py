import json
from dataclasses import asdict

from capture.segments import SegmentEvidence
from device.descriptors import Attempt
from publication.artifact import Artifact, SuccessReceipt


def encode_artifact(artifact: Artifact) -> str:
    return json.dumps(asdict(artifact), ensure_ascii=False, separators=(',', ':'))


def decode_artifact(encoded: str) -> Artifact:
    fields = json.loads(encoded)
    fields['attempt'] = Attempt(**fields['attempt'])
    fields['samples'] = tuple(fields['samples'])
    segments = []
    for segment in fields['segments']:
        segment['attempt'] = Attempt(**segment['attempt'])
        segments.append(SegmentEvidence(**segment))
    fields['segments'] = tuple(segments)
    return Artifact(**fields)


def encode_success_receipt(receipt: SuccessReceipt) -> str:
    return json.dumps(asdict(receipt), ensure_ascii=False, separators=(',', ':'))


def decode_success_receipt(encoded: str) -> SuccessReceipt:
    fields = json.loads(encoded)
    fields['attempt'] = Attempt(**fields['attempt'])
    return SuccessReceipt(**fields)
