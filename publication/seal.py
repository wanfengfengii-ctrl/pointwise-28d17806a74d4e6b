from capture.receipts import ReceiptBook
from orders.eligibility import authorize_seal
from orders.lifecycle import Ticket
from publication.artifact import SignedPair, create_artifact
from publication.store import PublicationStore


def seal(ticket: Ticket, receipt_id: str, receipts: ReceiptBook, store: PublicationStore) -> SignedPair:
    receipt = receipts.get(receipt_id)
    if receipt is None:
        raise ValueError('指定尝试没有完整实收')
    authorize_seal(ticket, receipt)
    artifact, success = create_artifact(receipt)
    store.commit(artifact, success)
    ticket.mark_signed(artifact.attempt, artifact.artifact_id)
    return artifact, success
