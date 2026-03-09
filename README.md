# SkyHigh

A **flight reservation system** that provides safe seat bookings at scale, with baggage fees, waitlists, and protection against abuse. Built as a small **microservices** backend: clients talk to a single **Reservation API**, which orchestrates **Seat** and **Baggage** services over HTTP.

---

## Features

- **Reservations** — Create a reservation (hold a seat for a short window), add baggage, complete payment or abandon, and cancel a completed reservation.
- **Seat lifecycle** — Hold (120s TTL), confirm, and cancel with conflict-free assignment; no double-booking.
- **Waitlist** — Passengers can join a waitlist for a seat; when the seat is released (e.g. after cancellation), the next waitlisted passenger can be assigned.
- **Baggage** — Add baggage to a reservation; overweight fees are calculated and the reservation can be marked as awaiting payment.
- **Hold expiry** — Before payment, the system checks with the seat service whether the hold is still valid; if it has expired, the reservation is marked failed and the client gets a clear message. A background listener also reacts to hold-expired events.
- **Abuse protection** — Rate limiting on seat-related operations (create reservation, join waitlist); clients that exceed the threshold are temporarily blocked (429 with `Retry-After`).

---

## Prerequisites

- **Python 3.10+**
- **Docker and Docker Compose** (for running all services together)
- Optionally **uv** or **pip** for installing dependencies

---

## Quick Start

### Run with Docker Compose

From the repo root:

```bash
docker compose up --build
```

This starts:

- **PostgreSQL** (port 5432) — used by the Seat service
- **Redis** (port 6379) — cache, waitlist, events, abuse counters
- **Seat service** (port 8000)
- **Baggage service** (port 8001)
- **Reservation service** (port 8002) — **this is the API clients should call**

Example: create a reservation (hold a seat):

```bash
curl -X POST "http://localhost:8002/reservations?flightId=F1&passengerId=P1&seatId=12A"
```

### Run tests

Install dev dependencies, then run pytest (no database or Redis required; tests use in-memory implementations):

```bash
pip install -e ".[dev]"
# or: uv sync --all-extras

PYTHONPATH=. pytest tests/ -v
```

---

## Architecture

| Service            | Port | Role |
|--------------------|------|------|
| **Reservation API** | 8002 | Single client-facing API. Orchestrates seat and baggage via HTTP; owns reservation state; runs abuse detection and hold-expired event listener. |
| **Seat service**   | 8000 | Seat lifecycle (hold, confirm, cancel), assignments, waitlist. Persistence: Postgres (seats, assignments) + Redis (cache, waitlist, events). |
| **Baggage service** | 8001 | Baggage quote (weight → overweight fee). Stateless. |

Clients **only** need to call the Reservation service. It depends on `SEAT_SERVICE_URL` and `BAGGAGE_SERVICE_URL` (and optionally `REDIS_URL` for events and abuse). In Docker Compose these are set so the reservation service can reach the other containers by name.

---

## Configuration

| Variable | Service | Default / purpose |
|----------|---------|-------------------|
| `DATABASE_URL` | Seat | Postgres DSN (e.g. `postgres://user:pass@host:5432/skyhigh`) |
| `REDIS_URL` | Seat, Reservation | Redis DSN (e.g. `redis://redis:6379/0`) |
| `SEAT_SERVICE_URL` | Reservation | `http://seat-service:8000` |
| `BAGGAGE_SERVICE_URL` | Reservation | `http://baggage-service:8001` |
| `ABUSE_WINDOW_SECONDS` | Reservation | 60 (rate limit window) |
| `ABUSE_MAX_REQUESTS` | Reservation | 15 (max requests in window before block) |
| `ABUSE_BLOCK_DURATION_SECONDS` | Reservation | 300 (block duration in seconds) |

---

## API Overview

The **Reservation API** (base URL `http://localhost:8002` when run via Docker) exposes:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/reservations` | Create reservation (hold seat). Query: `flightId`, `passengerId`, `seatId`. |
| GET | `/reservations/{id}` | Get reservation details. |
| POST | `/reservations/{id}/baggage` | Add baggage. Query: `additionalWeightKg`. |
| POST | `/reservations/{id}/complete` | Complete (pay or abandon). Body: `{ "pay": true }` or `{ "pay": false }`. |
| POST | `/reservations/{id}/cancel` | Cancel a completed reservation (releases seat). |
| POST | `/waitlist` | Join waitlist for a seat. Body: `{ "flightId", "passengerId", "seatId" }`. |

Full request/response details and error codes are in **`API-SPECIFICATION.yml`** (OpenAPI 3.0).

---

## Documentation

| Document | Description |
|----------|-------------|
| [PRD.md](PRD.md) | Product requirements: problem, goals, key users. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architecture with diagrams (context, deployment, data ownership). |
| [WORKFLOW_DESIGN.md](WORKFLOW_DESIGN.md) | Flows, state machine, database schema, Redis keys. |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | Repository layout and purpose of each folder/module. |
| [API-SPECIFICATION.yml](API-SPECIFICATION.yml) | OpenAPI spec for Reservation, Seat, and Baggage APIs. |
| [TEST_REPORT.md](TEST_REPORT.md) | Test coverage and gaps. |

---

## License

See the repository for license information.
