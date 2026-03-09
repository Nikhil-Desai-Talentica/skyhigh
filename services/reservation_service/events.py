"""
Listen for HoldExpiredEvent from the seat service and mark affected reservations as FAILED.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

import redis

from .domain import ReservationRepository, ReservationStatus

logger = logging.getLogger(__name__)

EVENTS_KEY = "events:seat"


def _run_listener(repo: ReservationRepository, redis_client: redis.Redis) -> None:
    while True:
        try:
            # BLPOP blocks until an event is available or timeout (1s) for graceful shutdown
            result = redis_client.blpop(EVENTS_KEY, timeout=1)
            if result is None:
                continue
            _key, raw = result
            payload = json.loads(raw)
            event_type = payload.get("type")
            if event_type != "HoldExpiredEvent":
                continue
            flight_id = payload.get("flight_id")
            seat_id = payload.get("seat_id")
            passenger_id = payload.get("passenger_id")
            if not flight_id or not seat_id or not passenger_id:
                continue
            reservation = repo.get_by_seat(
                flight_id=flight_id,
                seat_id=seat_id,
                passenger_id=passenger_id,
            )
            if reservation is not None:
                reservation.status = ReservationStatus.FAILED
                repo.save(reservation)
                logger.info(
                    "Marked reservation %s FAILED due to hold expired (flight=%s seat=%s passenger=%s)",
                    reservation.reservation_id,
                    flight_id,
                    seat_id,
                    passenger_id,
                )
        except redis.ConnectionError:
            logger.warning("Redis connection lost in event listener; retrying...")
        except Exception:  # noqa: BLE001
            logger.exception("Error processing seat event")


def start_hold_expired_listener(
    repo: ReservationRepository,
    redis_url: Optional[str] = None,
) -> Optional[threading.Thread]:
    """Start a background thread that listens for HoldExpiredEvent and marks reservations FAILED."""
    url = redis_url or os.environ.get("REDIS_URL")
    if not url:
        logger.info("REDIS_URL not set; hold-expired event listener disabled")
        return None
    client = redis.from_url(url)
    thread = threading.Thread(
        target=_run_listener,
        args=(repo, client),
        daemon=True,
        name="hold-expired-listener",
    )
    thread.start()
    logger.info("Started hold-expired event listener (key=%s)", EVENTS_KEY)
    return thread