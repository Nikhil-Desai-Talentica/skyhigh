"""
Seat service infrastructure: Postgres and Redis implementations.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
from psycopg2 import errors
import redis

from .application import EventPublisher, KeyValueCache, SeatEvent, SeatRepository
from .domain import (
    Seat,
    SeatAlreadyAssigned,
    SeatAssignment,
    SeatAssignmentRepository,
    SeatState,
    WaitlistEntry,
    WaitlistRepository,
)


# ---------- PostgreSQL-backed repositories ----------


class PostgresSeatRepository(SeatRepository):
    """
    PostgreSQL implementation of SeatRepository.

    Expected schema:

    CREATE TABLE seats (
        flight_id TEXT NOT NULL,
        seat_id   TEXT NOT NULL,
        state     TEXT NOT NULL,
        held_by_passenger_id TEXT,
        held_at   TIMESTAMPTZ,
        confirmed_for_passenger_id TEXT,
        confirmed_at TIMESTAMPTZ,
        cancelled_at TIMESTAMPTZ,
        PRIMARY KEY (flight_id, seat_id)
    );
    """

    def __init__(self, conn: psycopg2.extensions.connection) -> None:
        self._conn = conn

    def get_seat(self, flight_id: str, seat_id: str) -> Optional[Seat]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT flight_id,
                       seat_id,
                       state,
                       held_by_passenger_id,
                       held_at,
                       confirmed_for_passenger_id,
                       confirmed_at,
                       cancelled_at
                  FROM seats
                 WHERE flight_id = %s AND seat_id = %s
                """,
                (flight_id, seat_id),
            )
            row = cur.fetchone()
            if row is None:
                return None

            (
                f_id,
                s_id,
                state_str,
                held_by,
                held_at,
                confirmed_for,
                confirmed_at,
                cancelled_at,
            ) = row

            state = SeatState[state_str]
            return Seat(
                flight_id=f_id,
                seat_id=s_id,
                state=state,
                held_by_passenger_id=held_by,
                held_at=held_at,
                confirmed_for_passenger_id=confirmed_for,
                confirmed_at=confirmed_at,
                cancelled_at=cancelled_at,
            )

    def save_seat(self, seat: Seat) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO seats (
                    flight_id,
                    seat_id,
                    state,
                    held_by_passenger_id,
                    held_at,
                    confirmed_for_passenger_id,
                    confirmed_at,
                    cancelled_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (flight_id, seat_id) DO UPDATE SET
                    state = EXCLUDED.state,
                    held_by_passenger_id = EXCLUDED.held_by_passenger_id,
                    held_at = EXCLUDED.held_at,
                    confirmed_for_passenger_id = EXCLUDED.confirmed_for_passenger_id,
                    confirmed_at = EXCLUDED.confirmed_at,
                    cancelled_at = EXCLUDED.cancelled_at
                """,
                (
                    seat.flight_id,
                    seat.seat_id,
                    seat.state.name,
                    seat.held_by_passenger_id,
                    seat.held_at,
                    seat.confirmed_for_passenger_id,
                    seat.confirmed_at,
                    seat.cancelled_at,
                ),
            )
        self._conn.commit()


class PostgresSeatAssignmentRepository(SeatAssignmentRepository):
    """
    PostgreSQL implementation of SeatAssignmentRepository.

    Expected schema:

    CREATE TABLE seat_assignments (
        flight_id   TEXT NOT NULL,
        seat_id     TEXT NOT NULL,
        passenger_id TEXT NOT NULL,
        assigned_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (flight_id, seat_id),
        UNIQUE (flight_id, seat_id)
    );
    """

    def __init__(self, conn: psycopg2.extensions.connection) -> None:
        self._conn = conn

    def assign_seat_if_available(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> SeatAssignment:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO seat_assignments (
                        flight_id,
                        seat_id,
                        passenger_id,
                        assigned_at
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (flight_id, seat_id, passenger_id, now),
                )
            self._conn.commit()
        except errors.UniqueViolation as exc:  # type: ignore[attribute-error]
            self._conn.rollback()
            raise SeatAlreadyAssigned(
                f"Seat {seat_id} on flight {flight_id} is already assigned."
            ) from exc

        return SeatAssignment(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            assigned_at=now,
        )

    def cancel_assignment(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,  # noqa: ARG002
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM seat_assignments
                 WHERE flight_id = %s
                   AND seat_id = %s
                   AND passenger_id = %s
                """,
                (flight_id, seat_id, passenger_id),
            )
        self._conn.commit()


# ---------- Redis-backed cache, waitlist, and events ----------


