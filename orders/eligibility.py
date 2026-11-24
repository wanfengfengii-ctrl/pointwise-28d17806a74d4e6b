from capture.receipts import CaptureReceipt
from orders.lifecycle import Ticket, TicketState


class IneligibleReceipt(ValueError):
    pass


def authorize_seal(ticket: Ticket, receipt: CaptureReceipt) -> None:
    if (receipt.attempt.ticket_id, receipt.attempt.lane) != (ticket.first.ticket_id, ticket.first.lane):
        raise IneligibleReceipt('实收不属于该工单与测道')
    if (ticket.state not in (TicketState.FIRST_READY, TicketState.RETRY_READY)
            or ticket.eligible_attempt != receipt.attempt
            or ticket.eligible_receipt_id != receipt.receipt_id):
        raise IneligibleReceipt('指定完整实收无当前签发资格')
