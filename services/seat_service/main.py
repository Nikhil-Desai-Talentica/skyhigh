from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from .application import SeatOrchestrationService
from .domain import SeatLifecycleService, WaitlistAssignmentService
from .domain import HoldExpiredException
from .infrastructure import (
    PostgresSeatAssignmentRepository,
    PostgresSeatRepository,
    RedisEventPublisher,
    RedisKeyValueCache,
    RedisWaitlistRepository,
    create_postgres_connection,
    create_redis_client,
)

app = FastAPI(title="Seat Service")


def get_seat_service() -> SeatOrchestrationService:
    import os
    db_url = os.environ.get("DATABASE_URL", "postgres://postgres:postgres@postgres:5432/skyhigh")
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    pg_conn = create_postgres_connection(db_url)
    redis_client = create_redis_client(redis_url)

    seat_repo = PostgresSeatRepository(pg_conn)
    assignment_repo = PostgresSeatAssignmentRepository(pg_conn)
    waitlist_repo = RedisWaitlistRepository(redis_client)
    waitlist_service = WaitlistAssignmentService(
        waitlist_repo=waitlist_repo,
        assignment_repo=assignment_repo,
    )
    cache = RedisKeyValueCache(redis_client)
    events = RedisEventPublisher(redis_client, key="events:seat")

    return SeatOrchestrationService(
        seat_repo=seat_repo,
        assignment_repo=assignment_repo,
        waitlist_service=waitlist_service,
        cache=cache,
        events=events,
        seat_lifecycle=SeatLifecycleService(),
    )


CoreDep = Annotated[SeatOrchestrationService, Depends(get_seat_service)]


@app.get("/seats/{flight_id}/{seat_id}/hold-status")
def get_hold_status(flight_id: str, seat_id: str, passenger_id: str, core: CoreDep):
    """Return whether the seat is still held for this passenger (applies expiry logic)."""
    status = core.get_hold_status(
        flight_id=flight_id,
        seat_id=seat_id,
        passenger_id=passenger_id,
        now=datetime.now(timezone.utc),
    )
    return status


@app.post("/seats/{flight_id}/{seat_id}/waitlist")
def join_waitlist(flight_id: str, seat_id: str, passenger_id: str, core: CoreDep):
    """Add a passenger to the waitlist for this seat."""
    entry = core.join_waitlist(
        flight_id=flight_id,
        seat_id=seat_id,
        passenger_id=passenger_id,
        now=datetime.now(timezone.utc),
    )
    return entry


@app.post("/seats/{flight_id}/{seat_id}/hold")
def hold_seat(flight_id: str, seat_id: str, passenger_id: str, core: CoreDep):
    try:
        seat = core.hold_seat(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            now=datetime.now(timezone.utc),
        )
        return {"flightId": seat.flight_id, "seatId": seat.seat_id, "state": seat.state.name}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/seats/{flight_id}/{seat_id}/confirm")
def confirm_seat(flight_id: str, seat_id: str, passenger_id: str, core: CoreDep):
    try:
        assignment = core.confirm_seat(
            flight_id=flight_id,
            seat_id=seat_id,
            passenger_id=passenger_id,
            now=datetime.now(timezone.utc),
        )
        return {
            "flightId": assignment.flight_id,
            "seatId": assignment.seat_id,
            "passengerId": assignment.passenger_id,
        }
    except HoldExpiredException as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/seats/{flight_id}/{seat_id}/cancel")
def cancel_seat(flight_id: str, seat_id: str, passenger_id: str, core: CoreDep):
    core.cancel_confirmed_seat(
        flight_id=flight_id,
        seat_id=seat_id,
        passenger_id=passenger_id,
        now=datetime.now(timezone.utc),
    )
    return {"status": "ok"}
