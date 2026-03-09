from datetime import datetime, timezone

from skyhigh_core.application import CheckInStatusChangedEvent, SkyHighCoreService
from skyhigh_core.domain import (
    BaggageInfo,
    CheckInSession,
    CheckInSessionRepository,
    CheckInStatus,
    SeatLifecycleService,
    WaitlistAssignmentService,
)
from .inmemory_impl import (
    InMemoryEventPublisher,
    InMemoryKeyValueCache,
    InMemorySeatAssignmentRepository,
    InMemorySeatRepository,
    InMemoryWaitlistRepository,
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


def _make_core_with_baggage() -> tuple[SkyHighCoreService, InMemoryCheckInSessionRepository, InMemoryEventPublisher]:
    seat_repo = InMemorySeatRepository()
    assignment_repo = InMemorySeatAssignmentRepository()
    waitlist_repo = InMemoryWaitlistRepository()
    waitlist_service = WaitlistAssignmentService(
        waitlist_repo=waitlist_repo,
        assignment_repo=assignment_repo,
    )
    cache = InMemoryKeyValueCache()
    events = InMemoryEventPublisher()
    checkin_repo = InMemoryCheckInSessionRepository()
    weight_service = SimpleWeightService(per_kg_fee=10.0)
    payment_service = DummyPaymentService()

    core = SkyHighCoreService(
        seat_repo=seat_repo,
        assignment_repo=assignment_repo,
        waitlist_service=waitlist_service,
        cache=cache,
        events=events,
        seat_lifecycle=SeatLifecycleService(),
        checkin_repo=checkin_repo,
        weight_service=weight_service,
        payment_service=payment_service,
    )
    return core, checkin_repo, events


def test_baggage_within_limit_keeps_checkin_in_progress():
    core, checkin_repo, events = _make_core_with_baggage()
    session = CheckInSession(
        session_id="S1",
        passenger_id="P1",
        flight_id="F1",
        created_at=_now(),
        status=CheckInStatus.IN_PROGRESS,
        baggage=BaggageInfo(total_weight_kg=10.0),
    )
    checkin_repo.save(session)

    updated = core.add_baggage_and_validate("S1", additional_weight_kg=10.0, now=_now())

    assert updated.baggage.total_weight_kg == 20.0
    assert updated.status is CheckInStatus.IN_PROGRESS
    assert all(not isinstance(e, CheckInStatusChangedEvent) for e in events.events)


def test_overweight_baggage_triggers_waiting_for_payment():
    core, checkin_repo, events = _make_core_with_baggage()
    session = CheckInSession(
        session_id="S2",
        passenger_id="P2",
        flight_id="F1",
        created_at=_now(),
        status=CheckInStatus.IN_PROGRESS,
        baggage=BaggageInfo(total_weight_kg=20.0),
    )
    checkin_repo.save(session)

    updated = core.add_baggage_and_validate("S2", additional_weight_kg=10.0, now=_now())

    assert updated.baggage.total_weight_kg == 30.0
    assert updated.baggage.overweight_fee_due > 0
    assert updated.status is CheckInStatus.WAITING_FOR_PAYMENT
    assert any(isinstance(e, CheckInStatusChangedEvent) for e in events.events)


def test_successful_payment_resets_status_to_in_progress():
    core, checkin_repo, events = _make_core_with_baggage()
    session = CheckInSession(
        session_id="S3",
        passenger_id="P3",
        flight_id="F1",
        created_at=_now(),
        status=CheckInStatus.WAITING_FOR_PAYMENT,
        baggage=BaggageInfo(total_weight_kg=30.0, overweight_fee_due=50.0),
    )
    checkin_repo.save(session)

    updated = core.process_baggage_payment("S3", now=_now())

    assert updated.status is CheckInStatus.IN_PROGRESS
    assert updated.baggage.overweight_fee_due == 0.0
    assert updated.payment_reference is not None
    assert any(isinstance(e, CheckInStatusChangedEvent) for e in events.events)

