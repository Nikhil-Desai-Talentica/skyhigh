from datetime import datetime, timezone

from services.baggage_service.application import BaggageOrchestrationService, CheckInStatusChangedEvent
from services.baggage_service.domain import (
    BaggageInfo,
    CheckInSession,
    CheckInSessionRepository,
    CheckInStatus,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryCheckInSessionRepository(CheckInSessionRepository):
    def __init__(self) -> None:
        self._sessions: dict[str, CheckInSession] = {}

    def get(self, session_id: str) -> CheckInSession | None:
        return self._sessions.get(session_id)

    def save(self, session: CheckInSession) -> None:
        self._sessions[session.session_id] = session


class SimpleWeightService:
    MAX_WEIGHT_KG = 25.0

    def __init__(self, per_kg_fee: float = 10.0) -> None:
        self.per_kg_fee = per_kg_fee

    def calculate_overweight_fee(self, total_weight_kg: float) -> float:
        if total_weight_kg <= self.MAX_WEIGHT_KG:
            return 0.0
        excess = total_weight_kg - self.MAX_WEIGHT_KG
        return excess * self.per_kg_fee


class DummyPaymentService:
    def __init__(self) -> None:
        self.charges: list[tuple[str, float]] = []

    def charge_overweight_fee(
        self,
        session: CheckInSession,
        amount: float,
    ) -> str:
        self.charges.append((session.session_id, amount))
        return f"PAY-{session.session_id}"


class CollectingEventPublisher:
    def __init__(self) -> None:
        self.events: list[CheckInStatusChangedEvent] = []

    def publish(self, event: CheckInStatusChangedEvent) -> None:
        self.events.append(event)


def _make_baggage_service() -> tuple[BaggageOrchestrationService, InMemoryCheckInSessionRepository, CollectingEventPublisher]:
    checkin_repo = InMemoryCheckInSessionRepository()
    weight_service = SimpleWeightService(per_kg_fee=10.0)
    payment_service = DummyPaymentService()
    events = CollectingEventPublisher()

    service = BaggageOrchestrationService(
        checkin_repo=checkin_repo,
        weight_service=weight_service,
        payment_service=payment_service,
        events=events,
    )
    return service, checkin_repo, events


def test_baggage_within_limit_keeps_checkin_in_progress():
    service, checkin_repo, events = _make_baggage_service()
    session = CheckInSession(
        session_id="S1",
        passenger_id="P1",
        flight_id="F1",
        created_at=_now(),
        status=CheckInStatus.IN_PROGRESS,
        baggage=BaggageInfo(total_weight_kg=10.0),
    )
    checkin_repo.save(session)

    updated = service.add_baggage_and_validate("S1", additional_weight_kg=10.0, now=_now())

    assert updated.baggage.total_weight_kg == 20.0
    assert updated.status is CheckInStatus.IN_PROGRESS
    assert all(not isinstance(e, CheckInStatusChangedEvent) for e in events.events)


def test_overweight_baggage_triggers_waiting_for_payment():
    service, checkin_repo, events = _make_baggage_service()
    session = CheckInSession(
        session_id="S2",
        passenger_id="P2",
        flight_id="F1",
        created_at=_now(),
        status=CheckInStatus.IN_PROGRESS,
        baggage=BaggageInfo(total_weight_kg=20.0),
    )
    checkin_repo.save(session)

    updated = service.add_baggage_and_validate("S2", additional_weight_kg=10.0, now=_now())

    assert updated.baggage.total_weight_kg == 30.0
    assert updated.baggage.overweight_fee_due > 0
    assert updated.status is CheckInStatus.WAITING_FOR_PAYMENT
    assert any(isinstance(e, CheckInStatusChangedEvent) for e in events.events)


def test_successful_payment_resets_status_to_in_progress():
    service, checkin_repo, events = _make_baggage_service()
    session = CheckInSession(
        session_id="S3",
        passenger_id="P3",
        flight_id="F1",
        created_at=_now(),
        status=CheckInStatus.WAITING_FOR_PAYMENT,
        baggage=BaggageInfo(total_weight_kg=30.0, overweight_fee_due=50.0),
    )
    checkin_repo.save(session)

    updated = service.process_baggage_payment("S3", now=_now())

    assert updated.status is CheckInStatus.IN_PROGRESS
    assert updated.baggage.overweight_fee_due == 0.0
    assert updated.payment_reference is not None
    assert any(isinstance(e, CheckInStatusChangedEvent) for e in events.events)
