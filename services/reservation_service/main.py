"""
Reservation service: client-facing API.
Orchestrates seat (hold/confirm/cancel) and baggage (quote) services.
Listens for HoldExpiredEvent from seat service and marks reservations FAILED.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .abuse import AbuseDetector, get_client_id
from .clients import BaggageServiceClient, SeatServiceClient
from .domain import Reservation, ReservationRepository, ReservationStatus
from .events import start_hold_expired_listener

logging.basicConfig(level=logging.INFO)


class CompleteRequest(BaseModel):
    """Pay=true → COMPLETED (seat confirmed); pay=false → FAILED (abandoned)."""
    pay: bool


class JoinWaitlistRequest(BaseModel):
    """Request to be added to the waitlist for a particular seat."""
    flightId: str
    passengerId: str
    seatId: str


class InMemoryReservationRepository(ReservationRepository):
    def __init__(self) -> None:
        self._items: Dict[str, Reservation] = {}

    def get(self, reservation_id: str) -> Optional[Reservation]:
        return self._items.get(reservation_id)

    def save(self, reservation: Reservation) -> None:
        self._items[reservation.reservation_id] = reservation

    def get_by_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
    ) -> Optional[Reservation]:
        for r in self._items.values():
            if (
                r.flight_id == flight_id
                and r.seat_id == seat_id
                and r.passenger_id == passenger_id
                and r.status in (ReservationStatus.IN_PROGRESS, ReservationStatus.AWAITING_PAYMENT)
            ):
                return r
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.repo = repo
    app.state.abuse_detector = AbuseDetector()
    start_hold_expired_listener(repo)
    yield
    # Listener thread is daemon; no explicit stop needed


app = FastAPI(
    title="Reservation Service",
    description="Client-facing API: create reservation (hold seat), add baggage, pay or abandon, cancel completed.",
    lifespan=lifespan,
)

repo = InMemoryReservationRepository()
_seat_client: Optional[SeatServiceClient] = None
_baggage_client: Optional[BaggageServiceClient] = None


def get_repo() -> ReservationRepository:
    return repo


def get_seat_client() -> SeatServiceClient:
    global _seat_client
    if _seat_client is None:
        _seat_client = SeatServiceClient()
    return _seat_client


def get_baggage_client() -> BaggageServiceClient:
    global _baggage_client
    if _baggage_client is None:
        _baggage_client = BaggageServiceClient()
    return _baggage_client


RepoDep = Annotated[ReservationRepository, Depends(get_repo)]
SeatClientDep = Annotated[SeatServiceClient, Depends(get_seat_client)]
BaggageClientDep = Annotated[BaggageServiceClient, Depends(get_baggage_client)]


def require_seat_access_not_abused(request: Request) -> None:
    """
    Dependency for seat-related endpoints: reject blocked clients (429),
    otherwise record this request for rate limiting.
    """
    detector = request.app.state.abuse_detector
    client_id = get_client_id(
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-real-ip"),
        request.client.host if request.client else None,
    )
    if detector.is_blocked(client_id):
        retry_after = detector.block_remaining_seconds(client_id)
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many requests. Access to seat availability has been temporarily blocked "
                "to prevent abuse. Please try again later."
            ),
            headers={"Retry-After": str(max(1, retry_after))},
        )
    detector.record_seat_access(client_id)


SeatAccessDep = Annotated[None, Depends(require_seat_access_not_abused)]


# ---------- 1. Make a new reservation (holds a seat) ----------


@app.post("/reservations")
def create_reservation(
    flightId: str,
    passengerId: str,
    seatId: str,
    _abuse: SeatAccessDep,
    repo: RepoDep,
    seat_client: SeatClientDep,
):
    """Create a new reservation; holds the seat for a limited time."""
    now = datetime.now(timezone.utc)
    reservation_id = f"R-{int(now.timestamp())}-{passengerId}"

    try:
        seat_client.hold_seat(
            flight_id=flightId,
            seat_id=seatId,
            passenger_id=passengerId,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Seat hold failed: {exc!s}") from exc

    hold_expires_at = now + timedelta(seconds=120)
    reservation = Reservation(
        reservation_id=reservation_id,
        passenger_id=passengerId,
        flight_id=flightId,
        seat_id=seatId,
        created_at=now,
        status=ReservationStatus.IN_PROGRESS,
        hold_expires_at=hold_expires_at,
    )
    repo.save(reservation)
    return {
        "reservationId": reservation.reservation_id,
        "status": reservation.status.name,
        "holdExpiresAt": hold_expires_at.isoformat(),
    }


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str, repo: RepoDep):
    """Get reservation details."""
    reservation = repo.get(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return {
        "reservationId": reservation.reservation_id,
        "passengerId": reservation.passenger_id,
        "flightId": reservation.flight_id,
        "seatId": reservation.seat_id,
        "status": reservation.status.name,
        "baggageTotalKg": reservation.baggage_total_kg,
        "overweightFee": reservation.overweight_fee,
        "holdExpiresAt": reservation.hold_expires_at.isoformat() if reservation.hold_expires_at else None,
    }


# ---------- 2. Add baggage (charges calculation, awaiting payment) ----------


@app.post("/reservations/{reservation_id}/baggage")
def add_baggage(
    reservation_id: str,
    additionalWeightKg: float,
    repo: RepoDep,
    baggage_client: BaggageClientDep,
):
    """Add baggage to the reservation; computes charges and marks as awaiting payment if overweight."""
    if additionalWeightKg <= 0:
        raise HTTPException(status_code=400, detail="additionalWeightKg must be positive")

    reservation = repo.get(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status not in (ReservationStatus.IN_PROGRESS, ReservationStatus.AWAITING_PAYMENT):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot add baggage when reservation status is {reservation.status.name}",
        )

    total_kg = reservation.baggage_total_kg + additionalWeightKg
    try:
        quote = baggage_client.get_quote(
            flight_id=reservation.flight_id,
            passenger_id=reservation.passenger_id,
            total_weight_kg=total_kg,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Baggage quote failed: {exc!s}") from exc

    fee = quote.get("overweightFee", 0.0)
    reservation.baggage_total_kg = total_kg
    reservation.overweight_fee = fee
    if fee > 0:
        reservation.status = ReservationStatus.AWAITING_PAYMENT
    repo.save(reservation)

    return {
        "reservationId": reservation_id,
        "baggageTotalKg": reservation.baggage_total_kg,
        "overweightFee": reservation.overweight_fee,
        "status": reservation.status.name,
    }


# ---------- 3. Pay or not (complete or fail) ----------


@app.post("/reservations/{reservation_id}/complete")
def complete_reservation(
    reservation_id: str,
    body: CompleteRequest,
    repo: RepoDep,
    seat_client: SeatClientDep,
):
    """
    Complete the reservation: pay=true confirms the seat and marks COMPLETED;
    pay=false abandons and marks FAILED.
    Before payment, the reservation service asks the seat service if the seat is still held;
    if not (e.g. hold expired), the reservation is marked FAILED and a clear error is returned.
    """
    pay = body.pay
    reservation = repo.get(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if reservation.status is ReservationStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail="Reservation has failed; the seat has been released (hold expired or no longer held).",
        )
    if reservation.status not in (ReservationStatus.IN_PROGRESS, ReservationStatus.AWAITING_PAYMENT):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete when status is {reservation.status.name}",
        )

    if pay:
        try:
            status = seat_client.get_hold_status(
                flight_id=reservation.flight_id,
                seat_id=reservation.seat_id,
                passenger_id=reservation.passenger_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Seat service unavailable: {exc!s}") from exc

        if not status.get("held", False):
            reservation.status = ReservationStatus.FAILED
            repo.save(reservation)
            reason = status.get("reason", "released")
            if reason == "expired":
                msg = "The seat hold window has expired; the seat has been released. The reservation could not be completed."
            else:
                msg = "The seat has been released and is no longer held for this reservation. The reservation could not be completed."
            raise HTTPException(status_code=400, detail=msg)

        try:
            seat_client.confirm_seat(
                flight_id=reservation.flight_id,
                seat_id=reservation.seat_id,
                passenger_id=reservation.passenger_id,
            )
        except Exception as exc:  # noqa: BLE001
            reservation.status = ReservationStatus.FAILED
            repo.save(reservation)
            if "expired" in str(exc).lower():
                raise HTTPException(
                    status_code=400,
                    detail="The seat hold had expired; the seat has been released. The reservation has been marked as failed.",
                ) from exc
            raise HTTPException(status_code=400, detail=f"Seat confirm failed: {exc!s}") from exc
        reservation.status = ReservationStatus.COMPLETED
    else:
        reservation.status = ReservationStatus.FAILED
    repo.save(reservation)
    return {"reservationId": reservation_id, "status": reservation.status.name}


# ---------- 4. Cancel completed reservation (release seat) ----------


@app.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(
    reservation_id: str,
    repo: RepoDep,
    seat_client: SeatClientDep,
):
    """Cancel a completed reservation; releases the seat (available for waitlist or others)."""
    reservation = repo.get(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.status is not ReservationStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail="Only a completed reservation can be cancelled",
        )

    try:
        seat_client.cancel_seat(
            flight_id=reservation.flight_id,
            seat_id=reservation.seat_id,
            passenger_id=reservation.passenger_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Seat cancel failed: {exc!s}") from exc

    reservation.status = ReservationStatus.CANCELLED
    repo.save(reservation)
    return {"reservationId": reservation_id, "status": reservation.status.name}


# ---------- 5. Join waitlist for a seat ----------


@app.post("/waitlist")
def join_waitlist(
    body: JoinWaitlistRequest,
    _abuse: SeatAccessDep,
    seat_client: SeatClientDep,
):
    """
    Add the passenger to the waitlist for the given seat on the given flight.
    When the seat becomes available (e.g. after a cancellation), the seat service
    may assign it to the next person on the waitlist.
    """
    try:
        entry = seat_client.join_waitlist(
            flight_id=body.flightId,
            seat_id=body.seatId,
            passenger_id=body.passengerId,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Could not add to waitlist: {exc!s}",
        ) from exc
    return {
        "flightId": entry.get("flight_id", body.flightId),
        "seatId": entry.get("seat_id", body.seatId),
        "passengerId": entry.get("passenger_id", body.passengerId),
        "joinedAt": entry.get("joined_at"),
        "message": "You have been added to the waitlist for this seat.",
    }
