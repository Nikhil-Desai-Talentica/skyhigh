from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Protocol

from .domain import BaggageInfo


class ReservationStatus(Enum):
    IN_PROGRESS = auto()
    AWAITING_PAYMENT = auto()
    FAILED = auto()
    COMPLETED = auto()
    CANCELLED = auto()


@dataclass
class Reservation:
    """
    High-level reservation aggregate that ties together seat, baggage, and status.
    """

    reservation_id: str
    passenger_id: str
    flight_id: str
    seat_id: str
    created_at: datetime
    status: ReservationStatus = ReservationStatus.IN_PROGRESS
    baggage: BaggageInfo = BaggageInfo()
    overweight_fee: float = 0.0
    payment_status: str = "none"  # "none", "required", "pending", "approved", "rejected"
    hold_expires_at: Optional[datetime] = None


class ReservationRepository(Protocol):
    def get(self, reservation_id: str) -> Optional[Reservation]:
        ...

    def save(self, reservation: Reservation) -> None:
        ...

