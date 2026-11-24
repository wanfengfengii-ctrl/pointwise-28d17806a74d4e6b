from dataclasses import dataclass, field
from enum import Enum

from capture.receipts import CaptureReceipt
from device.descriptors import Attempt, RecordingSpec
from orders.ledger import RegistrationEntry, RegistrationLedger


class TicketState(Enum):
    FIRST_PENDING = 'first_pending'
    FIRST_READY = 'first_ready'
    FIRST_EXPIRED = 'first_expired'
    RETRY_RESERVED = 'retry_reserved'
    RETRY_PENDING = 'retry_pending'
    RETRY_READY = 'retry_ready'
    SIGNED = 'signed'


@dataclass
class Ticket:
    registration: RegistrationEntry
    spec: RecordingSpec
    state: TicketState = field(default=TicketState.FIRST_PENDING, init=False)
    retry_attempt: Attempt | None = field(default=None, init=False)
    eligible_attempt: Attempt | None = field(default=None, init=False)
    eligible_receipt_id: str | None = field(default=None, init=False)
    artifact_id: str | None = field(default=None, init=False)
    first: Attempt = field(init=False)

    def __post_init__(self) -> None:
        self.first = Attempt(self.registration.ticket_id, self.registration.lane, 1)

    def accept_handoff(self, receipt: CaptureReceipt) -> bool:
        if self.state is TicketState.FIRST_PENDING and receipt.attempt == self.first:
            next_state = TicketState.FIRST_READY
        elif self.state is TicketState.RETRY_PENDING and receipt.attempt == self.retry_attempt:
            next_state = TicketState.RETRY_READY
        else:
            return False
        self.eligible_attempt = receipt.attempt
        self.eligible_receipt_id = receipt.receipt_id
        self.state = next_state
        return True

    def expire_first(self, ledger: RegistrationLedger) -> bool:
        if self.state is not TicketState.FIRST_PENDING:
            return False
        self.registration = ledger.record_first_timeout(self.first.ticket_id)
        self.state = TicketState.FIRST_EXPIRED
        return True

    def reserve_retry(self) -> Attempt:
        if self.state is not TicketState.FIRST_EXPIRED:
            raise ValueError('工单当前不可安排补拍')
        self.retry_attempt = Attempt(self.first.ticket_id, self.first.lane, 2)
        self.state = TicketState.RETRY_RESERVED
        return self.retry_attempt

    def mark_retry_started(self, attempt: Attempt) -> None:
        if self.state is not TicketState.RETRY_RESERVED or self.retry_attempt != attempt:
            raise ValueError('板卡接受结果与补拍预留不一致')
        self.state = TicketState.RETRY_PENDING

    def mark_signed(self, attempt: Attempt, artifact_id: str) -> None:
        if (self.state not in (TicketState.FIRST_READY, TicketState.RETRY_READY)
                or self.eligible_attempt != attempt):
            raise ValueError('提交事实与工单当前可签尝试不一致')
        self.artifact_id = artifact_id
        self.state = TicketState.SIGNED
