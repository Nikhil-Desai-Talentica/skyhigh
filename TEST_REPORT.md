# Test Report

Discussion of test coverage for the SkyHigh reservation system: what is tested, how tests are organized, and where coverage is missing.

---

## 1. Executive Summary

| Metric | Value |
|--------|--------|
| **Total test modules** | 5 |
| **Total test cases** | 14 |
| **Scope** | Domain and application logic for **Seat** and **Baggage** services; in-memory test doubles; no Postgres/Redis in tests. |
| **Not covered** | Reservation service (HTTP and orchestration), abuse detection, event listener, HTTP layer of Seat/Baggage. |
| **How to run** | From repo root: `PYTHONPATH=. pytest tests/` (requires `pytest`; install with `pip install -e ".[dev]"` or `uv sync`). |

The test suite focuses on **core business rules**: seat lifecycle, conflict-free assignment, waitlist behavior, and baggage/check-in orchestration. The **client-facing Reservation API** and **cross-service flows** are not exercised by automated tests; those would require integration or API tests.

---

## 2. Test Suite Overview

### 2.1 Test Files and Counts

| File | Test count | Focus |
|------|-------------|--------|
| `test_seat_lifecycle.py` | 9 | Seat domain: hold, confirm, cancel, TTL expiry, invalid transitions |
| `test_application_flow.py` | 1 | Seat orchestration: full flow hold → confirm → cancel, event emission |
| `test_waitlist_assignment.py` | 3 | Waitlist: join, auto-assign when seat frees, skip when seat taken |
| `test_conflict_free_assignment.py` | 3 | Assignment repo: single winner (sequential + concurrent), cancel and reassign |
| `test_baggage_and_payment.py` | 3 | Baggage orchestration: within limit, overweight → AWAITING_PAYMENT, payment → IN_PROGRESS |
| **Total** | **14** | |

### 2.2 Test Infrastructure

- **`tests/inmemory_impl.py`** provides in-memory implementations of Seat service protocols:
  - `InMemorySeatRepository`, `InMemoryKeyValueCache`, `InMemorySeatAssignmentRepository`, `InMemoryWaitlistRepository`, `InMemoryEventPublisher`
- Tests **do not** use Postgres or Redis; all persistence is in-memory so tests are fast and do not require external services.
- Baggage tests define their own in-memory check-in repo and dummy weight/payment services inside the test file.

---

## 3. Coverage by Area

### 3.1 Seat Service — Domain (`domain.py`)

**Covered:**

- **Hold:** Seat can be held only when AVAILABLE; cannot hold when already HELD; within-TTL hold blocks another passenger; after TTL (120s) hold expires and a new passenger can hold.
- **Confirm:** Only the holding passenger can confirm; cannot confirm when not HELD or when held by someone else; cannot confirm after hold has expired (raises and seat returns to AVAILABLE).
- **Cancel:** Only CONFIRMED seats can be cancelled; only the confirming passenger can cancel; cancelled_at is set.
- **Invalid transitions:** `InvalidSeatTransition` raised for invalid state changes.

**Not covered:**

- **`HoldExpiredException`:** The domain raises `HoldExpiredException` when confirming after this passenger’s hold has expired. `test_cannot_confirm_after_hold_has_expired` currently expects `InvalidSeatTransition`; it should be updated to expect `HoldExpiredException` so the test matches the implementation.
- **WaitlistAssignmentService** is covered in `test_waitlist_assignment.py` (domain-level); **SeatOrchestrationService.join_waitlist** is not explicitly tested (only via domain `join_waitlist`).
- **SeatOrchestrationService.get_hold_status** is not unit-tested (expiry logic is covered in lifecycle tests).

### 3.2 Seat Service — Application (`application.py`)

**Covered:**

- **SeatOrchestrationService:** hold_seat, confirm_seat, cancel_confirmed_seat wired with in-memory repos and cache; events (SeatHeldEvent, SeatConfirmedEvent, SeatCancelledEvent) are emitted (asserted in `test_full_flow_hold_confirm_cancel_and_waitlist_assignment`).

**Not covered:**

- **get_hold_status:** Return values (held/reason) and persistence of expiry.
- **join_waitlist:** Application-layer method that delegates to WaitlistAssignmentService.
- **HoldExpiredEvent** emission when confirm fails due to expiry (or when hold_seat expires a previous holder).
- Cache hit/miss behavior (cache is used but not asserted).

### 3.3 Seat Service — Infrastructure

**Covered:**

- **In-memory** implementations of assignment and waitlist are used and stressed (concurrent assignment, cancel and reassign).  
- **Postgres and Redis** implementations are **not** tested; they would require a test database and Redis or testcontainers.

