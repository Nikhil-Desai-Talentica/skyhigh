# Project Structure

Overview of the SkyHigh repository layout and the role of each folder and key module.

---

## Root

| Item | Purpose |
|------|--------|
| **`README.md`** | Short intro, architecture summary (three microservices), and how to run (Docker Compose, tests). |
| **`PRD.md`** | Product requirements: problem, goals, key users, and out-of-scope areas. |
| **`pyproject.toml`** | Python project config: dependencies (FastAPI, uvicorn, psycopg2, redis, httpx), package discovery for `services*`, and dev deps (pytest). |
| **`requirements.txt`** | Minimal dependency list for environments that don’t use `pyproject.toml` / uv. |
| **`docker-compose.yml`** | Defines Postgres, Redis, and the three services (seat, baggage, reservation) with env and ports. |
| **`Dockerfile.seat`** | Builds the seat service image (copies `services/`, runs uvicorn on port 8000). |
| **`Dockerfile.baggage`** | Builds the baggage service image (port 8001). |
| **`Dockerfile.reservation`** | Builds the reservation service image (port 8002). |

---

## `services/`

Holds the **microservices**. Each service is a Python package under `services/<name>_service/` with its own domain, application logic, and (where needed) infrastructure.

- **`services/__init__.py`** – Marks `services` as a package so imports like `services.seat_service.domain` work.

---

### `services/seat_service/`

**Seat lifecycle and availability.** Owns seats, holds, confirmations, cancellations, assignments, and waitlist. Uses Postgres and Redis.

| Module | Purpose |
|--------|--------|
| **`domain.py`** | Seat state (AVAILABLE, HELD, CONFIRMED, CANCELLED), `Seat`, `SeatLifecycleService` (hold/confirm/cancel, TTL expiry), `SeatAssignment`, `WaitlistEntry`, `WaitlistRepository`, `WaitlistAssignmentService`, and exceptions (`InvalidSeatTransition`, `HoldExpiredException`, `SeatAlreadyAssigned`). |
| **`application.py`** | Orchestration: `SeatOrchestrationService` (hold, confirm, cancel, `get_hold_status`, `join_waitlist`), repository/cache/event protocols, and event types (`SeatHeldEvent`, `HoldExpiredEvent`, etc.). |
| **`infrastructure.py`** | Postgres implementations (`PostgresSeatRepository`, `PostgresSeatAssignmentRepository`), Redis (`RedisKeyValueCache`, `RedisWaitlistRepository`, `RedisEventPublisher`), and helpers to create DB/Redis connections. |
| **`main.py`** | FastAPI app and HTTP endpoints: GET hold-status, POST hold/confirm/cancel, POST waitlist. Wires repositories, cache, and events from env (e.g. `DATABASE_URL`, `REDIS_URL`). |

---

### `services/baggage_service/`

**Baggage and check-in.** Owns weight rules, overweight fees, and check-in session state.

| Module | Purpose |
|--------|--------|
| **`domain.py`** | `BaggageInfo`, `CheckInStatus`, `CheckInSession`, and protocols for `CheckInSessionRepository`, `WeightService`, and `PaymentService`. |
| **`application.py`** | `BaggageOrchestrationService`: add baggage and validate, process baggage payment; `CheckInStatusChangedEvent`. |
| **`main.py`** | FastAPI app: POST `/baggage/quote` (weight → overweight fee). Uses a local `WeightService` implementation. |

---

### `services/reservation_service/`

**Client-facing API.** Orchestrates seat and baggage via HTTP and owns the reservation aggregate. This is what users and front-ends call.

| Module | Purpose |
|--------|--------|
| **`domain.py`** | `Reservation`, `ReservationStatus`, and `ReservationRepository` (including `get_by_seat` for event handling). |
| **`clients.py`** | HTTP clients for seat and baggage services: `SeatServiceClient` (hold-status, hold, confirm, cancel, join waitlist) and `BaggageServiceClient` (quote). Base URLs from env. |
| **`events.py`** | Listens to Redis list `events:seat` for `HoldExpiredEvent`; finds the matching reservation by flight/seat/passenger and marks it FAILED. Started in app lifespan. |
| **`abuse.py`** | Abuse/bot detection: rate-limits “seat access” (create reservation, join waitlist) per client (IP), temporarily blocks over limit, uses Redis or in-memory storage. |
| **`main.py`** | FastAPI app and all reservation APIs: POST/GET reservations, add baggage, complete (pay or fail), cancel, POST waitlist. Wires repo, seat/baggage clients, abuse detector, and hold-expired listener; applies abuse check to POST reservations and POST waitlist. |

---

## `tests/`

Test suite for domain and application behavior. Uses in-memory implementations so tests don’t need Postgres or Redis.

| Item | Purpose |
|------|--------|
| **`inmemory_impl.py`** | In-memory implementations of seat-service protocols: `InMemorySeatRepository`, `InMemoryKeyValueCache`, `InMemorySeatAssignmentRepository`, `InMemoryWaitlistRepository`, `InMemoryEventPublisher`. Imports from `services.seat_service`. |
| **`test_seat_lifecycle.py`** | Seat lifecycle: hold only when available, confirm only when held by same passenger, cancel only when confirmed by same passenger, TTL expiry and re-hold. |
| **`test_application_flow.py`** | Full flow with `SeatOrchestrationService`: hold → confirm → cancel; checks that seat events are emitted. |
| **`test_waitlist_assignment.py`** | Waitlist: join, dequeue order, auto-assign when seat is freed, no assign when seat is taken. |
| **`test_conflict_free_assignment.py`** | Concurrent seat assignment: only one succeeds per seat; cancelled seat can be reassigned. |
| **`test_baggage_and_payment.py`** | Baggage service: add baggage within limit (stay IN_PROGRESS), overweight (AWAITING_PAYMENT), and payment (back to IN_PROGRESS). Uses in-memory check-in repo and dummy weight/payment services. |

---

## Data flow (high level)

- **Clients** call **reservation service** (create reservation, add baggage, complete, cancel, waitlist).
- **Reservation service** calls **seat service** (hold, hold-status, confirm, cancel, join waitlist) and **baggage service** (quote).
- **Seat service** writes to **Postgres** (seats, assignments) and **Redis** (cache, waitlist, events).
- **Reservation service** consumes **HoldExpiredEvent** from Redis and updates reservations to FAILED; uses Redis (or memory) for abuse rate limits.

Run from repo root: `docker compose up` for all services; `PYTHONPATH=. pytest tests/` for tests.
