from device.descriptors import Attempt, RecordingSpec
from orders.dispatch import LaneDispatch
from orders.ledger import RegistrationLedger
from orders.lifecycle import Ticket


def open(
    ledger: RegistrationLedger, dispatch: LaneDispatch, shift: str,
    ticket_id: str, lane: str, spec: RecordingSpec,
) -> Ticket:
    if not isinstance(shift, str) or not shift.strip():
        raise ValueError('班次不能为空')
    first = Attempt(ticket_id, lane, 1)
    if not isinstance(spec, RecordingSpec):
        raise ValueError('工单需要有效的非空录制布局')
    registration = ledger.register(shift, first.ticket_id, first.lane)
    ticket = Ticket(registration, spec)
    dispatch.start_first(ticket)
    return ticket
