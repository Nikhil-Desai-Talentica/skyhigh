"""
Reservation service domain: reservation aggregate.
This service owns the reservation concept and coordinates with seat and baggage via HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Protocol


class ReservationStatus(Enum):
    IN_PROGRESS = auto()
    AWAITING_PAYMENT = auto()
    FAILED = auto()
    COMPLETED = auto()
    CANCELLED = auto()


@dataclass
class Reservation:
    """
    Client-facing reservation aggregate. Tracks seat hold, baggage, payment,
    and completion/cancellation. Orchestrates seat and baggage services.
    """

    reservation_id: str
    passenger_id: str
    flight_id: str
    seat_id: str
    created_at: datetime
    status: ReservationStatus = ReservationStatus.IN_PROGRESS
    baggage_total_kg: float = 0.0
    overweight_fee: float = 0.0
    hold_expires_at: Optional[datetime] = None
    # After completion, seat is confirmed in seat service; cancel releases it.


class ReservationRepository(Protocol):
    def get(self, reservation_id: str) -> Optional[Reservation]:
        ...

    def save(self, reservation: Reservation) -> None:
        ...

    def get_by_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
    ) -> Optional[Reservation]:
        """Find a reservation (IN_PROGRESS or AWAITING_PAYMENT) for this seat and passenger."""
        ...
