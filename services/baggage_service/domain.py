"""
Baggage service domain: baggage info, check-in sessions, weight and payment.
All baggage-related domain logic lives in this service for cohesion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Protocol


@dataclass
class BaggageInfo:
    total_weight_kg: float = 0.0
    overweight_fee_due: float = 0.0


class CheckInStatus(Enum):
    IN_PROGRESS = auto()
    WAITING_FOR_PAYMENT = auto()
    COMPLETED = auto()


@dataclass
class CheckInSession:
    """
    Represents a passenger's check-in session, including baggage and payment status.
    """

    session_id: str
    passenger_id: str
    flight_id: str
    created_at: datetime
    status: CheckInStatus = CheckInStatus.IN_PROGRESS
    baggage: BaggageInfo = field(default_factory=BaggageInfo)
    payment_reference: Optional[str] = None


class CheckInSessionRepository(Protocol):
    def get(self, session_id: str) -> Optional[CheckInSession]:
        ...

    def save(self, session: CheckInSession) -> None:
        ...


class WeightService(Protocol):
    """
    Service responsible for baggage weight validation and fee calculation.
    """

    MAX_WEIGHT_KG: float

    def calculate_overweight_fee(self, total_weight_kg: float) -> float:
        ...


class PaymentService(Protocol):
    def charge_overweight_fee(
        self,
        session: CheckInSession,
        amount: float,
    ) -> str:
        """Charge the given amount and return a payment reference."""
        ...