### 3.4 Baggage Service — Domain and Application

**Covered:**

- **BaggageOrchestrationService:** add_baggage_and_validate (within limit → stay IN_PROGRESS; overweight → AWAITING_PAYMENT); process_baggage_payment (WAITING_FOR_PAYMENT → IN_PROGRESS, fee cleared, payment reference set).
- **CheckInStatusChangedEvent** is emitted when status changes (asserted in tests).

**Not covered:**

- **Baggage service HTTP API** (`POST /baggage/quote`); only the orchestration and domain logic used by the reservation flow are tested.
- Edge cases (e.g. completed check-in, unknown session id) are partially covered by “cannot add baggage when status is X” in application logic; no dedicated tests for those error paths in the service.

### 3.5 Reservation Service

**Not covered:**

- **HTTP endpoints:** create reservation, get reservation, add baggage, complete (pay/not pay), cancel, join waitlist.
- **Orchestration:** Calling Seat and Baggage clients, mapping responses to reservation state, hold-expired handling.
- **Abuse detection:** Rate limiting, block, Retry-After.
- **Event listener:** Processing HoldExpiredEvent from Redis and marking reservations FAILED.
- **Reservation domain:** Repository behavior (get, save, get_by_seat) is only used by the event listener in production; not exercised by tests.

### 3.6 Baggage Service — HTTP

**Not covered:**

- **POST /baggage/quote** (request/response, status codes). Quote logic is covered indirectly via BaggageOrchestrationService tests.

---

## 4. Test Categories and Purpose

| Category | Examples | Purpose |
|----------|-----------|--------|
| **Domain unit** | `test_seat_lifecycle.py`, parts of waitlist/conflict-free | Validate business rules in isolation (state machine, invariants, exceptions). |
| **Application integration** | `test_application_flow.py`, `test_baggage_and_payment.py` | Validate orchestration with in-memory repos and stubs (no real HTTP or DB). |
| **Concurrency** | `test_only_one_assignment_succeeds_for_same_seat_concurrent` | Validate that a single assignment “wins” under concurrent requests. |
| **Event emission** | Assertions on `events.events` in application flow, `CheckInStatusChangedEvent` in baggage tests | Ensure domain events are published when expected. |

There are **no** API/HTTP tests (e.g. TestClient against FastAPI), **no** end-to-end tests (real HTTP between reservation and seat/baggage), and **no** line or branch coverage reports (e.g. pytest-cov) in the current setup.

---

## 5. Gaps and Recommendations

### 5.1 High-Value Additions

1. **Reservation service API tests**  
   Use FastAPI `TestClient` and mock Seat/Baggage HTTP clients (or a small test server) to test:
   - POST /reservations (success and seat hold failure),
   - POST …/complete with pay=true/false and hold expired,
   - POST …/cancel (success and “not COMPLETED”),
   - POST /waitlist,
   - GET /reservations/{id}.  
   This would cover the main user-facing behavior and error responses.

2. **Abuse detection tests**  
   Unit tests for `AbuseDetector`: record N requests within window → block; after block TTL → allow again; optional Redis vs in-memory behavior.

3. **Hold-expired event handling**  
   Test that when the event listener receives a HoldExpiredEvent (e.g. by pushing a fixture event and running the listener once), the matching reservation is updated to FAILED (using an in-memory repo).

### 5.2 Medium-Value Additions

4. **SeatOrchestrationService.get_hold_status**  
   Tests that return values (held, reason) are correct for: held, expired, released, held_by_other, not_held; and that expiry is persisted when applicable.

5. **SeatOrchestrationService.join_waitlist**  
   Test that it delegates to WaitlistAssignmentService and returns the expected structure.

6. **Coverage reporting**  
   Add `pytest-cov` (e.g. `pytest --cov=services --cov-report=term-missing tests/`) to track line and branch coverage and highlight untested code.

### 5.3 Lower Priority / Optional

7. **Infrastructure tests**  
   Postgres and Redis implementations could be tested with testcontainers or a dedicated test instance to catch schema or key-format issues.

8. **Baggage and Seat HTTP layers**  
   Optional TestClient tests for Seat and Baggage endpoints if those APIs are consumed by more than the reservation service or need a stable contract.

---

## 6. How to Run the Tests

From the repository root:

```bash
# Install dev dependencies (pytest)
pip install -e ".[dev]"
# or
uv sync --all-extras

# Run all tests
PYTHONPATH=. pytest tests/ -v

# Run with coverage (if pytest-cov is added)
PYTHONPATH=. pytest tests/ -v --cov=services --cov-report=term-missing
```

Tests do **not** require Postgres or Redis; they use in-memory implementations only.
