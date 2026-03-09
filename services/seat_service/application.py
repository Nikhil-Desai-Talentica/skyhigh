"""
Seat service application layer: orchestration, repositories, events.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from .domain import (
    HoldExpiredException,
    InvalidSeatTransition,
    Seat,
    SeatAlreadyAssigned,
    SeatAssignment,
    SeatAssignmentRepository,
    SeatLifecycleService,
    SeatState,
    WaitlistAssignmentService,
)


class SeatRepository(Protocol):
    def get_seat(self, flight_id: str, seat_id: str) -> Optional[Seat]:
        ...

    def save_seat(self, seat: Seat) -> None:
        ...


class KeyValueCache(Protocol):
    def get(self, key: str) -> Optional[object]:
        ...

    def set(self, key: str, value: object, ttl_seconds: Optional[int] = None) -> None:
        ...

    def delete(self, key: str) -> None:
        ...


class EventPublisher(Protocol):
    def publish(self, event: "SeatEvent") -> None:
        ...


# Event types (frozen DTOs for this service)


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
class HoldExpiredEvent(SeatEvent):
    """Emitted when a seat hold expires so listeners (e.g. reservation service) can mark reservation failed."""


class SeatOrchestrationService:
    """
    Orchestrates seat holds, confirmations, cancellations, and waitlist-driven
    assignments. Seat service owns all seat domain logic.
    """

    def __init__(
        self,
        seat_repo: SeatRepository,
        assignment_repo: SeatAssignmentRepository,
        waitlist_service: WaitlistAssignmentService,
        cache: Optional[KeyValueCache],
        events: EventPublisher,
        seat_lifecycle: Optional[SeatLifecycleService] = None,
    ) -> None:
        self._seat_repo = seat_repo
        self._assignment_repo = assignment_repo
        self._waitlist_service = waitlist_service
        self._cache = cache
        self._events = events
        self._seat_lifecycle = seat_lifecycle or SeatLifecycleService()

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

    def get_hold_status(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> dict[str, object]:
        """
        Check if the seat is still held for this passenger. Applies expiry logic;
        if the hold had expired, persists that and returns held=False, reason=expired.
        """
        seat = self._load_seat(flight_id, seat_id)
        if seat is None:
            return {"held": False, "reason": "not_held"}

        was_held_by_this = (
            seat.state is SeatState.HELD and seat.held_by_passenger_id == passenger_id
        )
        self._seat_lifecycle._expire_hold_if_needed(seat, now)

        if seat.state is SeatState.HELD:
            if seat.held_by_passenger_id == passenger_id:
                return {"held": True, "reason": "held"}
            return {"held": False, "reason": "held_by_other"}

        if was_held_by_this:
            self._save_seat(seat)
            return {"held": False, "reason": "expired"}

        if seat.state is SeatState.AVAILABLE:
            return {"held": False, "reason": "released"}
        return {"held": False, "reason": "released"}

    def join_waitlist(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> dict[str, object]:
        """Add a passenger to the waitlist for a particular seat."""
        entry = self._waitlist_service.join_waitlist(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            now=now,
        )
        return {
            "flight_id": entry.flight_id,
            "seat_id": entry.seat_id,
            "passenger_id": entry.passenger_id,
            "joined_at": entry.joined_at.isoformat(),
        }

    def hold_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> Seat:
        seat = self._load_seat(flight_id, seat_id) or Seat(
            flight_id=flight_id,
            seat_id=seat_id,
        )
        previous_holder = (
            seat.held_by_passenger_id
            if seat.state is SeatState.HELD
            else None
        )
        self._seat_lifecycle.hold_seat(seat, passenger_id=passenger_id, now=now)
        self._save_seat(seat)
        if (
            previous_holder is not None
            and previous_holder != passenger_id
        ):
            self._events.publish(
                HoldExpiredEvent(
                    flight_id=flight_id,
                    seat_id=seat_id,
                    passenger_id=previous_holder,
                    occurred_at=now,
                )
            )
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
        seat = self._load_seat(flight_id, seat_id)
        if seat is None:
            raise InvalidSeatTransition("Seat does not exist.")

        try:
            self._seat_lifecycle.confirm_seat(seat, passenger_id=passenger_id, now=now)
        except HoldExpiredException:
            self._save_seat(seat)
            self._events.publish(
                HoldExpiredEvent(
                    flight_id=flight_id,
                    seat_id=seat_id,
                    passenger_id=passenger_id,
                    occurred_at=now,
                )
            )
            raise

        self._save_seat(seat)

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
        seat = self._load_seat(flight_id, seat_id)
        if seat is None:
            return

        self._seat_lifecycle.cancel_seat(seat, passenger_id=passenger_id, now=now)
        self._save_seat(seat)

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