def _seat_to_dict(seat: Seat) -> dict[str, Any]:
    return {
        "flight_id": seat.flight_id,
        "seat_id": seat.seat_id,
        "state": seat.state.name,
        "held_by_passenger_id": seat.held_by_passenger_id,
        "held_at": seat.held_at.isoformat() if seat.held_at else None,
        "confirmed_for_passenger_id": seat.confirmed_for_passenger_id,
        "confirmed_at": seat.confirmed_at.isoformat() if seat.confirmed_at else None,
        "cancelled_at": seat.cancelled_at.isoformat() if seat.cancelled_at else None,
    }


def _dict_to_seat(data: dict[str, Any]) -> Seat:
    held_at = data.get("held_at")
    if held_at:
        held_at = datetime.fromisoformat(held_at)
        if held_at.tzinfo is None:
            held_at = held_at.replace(tzinfo=timezone.utc)
    confirmed_at = data.get("confirmed_at")
    if confirmed_at:
        confirmed_at = datetime.fromisoformat(confirmed_at)
        if confirmed_at.tzinfo is None:
            confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
    cancelled_at = data.get("cancelled_at")
    if cancelled_at:
        cancelled_at = datetime.fromisoformat(cancelled_at)
        if cancelled_at.tzinfo is None:
            cancelled_at = cancelled_at.replace(tzinfo=timezone.utc)
    return Seat(
        flight_id=data["flight_id"],
        seat_id=data["seat_id"],
        state=SeatState[data["state"]],
        held_by_passenger_id=data.get("held_by_passenger_id"),
        held_at=held_at,
        confirmed_for_passenger_id=data.get("confirmed_for_passenger_id"),
        confirmed_at=confirmed_at,
        cancelled_at=cancelled_at,
    )


class RedisKeyValueCache(KeyValueCache):
    """
    Redis-backed cache for Seat objects. Uses JSON serialization (no pickle)
    for security and interop.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def get(self, key: str) -> Optional[object]:
        raw = self._client.get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        if isinstance(data, dict) and "flight_id" in data and "seat_id" in data:
            return _dict_to_seat(data)
        return data

    def set(self, key: str, value: object, ttl_seconds: Optional[int] = None) -> None:
        if isinstance(value, Seat):
            data = json.dumps(_seat_to_dict(value))
        else:
            data = json.dumps(value)
        if ttl_seconds is None:
            self._client.set(key, data)
        else:
            self._client.set(key, data, ex=ttl_seconds)

    def delete(self, key: str) -> None:
        self._client.delete(key)


class RedisWaitlistRepository(WaitlistRepository):
    """
    Redis-backed waitlist using a FIFO list per (flight_id, seat_id).
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _key(self, flight_id: str, seat_id: str) -> str:
        return f"waitlist:{flight_id}:{seat_id}"

    def enqueue(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
        now: datetime,
    ) -> WaitlistEntry:
        entry = WaitlistEntry(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            joined_at=now,
        )
        payload = json.dumps(
            {
                "flight_id": entry.flight_id,
                "seat_id": entry.seat_id,
                "passenger_id": entry.passenger_id,
                "joined_at": entry.joined_at.isoformat(),
            }
        )
        self._client.rpush(self._key(flight_id, seat_id), payload)
        return entry

    def dequeue_next(self, flight_id: str, seat_id: str) -> Optional[WaitlistEntry]:
        payload = self._client.lpop(self._key(flight_id, seat_id))
        if payload is None:
            return None
        data = json.loads(payload)
        joined_at = datetime.fromisoformat(data["joined_at"])
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        return WaitlistEntry(
            flight_id=data["flight_id"],
            seat_id=data["seat_id"],
            passenger_id=data["passenger_id"],
            joined_at=joined_at,
        )


class RedisEventPublisher(EventPublisher):
    """
    Redis-based event publisher using a list as a simple queue.
    """

    def __init__(self, client: redis.Redis, key: str = "events:seat") -> None:
        self._client = client
        self._key = key

    def publish(self, event: SeatEvent) -> None:
        from dataclasses import asdict

        payload = {
            "type": type(event).__name__,
            **asdict(event),
        }
        for k, v in list(payload.items()):
            if isinstance(v, datetime):
                payload[k] = v.isoformat()
        self._client.rpush(self._key, json.dumps(payload))


def create_redis_client(url: str = "redis://localhost:6379/0") -> redis.Redis:
    return redis.from_url(url)


def create_postgres_connection(dsn: str) -> psycopg2.extensions.connection:
    return psycopg2.connect(dsn)
