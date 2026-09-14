"""Trusted deployment adapters. No concrete market-data or live-order client."""
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

    def submit(self, intent: dict) -> OrderUpdate:
        """Map stable client ID and LIMIT OPEN/CLOSE intent to broker semantics.

        Must enforce account funds, order limits and close-only owned quantity.
        Persist client-ID/broker-ID mapping; rejection must be authoritative.
        A transport timeout is UNKNOWN, never a guarantee of rejection.
        """
        ...

    def cancel(self, target_client_order_id: str, cancel_id: str) -> None:
        """Acknowledges cancel request ONLY; deliver original order terminal update separately."""
        ...

    def lookup(self, client_order_id: str) -> OrderUpdate | None:
        """Reconcile after restart/timeout; None does not authorize resubmission."""
        ...
