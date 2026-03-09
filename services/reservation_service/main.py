from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException

from skyhigh_core.reservations import Reservation, ReservationRepository, ReservationStatus


class InMemoryReservationRepository(ReservationRepository):
    def __init__(self) -> None:
        self._items: Dict[str, Reservation] = {}

    def get(self, reservation_id: str) -> Optional[Reservation]:
        return self._items.get(reservation_id)

    def save(self, reservation: Reservation) -> None:
        self._items[reservation.reservation_id] = reservation


app = FastAPI(title="Reservation Service")
repo = InMemoryReservationRepository()


def get_repo() -> ReservationRepository:
    return repo


RepoDep = Annotated[ReservationRepository, Depends(get_repo)]


@app.post("/reservations")
def create_reservation(flightId: str, passengerId: str, seatId: str, repo: RepoDep):
    now = datetime.now(timezone.utc)
    reservation_id = f"R-{int(now.timestamp())}-{passengerId}"
    reservation = Reservation(
        reservation_id=reservation_id,
        passenger_id=passengerId,
        flight_id=flightId,
        seat_id=seatId,
        created_at=now,
        status=ReservationStatus.IN_PROGRESS,
    )
    repo.save(reservation)
    return {"reservationId": reservation.reservation_id, "status": reservation.status.name}


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str, repo: RepoDep):
    reservation = repo.get(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return {
        "reservationId": reservation.reservation_id,
        "passengerId": reservation.passenger_id,
        "flightId": reservation.flight_id,
        "seatId": reservation.seat_id,
        "status": reservation.status.name,
        "overweightFee": reservation.overweight_fee,
    }

