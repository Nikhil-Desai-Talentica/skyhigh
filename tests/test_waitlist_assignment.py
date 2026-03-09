from datetime import datetime, timezone

from skyhigh_core.domain import SeatAssignment, SeatAlreadyAssigned, WaitlistAssignmentService
from .inmemory_impl import InMemorySeatAssignmentRepository, InMemoryWaitlistRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_passengers_can_join_waitlist_when_seat_unavailable():
    assignment_repo = InMemorySeatAssignmentRepository()
    waitlist_repo = InMemoryWaitlistRepository()
    service = WaitlistAssignmentService(
        waitlist_repo=waitlist_repo,
        assignment_repo=assignment_repo,
    )

    # Seat already assigned to P1.
    assignment_repo.assign_seat_if_available(
        flight_id="F1", seat_id="20A", passenger_id="P1", now=_now()
    )

    # P2 and P3 join the waitlist.
    entry2 = service.join_waitlist(
        flight_id="F1", seat_id="20A", passenger_id="P2", now=_now()
    )
    entry3 = service.join_waitlist(
        flight_id="F1", seat_id="20A", passenger_id="P3", now=_now()
    )

    assert entry2.passenger_id == "P2"
    assert entry3.passenger_id == "P3"


def test_when_seat_frees_next_waitlisted_passenger_is_assigned():
    assignment_repo = InMemorySeatAssignmentRepository()
    waitlist_repo = InMemoryWaitlistRepository()
    service = WaitlistAssignmentService(
        waitlist_repo=waitlist_repo,
        assignment_repo=assignment_repo,
    )

    # Seat initially assigned to P1.
    assignment_repo.assign_seat_if_available(
        flight_id="F1", seat_id="21B", passenger_id="P1", now=_now()
    )

    # P2 and P3 join the waitlist in order.
    service.join_waitlist(
        flight_id="F1", seat_id="21B", passenger_id="P2", now=_now()
    )
    service.join_waitlist(
        flight_id="F1", seat_id="21B", passenger_id="P3", now=_now()
    )

    # P1 cancels; seat becomes free.
    assignment_repo.cancel_assignment(
        flight_id="F1", seat_id="21B", passenger_id="P1", now=_now()
    )

    # Auto-assign should give the seat to P2 (first in line).
    assignment = service.auto_assign_next(
        flight_id="F1", seat_id="21B", now=_now()
    )

    assert isinstance(assignment, SeatAssignment)
    assert assignment.passenger_id == "P2"
    assert assignment.seat_id == "21B"


def test_auto_assign_skips_if_seat_already_taken():
    assignment_repo = InMemorySeatAssignmentRepository()
    waitlist_repo = InMemoryWaitlistRepository()
    service = WaitlistAssignmentService(
        waitlist_repo=waitlist_repo,
        assignment_repo=assignment_repo,
    )

    # P2 and P3 join waitlist for 22C.
    service.join_waitlist(
        flight_id="F1", seat_id="22C", passenger_id="P2", now=_now()
    )
    service.join_waitlist(
        flight_id="F1", seat_id="22C", passenger_id="P3", now=_now()
    )

    # Before auto-assign runs, someone else (P9) manages to get the seat directly.
    assignment_repo.assign_seat_if_available(
        flight_id="F1", seat_id="22C", passenger_id="P9", now=_now()
    )

    # auto_assign_next should return None because seat is no longer available.
    assignment = service.auto_assign_next(
        flight_id="F1", seat_id="22C", now=_now()
    )

    assert assignment is None

