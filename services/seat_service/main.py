from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from skyhigh_core.application import SkyHighCoreService
from skyhigh_core.domain import SeatLifecycleService, WaitlistAssignmentService
from skyhigh_core.infrastructure import (
    PostgresSeatAssignmentRepository,
    PostgresSeatRepository,
    RedisKeyValueCache,
    RedisWaitlistRepository,
    create_postgres_connection,
    create_redis_client,
)

app = FastAPI(title="Seat Service")


def get_core_service() -> SkyHighCoreService:
    # In a real setup, these DSNs would come from environment variables.
    pg_conn = create_postgres_connection("postgres://postgres:postgres@postgres:5432/skyhigh")
    redis_client = create_redis_client("redis://redis:6379/0")

    seat_repo = PostgresSeatRepository(pg_conn)
    assignment_repo = PostgresSeatAssignmentRepository(pg_conn)
    waitlist_repo = RedisWaitlistRepository(redis_client)
    waitlist_service = WaitlistAssignmentService(
        waitlist_repo=waitlist_repo,
        assignment_repo=assignment_repo,
    )
    cache = RedisKeyValueCache(redis_client)

    # Event publishing is left as a future enhancement; pass a no-op lambda for now.
    class NoOpEvents:
        def publish(self, event) -> None:  # type: ignore[no-untyped-def]
            return

    events = NoOpEvents()

    return SkyHighCoreService(
        seat_repo=seat_repo,
        assignment_repo=assignment_repo,
        waitlist_service=waitlist_service,
        cache=cache,
        events=events,
        seat_lifecycle=SeatLifecycleService(),
    )


CoreDep = Annotated[SkyHighCoreService, Depends(get_core_service)]


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

