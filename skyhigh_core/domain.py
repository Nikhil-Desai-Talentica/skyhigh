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
        # First, automatically release any expired hold.
        self._expire_hold_if_needed(seat, now)

        if seat.state is not SeatState.AVAILABLE:
            raise InvalidSeatTransition("Seat must be AVAILABLE to be held.")

        seat.state = SeatState.HELD
        seat.held_by_passenger_id = passenger_id
        seat.held_at = now

    def confirm_seat(self, seat: Seat, passenger_id: str, now: datetime) -> None:
        """
        Transition seat from HELD -> CONFIRMED for the same passenger.
        """
        # Expire any stale holds before confirming.
        self._expire_hold_if_needed(seat, now)

        if seat.state is not SeatState.HELD:
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


class SeatAssignmentRepository:
    """
    Abstraction for persisting seat assignments with conflict-free semantics.

    A real implementation should use a database with a UNIQUE constraint on
    (flight_id, seat_id) and rely on an atomic INSERT/UPSERT to guarantee that
    only one assignment can succeed even under concurrency.
    """

    def assign_seat_if_available(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> SeatAssignment:
        """
        Atomically assign the seat to the passenger if and only if it is not
        already assigned. Returns the created assignment, or raises
        SeatAlreadyAssigned on conflict.
        """
        raise NotImplementedError

    def cancel_assignment(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> None:
        """
        Cancel an existing seat assignment, freeing the seat so that it can be
        assigned again or offered to a waitlisted passenger.

        A real implementation would typically:
        - validate that the assignment exists for this passenger
        - delete or mark the assignment as cancelled
        - emit a domain event such as SeatCancelled
        """
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


class WaitlistRepository:
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
        """
        Returns and removes the next eligible waitlisted passenger for the given
        seat, or None if the waitlist is empty.
        """
        raise NotImplementedError


class WaitlistAssignmentService:
    """
    Coordinates waitlist enrollment and automatic assignment when seats free up.

    In a real deployment, this would typically be triggered by events such as
    SeatCancelled or SeatHoldExpired and would also publish notifications when
    a waitlisted seat is assigned.
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
        """
        Add a passenger to the waitlist for a particular seat.
        """
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
        """
        When a seat becomes available, assign it to the next eligible waitlisted
        passenger, if any. Returns the created assignment or None if there is no
        one to assign or if the seat is already taken.
        """
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
                # In a real system, we would also trigger a notification here.
                return assignment
            except SeatAlreadyAssigned:
                # Seat was taken (e.g., directly selected by someone else)
                # before we could assign it to this waitlisted passenger.
                # Continue to the next entry, if any.
                continue


class CheckInStatus(Enum):
    IN_PROGRESS = auto()
    WAITING_FOR_PAYMENT = auto()
    COMPLETED = auto()


@dataclass
class BaggageInfo:
    total_weight_kg: float = 0.0
    overweight_fee_due: float = 0.0


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
    baggage: BaggageInfo = BaggageInfo()
    payment_reference: Optional[str] = None


class CheckInSessionRepository:
    """
    Persistence abstraction for check-in sessions.
    """

    def get(self, session_id: str) -> Optional[CheckInSession]:
        raise NotImplementedError

    def save(self, session: CheckInSession) -> None:
        raise NotImplementedError


class WeightService(Protocol):
    """
    External service responsible for baggage weight validation and fee calculation.
    """

    MAX_WEIGHT_KG: float

    def calculate_overweight_fee(self, total_weight_kg: float) -> float:
        ...


class PaymentService(Protocol):
    """
    Abstraction for payment processing related to baggage and check-in.
    """

    def charge_overweight_fee(
        self,
        session: CheckInSession,
        amount: float,
    ) -> str:
        """
        Charge the given amount and return a payment reference.
        """
        ...


