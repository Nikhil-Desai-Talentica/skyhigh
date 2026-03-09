"""
HTTP clients for seat and baggage services.
Reservation service orchestrates by calling these microservices.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx


def _seat_base_url() -> str:
    return os.environ.get("SEAT_SERVICE_URL", "http://seat-service:8000")


def _baggage_base_url() -> str:
    return os.environ.get("BAGGAGE_SERVICE_URL", "http://baggage-service:8001")


class SeatServiceClient:
    """Client for the seat microservice."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = (base_url or _seat_base_url()).rstrip("/")

    def get_hold_status(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
    ) -> dict[str, Any]:
        """Ask seat service if the seat is still held for this passenger."""
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                f"{self._base_url}/seats/{flight_id}/{seat_id}/hold-status",
                params={"passenger_id": passenger_id},
            )
            r.raise_for_status()
            return r.json()

    def hold_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{self._base_url}/seats/{flight_id}/{seat_id}/hold",
                params={"passenger_id": passenger_id},
            )
            r.raise_for_status()
            return r.json()

    def confirm_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{self._base_url}/seats/{flight_id}/{seat_id}/confirm",
                params={"passenger_id": passenger_id},
            )
            r.raise_for_status()
            return r.json()

    def cancel_seat(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
    ) -> None:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{self._base_url}/seats/{flight_id}/{seat_id}/cancel",
                params={"passenger_id": passenger_id},
            )
            r.raise_for_status()

    def join_waitlist(
        self,
        flight_id: str,
        seat_id: str,
        passenger_id: str,
    ) -> dict[str, Any]:
        """Add passenger to the waitlist for a seat."""
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{self._base_url}/seats/{flight_id}/{seat_id}/waitlist",
                params={"passenger_id": passenger_id},
            )
            r.raise_for_status()
            return r.json()


class BaggageServiceClient:
    """Client for the baggage microservice."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = (base_url or _baggage_base_url()).rstrip("/")

    def get_quote(
        self,
        flight_id: str,
        passenger_id: str,
        total_weight_kg: float,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{self._base_url}/baggage/quote",
                json={
                    "flightId": flight_id,
                    "passengerId": passenger_id,
                    "totalWeightKg": total_weight_kg,
                },
            )
            r.raise_for_status()
            return r.json()
