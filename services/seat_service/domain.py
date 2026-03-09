"""
Seat service domain: seat lifecycle, assignments, waitlist.
All seat-related domain logic lives in this service for cohesion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Optional, Protocol


class SeatState(Enum):
    AVAILABLE = auto()
    HELD = auto()
    CONFIRMED = auto()
    CANCELLED = auto()


@dataclass
class Seat:
    flight_id: str
    seat_id: str
    state: SeatState = SeatState.AVAILABLE
    held_by_passenger_id: Optional[str] = None
    held_at: Optional[datetime] = None
    confirmed_for_passenger_id: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None


class InvalidSeatTransition(Exception):
    """
    Raised when a seat lifecycle operation violates business rules.
    """


class HoldExpiredException(Exception):
    """
    Raised when confirming a seat whose hold window has expired.
    """


class SeatLifecycleService:
    HOLD_TTL_SECONDS: int = 120

    def _expire_hold_if_needed(self, seat: Seat, now: datetime) -> None:
        """
        If the seat is HELD and the hold has exceeded the TTL, release it back to AVAILABLE.
        """
        if seat.state is not SeatState.HELD or seat.held_at is None:
            return

        if now >= seat.held_at + timedelta(seconds=self.HOLD_TTL_SECONDS):
            seat.state = SeatState.AVAILABLE
            seat.held_by_passenger_id = None
            seat.held_at = None

    def hold_seat(self, seat: Seat, passenger_id: str, now: datetime) -> None:
        """
        Transition seat from AVAILABLE -> HELD for a specific passenger.
        """
        self._expire_hold_if_needed(seat, now)

        if seat.state is not SeatState.AVAILABLE:
            raise InvalidSeatTransition("Seat must be AVAILABLE to be held.")

        seat.state = SeatState.HELD
        seat.held_by_passenger_id = passenger_id
        seat.held_at = now

    def confirm_seat(self, seat: Seat, passenger_id: str, now: datetime) -> None:
        """
        Transition seat from HELD -> CONFIRMED for the same passenger.
        Raises HoldExpiredException if this passenger's hold had expired.
        """
        was_held_by_this_passenger = (
            seat.state is SeatState.HELD and seat.held_by_passenger_id == passenger_id
        )
        self._expire_hold_if_needed(seat, now)

        if seat.state is not SeatState.HELD:
            if was_held_by_this_passenger:
                raise HoldExpiredException("Seat hold window has expired.")
            raise InvalidSeatTransition("Seat must be HELD to be confirmed.")

        if seat.held_by_passenger_id != passenger_id:
            raise InvalidSeatTransition(
                "Seat is held by a different passenger and cannot be confirmed."
            )

        seat.state = SeatState.CONFIRMED
        seat.confirmed_for_passenger_id = passenger_id
        seat.confirmed_at = now

    def cancel_seat(self, seat: Seat, passenger_id: str, now: datetime) -> None:
        """
        Transition seat from CONFIRMED -> CANCELLED by the confirming passenger.
        """
        if seat.state is not SeatState.CONFIRMED:
            raise InvalidSeatTransition("Only CONFIRMED seats can be cancelled.")

        if seat.confirmed_for_passenger_id != passenger_id:
            raise InvalidSeatTransition(
                "Only the confirming passenger can cancel this seat."
            )

        seat.state = SeatState.CANCELLED
        seat.cancelled_at = now


@dataclass(frozen=True)
class SeatAssignment:
    """
    Authoritative record that a particular seat on a flight belongs to a passenger.
    """

    flight_id: str
    seat_id: str
    passenger_id: str
    assigned_at: datetime


class SeatAlreadyAssigned(Exception):
    """
    Raised when attempting to assign a seat that is already assigned.
    """


class SeatAssignmentRepository(Protocol):
    """
    Abstraction for persisting seat assignments with conflict-free semantics.
    """

    def assign_seat_if_available(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> SeatAssignment:
        raise NotImplementedError

    def cancel_assignment(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class WaitlistEntry:
    """
    A passenger waiting for a specific seat on a flight.
    """

    flight_id: str
    seat_id: str
    passenger_id: str
    joined_at: datetime


class WaitlistRepository(Protocol):
    """
    Abstraction for managing waitlists for seats.
    """

    def enqueue(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> WaitlistEntry:
        raise NotImplementedError

    def dequeue_next(self, flight_id: str, seat_id: str) -> Optional[WaitlistEntry]:
        raise NotImplementedError


class WaitlistAssignmentService:
    """
    Coordinates waitlist enrollment and automatic assignment when seats free up.
    """

    def __init__(
        self,
        waitlist_repo: WaitlistRepository,
        assignment_repo: SeatAssignmentRepository,
    ) -> None:
        self._waitlist_repo = waitlist_repo
        self._assignment_repo = assignment_repo

    def join_waitlist(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> WaitlistEntry:
        return self._waitlist_repo.enqueue(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            now=now,
        )

    def auto_assign_next(
        self,
        flight_id: str,
        seat_id: str,
        now: datetime,
    ) -> Optional[SeatAssignment]:
        while True:
            entry = self._waitlist_repo.dequeue_next(flight_id, seat_id)
            if entry is None:
                return None

            try:
                assignment = self._assignment_repo.assign_seat_if_available(
                    flight_id=entry.flight_id,
                    seat_id=entry.seat_id,
                    passenger_id=entry.passenger_id,
                    now=now,
                )
                return assignment
            except SeatAlreadyAssigned:
                continue
