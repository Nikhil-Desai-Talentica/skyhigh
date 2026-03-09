"""
Baggage service application layer: check-in session orchestration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from .domain import (
    BaggageInfo,
    CheckInSession,
    CheckInSessionRepository,
    CheckInStatus,
    PaymentService,
    WeightService,
)


@dataclass(frozen=True)
class CheckInStatusChangedEvent:
    session_id: str
    passenger_id: str
    flight_id: str
    old_status: CheckInStatus
    new_status: CheckInStatus
    occurred_at: datetime


class EventPublisher(Protocol):
    def publish(self, event: CheckInStatusChangedEvent) -> None:
        ...


class BaggageOrchestrationService:
    """
    Orchestrates baggage validation and payment for check-in sessions.
    """

    def __init__(
        self,
        checkin_repo: CheckInSessionRepository,
        weight_service: WeightService,
        payment_service: PaymentService,
        events: Optional[EventPublisher] = None,
    ) -> None:
        self._checkin_repo = checkin_repo
        self._weight_service = weight_service
        self._payment_service = payment_service
        self._events = events

    def add_baggage_and_validate(
        self,
        session_id: str,
        additional_weight_kg: float,
        now: datetime,
    ) -> CheckInSession:
        session = self._checkin_repo.get(session_id)
        if session is None:
            raise ValueError(f"Unknown check-in session {session_id}")

        if session.status is CheckInStatus.COMPLETED:
            raise ValueError("Cannot modify baggage for completed check-in.")

        new_total = session.baggage.total_weight_kg + additional_weight_kg
        fee = self._weight_service.calculate_overweight_fee(new_total)

        session.baggage = BaggageInfo(
            total_weight_kg=new_total,
            overweight_fee_due=fee,
        )

        old_status = session.status
        if new_total > self._weight_service.MAX_WEIGHT_KG and fee > 0:
            session.status = CheckInStatus.WAITING_FOR_PAYMENT
        else:
            session.status = CheckInStatus.IN_PROGRESS

        self._checkin_repo.save(session)

        if self._events is not None and old_status != session.status:
            self._events.publish(
                CheckInStatusChangedEvent(
                    session_id=session.session_id,
                    passenger_id=session.passenger_id,
                    flight_id=session.flight_id,
                    old_status=old_status,
                    new_status=session.status,
                    occurred_at=now,
                )
            )

        return session

    def process_baggage_payment(
        self,
        session_id: str,
        now: datetime,
    ) -> CheckInSession:
        session = self._checkin_repo.get(session_id)
        if session is None:
            raise ValueError(f"Unknown check-in session {session_id}")

        if session.status is not CheckInStatus.WAITING_FOR_PAYMENT:
            return session

        if session.baggage.overweight_fee_due <= 0:
            session.status = CheckInStatus.IN_PROGRESS
            self._checkin_repo.save(session)
            return session

        payment_ref = self._payment_service.charge_overweight_fee(
            session=session,
            amount=session.baggage.overweight_fee_due,
        )
        session.payment_reference = payment_ref
        session.baggage = BaggageInfo(
            total_weight_kg=session.baggage.total_weight_kg,
            overweight_fee_due=0.0,
        )

        old_status = session.status
        session.status = CheckInStatus.IN_PROGRESS
        self._checkin_repo.save(session)

        if self._events is not None and old_status != session.status:
            self._events.publish(
                CheckInStatusChangedEvent(
                    session_id=session.session_id,
                    passenger_id=session.passenger_id,
                    flight_id=session.flight_id,
                    old_status=old_status,
                    new_status=session.status,
                    occurred_at=now,
                )
            )

        return session
