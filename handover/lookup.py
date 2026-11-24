from publication.artifact import SignedPair
from publication.store import PublicationStore


class MissingArtifact(LookupError):
    pass


def get(store: PublicationStore, artifact_id: str) -> SignedPair:
    pair = store.get(artifact_id)
    if pair is None:
        raise MissingArtifact(artifact_id)
    return pair
