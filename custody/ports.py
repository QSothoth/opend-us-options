"""Trusted deployment adapters. The OpenD implementations live in custody.opend and custody.broker."""
from typing import Protocol
from .models import Contract, Session, OrderUpdate


class ContractResolver(Protocol):
    def resolve(self, code: str) -> Contract:
        """Resolve broker instrument metadata; do not infer from arbitrary text."""
        ...


class TradingCalendar(Protocol):
    def session(self, day: str) -> Session:
        """Return actual exchange session including holidays and early closes."""
        ...


class Broker(Protocol):
    account: str
    mode: str

    def submit(self, intent: dict, now) -> OrderUpdate:
        """Send one LIMIT OPEN/CLOSE intent; the stable client ID must be recoverable from the broker.

        An id the broker already holds returns that order instead of a new one. Must enforce
        close-only owned quantity. A returned rejection must be authoritative (nothing was
        sent); an unknown outcome must raise, never be reported as rejected.
        """
        ...

    def cancel(self, target_client_order_id: str, cancel_id: str) -> None:
        """Acknowledges cancel request ONLY; deliver original order terminal update separately."""
        ...

    def lookup(self, order: dict, now) -> OrderUpdate | None:
        """Current state of a dispatched order; None does not authorize resubmission.

        Re-observing an unchanged order must repeat its sequence and broker facts.
        """
        ...
