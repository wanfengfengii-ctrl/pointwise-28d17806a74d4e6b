from collections import deque

from capture.session import CaptureSession, CaptureUpdate
from device.descriptors import LANES, Attempt
from orders.ledger import RegistrationLedger
from orders.lifecycle import Ticket, TicketState


class LaneDispatch:
    def __init__(self, capture: CaptureSession, ledger: RegistrationLedger) -> None:
        self.capture = capture
        self.ledger = ledger
        self._queues: dict[str, deque[tuple[Ticket, Attempt]]] = {lane: deque() for lane in LANES}
        self._in_flight: dict[Attempt, Ticket] = {}

    def _enqueue(self, ticket: Ticket, attempt: Attempt) -> None:
        self._queues[attempt.lane].append((ticket, attempt))
        self.wake(attempt.lane)

    def start_first(self, ticket: Ticket) -> None:
        self._enqueue(ticket, ticket.first)

    def start_reserved_retry(self, ticket: Ticket, attempt: Attempt) -> None:
        if ticket.state is not TicketState.RETRY_RESERVED or ticket.retry_attempt != attempt:
            raise ValueError('尝试未占用本工单的补拍名额')
        self._enqueue(ticket, attempt)

    def expire_first(self, ticket: Ticket) -> bool:
        if not ticket.expire_first(self.ledger):
            return False
        self.capture.request_stop(ticket.first)
        self.wake(ticket.first.lane)
        return True

    def on_capture(self, update: CaptureUpdate) -> None:
        if update.receipt is not None:
            self._in_flight[update.attempt].accept_handoff(update.receipt)
        if update.area_released:
            del self._in_flight[update.attempt]
            self.wake(update.attempt.lane)

    def wake(self, lane: str) -> None:
        queue = self._queues[lane]
        while queue:
            ticket, attempt = queue[0]
            expected = TicketState.FIRST_PENDING if attempt.number == 1 else TicketState.RETRY_RESERVED
            if ticket.state is not expected:
                queue.popleft()
                continue
            if not self.capture.submit(attempt, ticket.spec):
                return
            queue.popleft()
            self._in_flight[attempt] = ticket
            if attempt.number == 2:
                ticket.mark_retry_started(attempt)
