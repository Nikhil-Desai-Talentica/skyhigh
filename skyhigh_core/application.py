from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from .domain import (
    BaggageInfo,
    CheckInSession,
    CheckInSessionRepository,
    CheckInStatus,
    InvalidSeatTransition,
    PaymentService,
    Seat,
    SeatAssignment,
    SeatAssignmentRepository,
    SeatAlreadyAssigned,
    SeatLifecycleService,
    WaitlistAssignmentService,
    WeightService,
)


class SeatRepository(Protocol):
    """
    Persistence abstraction for Seat aggregates.

    A production implementation would back this with a relational database and
    appropriate indexing (e.g. by flight_id, seat_id).
    """

    def get_seat(self, flight_id: str, seat_id: str) -> Optional[Seat]:
        ...

    def save_seat(self, seat: Seat) -> None:
        ...


class KeyValueCache(Protocol):
    """
    Generic key-value cache abstraction (e.g. backed by Redis).
    """

    def get(self, key: str) -> Optional[object]:
        ...

    def set(self, key: str, value: object, ttl_seconds: Optional[int] = None) -> None:
        ...

    def delete(self, key: str) -> None:
        ...


class EventPublisher(Protocol):
    """
    Abstraction for publishing domain events onto a message bus.
    """

    def publish(self, event: "SeatEvent") -> None:
        ...


@dataclass(frozen=True)
class SeatEvent:
    flight_id: str
    seat_id: str
    passenger_id: Optional[str]
    occurred_at: datetime


@dataclass(frozen=True)
class SeatHeldEvent(SeatEvent):
    ...


@dataclass(frozen=True)
class SeatConfirmedEvent(SeatEvent):
    ...


@dataclass(frozen=True)
class SeatCancelledEvent(SeatEvent):
    ...


@dataclass(frozen=True)
class WaitlistSeatAssignedEvent(SeatEvent):
    ...


@dataclass(frozen=True)
class CheckInStatusChangedEvent:
    session_id: str
    passenger_id: str
    flight_id: str
    old_status: CheckInStatus
    new_status: CheckInStatus
    occurred_at: datetime


