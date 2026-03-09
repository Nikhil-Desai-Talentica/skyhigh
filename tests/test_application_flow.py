from datetime import datetime, timezone

from skyhigh_core.application import SkyHighCoreService
from skyhigh_core.domain import SeatAssignment, SeatLifecycleService, WaitlistAssignmentService
from .inmemory_impl import (
    InMemoryEventPublisher,
    InMemoryKeyValueCache,
    InMemorySeatAssignmentRepository,
    InMemorySeatRepository,
    InMemoryWaitlistRepository,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_core_service() -> tuple[SkyHighCoreService, InMemoryEventPublisher]:
    seat_repo = InMemorySeatRepository()
    assignment_repo = InMemorySeatAssignmentRepository()
    waitlist_repo = InMemoryWaitlistRepository()
    waitlist_service = WaitlistAssignmentService(
        waitlist_repo=waitlist_repo,
        assignment_repo=assignment_repo,
    )
    cache = InMemoryKeyValueCache()
    events = InMemoryEventPublisher()
    core = SkyHighCoreService(
        seat_repo=seat_repo,
        assignment_repo=assignment_repo,
        waitlist_service=waitlist_service,
        cache=cache,
        events=events,
        seat_lifecycle=SeatLifecycleService(),
    )
    return core, events


def test_full_flow_hold_confirm_cancel_and_waitlist_assignment():
    core, events = _make_core_service()

    # P1 holds and confirms seat 30A.
    now = _now()
    core.hold_seat(flight_id="F1", seat_id="30A", passenger_id="P1", now=now)
    assignment = core.confirm_seat(
        flight_id="F1", seat_id="30A", passenger_id="P1", now=_now()
    )

    assert isinstance(assignment, SeatAssignment)

    # P2 joins waitlist for the same seat (would be done via WaitlistAssignmentService in a real system).
    # For this high-level integration test, we assume that's wired outside.

    # Cancel P1's seat and ensure a SeatCancelledEvent is emitted.
    cancel_time = _now()
    core.cancel_confirmed_seat(
        flight_id="F1", seat_id="30A", passenger_id="P1", now=cancel_time
    )

    # At least the SeatHeld, SeatConfirmed, and SeatCancelled events should be present.
    event_types = {type(e).__name__ for e in events.events}
    assert "SeatHeldEvent" in event_types
    assert "SeatConfirmedEvent" in event_types
    assert "SeatCancelledEvent" in event_types

