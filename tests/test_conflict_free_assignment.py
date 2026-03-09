from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import List

from services.seat_service.domain import SeatAlreadyAssigned, SeatAssignment
from .inmemory_impl import InMemorySeatAssignmentRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_only_one_assignment_succeeds_for_same_seat_sequential():
    repo = InMemorySeatAssignmentRepository()

    assignment1 = repo.assign_seat_if_available(
        flight_id="F1", seat_id="12A", passenger_id="P1", now=_now()
    )

    assert isinstance(assignment1, SeatAssignment)

    try:
        repo.assign_seat_if_available(
            flight_id="F1", seat_id="12A", passenger_id="P2", now=_now()
        )
    except SeatAlreadyAssigned:
        pass
    else:
        raise AssertionError("Expected SeatAlreadyAssigned on second assignment")


def test_only_one_assignment_succeeds_for_same_seat_concurrent():
    repo = InMemorySeatAssignmentRepository()

    def attempt_assign(passenger_id: str):
        try:
            return repo.assign_seat_if_available(
                flight_id="F1", seat_id="12B", passenger_id=passenger_id, now=_now()
            )
        except SeatAlreadyAssigned:
            return None

    passenger_ids = [f"P{i}" for i in range(10)]
    results: List[SeatAssignment] = []

    with ThreadPoolExecutor(max_workers=len(passenger_ids)) as executor:
        futures = {executor.submit(attempt_assign, pid): pid for pid in passenger_ids}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

    # Exactly one passenger should have successfully obtained the seat.
    assert len(results) == 1
    assert results[0].seat_id == "12B"
    assert results[0].flight_id == "F1"


def test_cancelled_seat_can_be_reassigned():
    repo = InMemorySeatAssignmentRepository()

    # First passenger gets the seat.
    assignment1 = repo.assign_seat_if_available(
        flight_id="F1", seat_id="14C", passenger_id="P1", now=_now()
    )
    assert isinstance(assignment1, SeatAssignment)

    # Passenger cancels, freeing the seat.
    repo.cancel_assignment(
        flight_id="F1", seat_id="14C", passenger_id="P1", now=_now()
    )

    # Another passenger can now successfully obtain the same seat.
    assignment2 = repo.assign_seat_if_available(
        flight_id="F1", seat_id="14C", passenger_id="P2", now=_now()
    )

    assert assignment2.passenger_id == "P2"
    assert assignment2.flight_id == "F1"
    assert assignment2.seat_id == "14C"

