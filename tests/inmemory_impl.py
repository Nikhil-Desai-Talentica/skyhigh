"""
In-memory implementations for seat service (used by tests).
Imports from the seat microservice package for cohesion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Dict, List, Optional, Tuple

from services.seat_service.application import EventPublisher, KeyValueCache, SeatEvent, SeatRepository
from services.seat_service.domain import (
    Seat,
    SeatAlreadyAssigned,
    SeatAssignment,
    SeatAssignmentRepository,
    WaitlistEntry,
    WaitlistRepository,
)


class InMemoryEventPublisher(EventPublisher):
    def __init__(self) -> None:
        self.events: list[SeatEvent] = []

    def publish(self, event: SeatEvent) -> None:
        self.events.append(event)


class InMemorySeatRepository(SeatRepository):
    def __init__(self) -> None:
        self._seats: dict[tuple[str, str], Seat] = {}

    def get_seat(self, flight_id: str, seat_id: str) -> Optional[Seat]:
        return self._seats.get((flight_id, seat_id))

    def save_seat(self, seat: Seat) -> None:
        self._seats[(seat.flight_id, seat.seat_id)] = seat


class InMemoryKeyValueCache(KeyValueCache):
    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def get(self, key: str) -> Optional[object]:
        return self._store.get(key)

    def set(self, key: str, value: object, ttl_seconds: Optional[int] = None) -> None:  # noqa: ARG002
        self._store[key] = value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class InMemorySeatAssignmentRepository(SeatAssignmentRepository):
    def __init__(self) -> None:
        self._assignments: Dict[Tuple[str, str], SeatAssignment] = {}
        self._lock = Lock()

    def assign_seat_if_available(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> SeatAssignment:
        key = (flight_id, seat_id)
        with self._lock:
            if key in self._assignments:
                raise SeatAlreadyAssigned(
                    f"Seat {seat_id} on flight {flight_id} is already assigned."
                )

            assignment = SeatAssignment(
                flight_id=flight_id,
                seat_id=seat_id,
                passenger_id=passenger_id,
                assigned_at=now,
            )
            self._assignments[key] = assignment
            return assignment

    def cancel_assignment(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> None:
        key = (flight_id, seat_id)
        with self._lock:
            existing = self._assignments.get(key)
            if existing is None:
                return
            if existing.passenger_id != passenger_id:
                return
            del self._assignments[key]


@dataclass(frozen=True)
class InMemoryWaitlistEntry(WaitlistEntry):
    pass


class InMemoryWaitlistRepository(WaitlistRepository):
    def __init__(self) -> None:
        self._lists: Dict[Tuple[str, str], List[WaitlistEntry]] = {}
        self._lock = Lock()

    def enqueue(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> WaitlistEntry:
        entry = InMemoryWaitlistEntry(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            joined_at=now,
        )
        key = (flight_id, seat_id)
        with self._lock:
            self._lists.setdefault(key, []).append(entry)
        return entry

    def dequeue_next(self, flight_id: str, seat_id: str) -> Optional[WaitlistEntry]:
        key = (flight_id, seat_id)
        with self._lock:
            queue = self._lists.get(key)
            if not queue:
                return None
            return queue.pop(0)