class SkyHighCoreService:
    """
    Application service that orchestrates seat holds, confirmations, cancellations,
    and waitlist-driven assignments using persistence, cache, and event publishing.
    """

    def __init__(
        self,
        seat_repo: SeatRepository,
        assignment_repo: SeatAssignmentRepository,
        waitlist_service: WaitlistAssignmentService,
        cache: Optional[KeyValueCache],
        events: EventPublisher,
        seat_lifecycle: Optional[SeatLifecycleService] = None,
        checkin_repo: Optional[CheckInSessionRepository] = None,
        weight_service: Optional[WeightService] = None,
        payment_service: Optional[PaymentService] = None,
    ) -> None:
        self._seat_repo = seat_repo
        self._assignment_repo = assignment_repo
        self._waitlist_service = waitlist_service
        self._cache = cache
        self._events = events
        self._seat_lifecycle = seat_lifecycle or SeatLifecycleService()
        self._checkin_repo = checkin_repo
        self._weight_service = weight_service
        self._payment_service = payment_service

    def _seat_cache_key(self, flight_id: str, seat_id: str) -> str:
        return f"seat:{flight_id}:{seat_id}"

    def _load_seat(self, flight_id: str, seat_id: str) -> Optional[Seat]:
        if self._cache is not None:
            cached = self._cache.get(self._seat_cache_key(flight_id, seat_id))
            if isinstance(cached, Seat):
                return cached
        seat = self._seat_repo.get_seat(flight_id, seat_id)
        if seat is not None and self._cache is not None:
            self._cache.set(self._seat_cache_key(flight_id, seat_id), seat)
        return seat

    def _save_seat(self, seat: Seat) -> None:
        self._seat_repo.save_seat(seat)
        if self._cache is not None:
            self._cache.set(self._seat_cache_key(seat.flight_id, seat.seat_id), seat)

    def hold_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> Seat:
        """
        Public API: attempt to place a 120-second hold on a seat.
        """
        seat = self._load_seat(flight_id, seat_id) or Seat(
            flight_id=flight_id,
            seat_id=seat_id,
        )

        self._seat_lifecycle.hold_seat(seat, passenger_id=passenger_id, now=now)
        self._save_seat(seat)
        self._events.publish(
            SeatHeldEvent(
                flight_id=flight_id,
                seat_id=seat_id,
                passenger_id=passenger_id,
                occurred_at=now,
            )
        )
        return seat

    def confirm_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> SeatAssignment:
        """
        Confirm a held seat and persist a conflict-free assignment.
        """
        seat = self._load_seat(flight_id, seat_id)
        if seat is None:
            raise InvalidSeatTransition("Seat does not exist.")

        self._seat_lifecycle.confirm_seat(seat, passenger_id=passenger_id, now=now)
        # Persist state change first.
        self._save_seat(seat)

        # Then persist authoritative assignment with conflict-free semantics.
        assignment = self._assignment_repo.assign_seat_if_available(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            now=now,
        )

        self._events.publish(
            SeatConfirmedEvent(
                flight_id=flight_id,
                seat_id=seat_id,
                passenger_id=passenger_id,
                occurred_at=now,
            )
        )
        return assignment

    def cancel_confirmed_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> None:
        """
        Cancel a confirmed seat, free the seat, and auto-assign to next
        waitlisted passenger if present.
        """
        seat = self._load_seat(flight_id, seat_id)
        if seat is None:
            return

        self._seat_lifecycle.cancel_seat(seat, passenger_id=passenger_id, now=now)
        self._save_seat(seat)

        # Cancel the authoritative assignment.
        self._assignment_repo.cancel_assignment(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            now=now,
        )

        self._events.publish(
            SeatCancelledEvent(
                flight_id=flight_id,
                seat_id=seat_id,
                passenger_id=passenger_id,
                occurred_at=now,
            )
        )

        # Offer the seat to the next waitlisted passenger, if any.
        try:
            new_assignment = self._waitlist_service.auto_assign_next(
                flight_id=flight_id,
                seat_id=seat_id,
                now=now,
            )
        except SeatAlreadyAssigned:
            new_assignment = None

        if new_assignment is not None:
            self._events.publish(
                WaitlistSeatAssignedEvent(
                    flight_id=new_assignment.flight_id,
                    seat_id=new_assignment.seat_id,
                    passenger_id=new_assignment.passenger_id,
                    occurred_at=now,
                )
            )

    # ---------- Baggage and payment orchestration ----------

    def add_baggage_and_validate(
        self,
        session_id: str,
        additional_weight_kg: float,
        now: datetime,
    ) -> CheckInSession:
        """
        Add baggage to a check-in session and determine whether payment is required.
        """
        if self._checkin_repo is None or self._weight_service is None:
            raise RuntimeError("Check-in repository and weight service must be configured.")

        session = self._checkin_repo.get(session_id)
        if session is None:
            raise ValueError(f"Unknown check-in session {session_id}")

        if session.status is CheckInStatus.COMPLETED:
            raise ValueError("Cannot modify baggage for completed check-in.")

        new_total = session.baggage.total_weight_kg + additional_weight_kg
        fee = self._weight_service.calculate_overweight_fee(new_total)

        session.baggage = BaggageInfo(
            total_weight_kg=new_total,
            overweight_fee_due=fee,
        )

        old_status = session.status
        if new_total > self._weight_service.MAX_WEIGHT_KG and fee > 0:
            session.status = CheckInStatus.WAITING_FOR_PAYMENT
        else:
            session.status = CheckInStatus.IN_PROGRESS

        self._checkin_repo.save(session)

        if old_status != session.status:
            self._events.publish(
                CheckInStatusChangedEvent(
                    session_id=session.session_id,
                    passenger_id=session.passenger_id,
                    flight_id=session.flight_id,
                    old_status=old_status,
                    new_status=session.status,
                    occurred_at=now,
                )
            )

        return session

    def process_baggage_payment(
        self,
        session_id: str,
        now: datetime,
    ) -> CheckInSession:
        """
        Charge any outstanding overweight baggage fee and move check-in out of
        the WAITING_FOR_PAYMENT state.
        """
        if self._checkin_repo is None or self._payment_service is None:
            raise RuntimeError("Check-in repository and payment service must be configured.")

        session = self._checkin_repo.get(session_id)
        if session is None:
            raise ValueError(f"Unknown check-in session {session_id}")

        if session.status is not CheckInStatus.WAITING_FOR_PAYMENT:
            return session

        if session.baggage.overweight_fee_due <= 0:
            session.status = CheckInStatus.IN_PROGRESS
            self._checkin_repo.save(session)
            return session

        payment_ref = self._payment_service.charge_overweight_fee(
            session=session,
            amount=session.baggage.overweight_fee_due,
        )
        session.payment_reference = payment_ref
        session.baggage.overweight_fee_due = 0.0

        old_status = session.status
        session.status = CheckInStatus.IN_PROGRESS
        self._checkin_repo.save(session)

        if old_status != session.status:
            self._events.publish(
                CheckInStatusChangedEvent(
                    session_id=session.session_id,
                    passenger_id=session.passenger_id,
                    flight_id=session.flight_id,
                    old_status=old_status,
                    new_status=session.status,
                    occurred_at=now,
                )
            )

        return session

