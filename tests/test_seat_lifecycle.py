from datetime import datetime, timedelta, timezone

from skyhigh_core.domain import (
    InvalidSeatTransition,
    Seat,
    SeatLifecycleService,
    SeatState,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_seat_can_be_held_only_when_available():
    service = SeatLifecycleService()
    seat = Seat(flight_id="F1", seat_id="12A", state=SeatState.AVAILABLE)

    service.hold_seat(seat, passenger_id="P1", now=_now())

    assert seat.state == SeatState.HELD
    assert seat.held_by_passenger_id == "P1"


def test_cannot_hold_non_available_seat():
    service = SeatLifecycleService()
    seat = Seat(flight_id="F1", seat_id="12A", state=SeatState.HELD)

    try:
        service.hold_seat(seat, passenger_id="P2", now=_now())
    except InvalidSeatTransition:
        pass
    else:
        raise AssertionError("Expected InvalidSeatTransition for non-AVAILABLE seat")


def test_only_holding_passenger_can_confirm_seat():
    service = SeatLifecycleService()
    seat = Seat(flight_id="F1", seat_id="12A", state=SeatState.HELD, held_by_passenger_id="P1")

    service.confirm_seat(seat, passenger_id="P1", now=_now())

    assert seat.state == SeatState.CONFIRMED
    assert seat.confirmed_for_passenger_id == "P1"


def test_cannot_confirm_if_not_held_or_by_different_passenger():
    service = SeatLifecycleService()
    # Not HELD
    seat_not_held = Seat(flight_id="F1", seat_id="12A", state=SeatState.AVAILABLE)
    try:
        service.confirm_seat(seat_not_held, passenger_id="P1", now=_now())
    except InvalidSeatTransition:
        pass
    else:
        raise AssertionError("Expected InvalidSeatTransition when seat is not HELD")

    # HELD by different passenger
    seat_other = Seat(flight_id="F1", seat_id="12B", state=SeatState.HELD, held_by_passenger_id="P2")
    try:
        service.confirm_seat(seat_other, passenger_id="P1", now=_now())
    except InvalidSeatTransition:
        pass
    else:
        raise AssertionError("Expected InvalidSeatTransition when held by someone else")


def test_confirmed_seat_can_be_cancelled_by_same_passenger():
    service = SeatLifecycleService()
    seat = Seat(
        flight_id="F1",
        seat_id="12A",
        state=SeatState.CONFIRMED,
        confirmed_for_passenger_id="P1",
    )

    service.cancel_seat(seat, passenger_id="P1", now=_now())

    assert seat.state == SeatState.CANCELLED
    assert seat.cancelled_at is not None


def test_cannot_cancel_unconfirmed_or_other_passengers_seat():
    service = SeatLifecycleService()
    unconfirmed = Seat(flight_id="F1", seat_id="12A", state=SeatState.AVAILABLE)

    try:
        service.cancel_seat(unconfirmed, passenger_id="P1", now=_now())
    except InvalidSeatTransition:
        pass
    else:
        raise AssertionError("Expected InvalidSeatTransition when not CONFIRMED")

    confirmed_other = Seat(
        flight_id="F1",
        seat_id="12B",
        state=SeatState.CONFIRMED,
        confirmed_for_passenger_id="P2",
    )

    try:
        service.cancel_seat(confirmed_other, passenger_id="P1", now=_now())
    except InvalidSeatTransition:
        pass
    else:
        raise AssertionError(
            "Expected InvalidSeatTransition when cancelling another passenger's seat"
        )


def test_cannot_hold_seat_while_hold_active_for_other_passenger():
    service = SeatLifecycleService()
    held_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    seat = Seat(
        flight_id="F1",
        seat_id="12C",
        state=SeatState.HELD,
        held_by_passenger_id="P1",
        held_at=held_at,
    )

    # Within TTL window, another passenger cannot take the seat.
    now_within_ttl = held_at + timedelta(seconds=60)
    try:
        service.hold_seat(seat, passenger_id="P2", now=now_within_ttl)
    except InvalidSeatTransition:
        pass
    else:
        raise AssertionError("Expected InvalidSeatTransition while hold is still active")


def test_expired_hold_allows_new_passenger_to_hold():
    service = SeatLifecycleService()
    held_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    seat = Seat(
        flight_id="F1",
        seat_id="12D",
        state=SeatState.HELD,
        held_by_passenger_id="P1",
        held_at=held_at,
    )

    # After TTL (120 seconds), hold should auto-expire and new passenger can hold.
    now_after_ttl = held_at + timedelta(seconds=121)
    service.hold_seat(seat, passenger_id="P2", now=now_after_ttl)

    assert seat.state == SeatState.HELD
    assert seat.held_by_passenger_id == "P2"


def test_cannot_confirm_after_hold_has_expired():
    service = SeatLifecycleService()
    held_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    seat = Seat(
        flight_id="F1",
        seat_id="12E",
        state=SeatState.HELD,
        held_by_passenger_id="P1",
        held_at=held_at,
    )

    now_after_ttl = held_at + timedelta(seconds=121)

    try:
        service.confirm_seat(seat, passenger_id="P1", now=now_after_ttl)
    except InvalidSeatTransition:
        # Confirm should fail and the seat should be made AVAILABLE again.
        assert seat.state == SeatState.AVAILABLE
        assert seat.held_by_passenger_id is None
        assert seat.held_at is None
    else:
        raise AssertionError("Expected InvalidSeatTransition after hold expiry")


