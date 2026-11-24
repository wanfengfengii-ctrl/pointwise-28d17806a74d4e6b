from enum import Enum

from orders.dispatch import LaneDispatch
from orders.lifecycle import Ticket, TicketState


class RetryStatus(Enum):
    SCHEDULED = 'scheduled'
    WAITING_FOR_AREA = 'waiting_for_area'


def redo(ticket: Ticket, dispatch: LaneDispatch) -> RetryStatus:
    attempt = ticket.reserve_retry()
    dispatch.start_reserved_retry(ticket, attempt)
    if ticket.state is TicketState.RETRY_PENDING:
        return RetryStatus.SCHEDULED
    return RetryStatus.WAITING_FOR_AREA
