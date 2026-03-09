# skyhigh

A flight reservation system that features safe bookings at scale.

## Architecture (microservices)

- **Seat service** (port 8000): seat lifecycle (hold/confirm/cancel), assignments, waitlist. Owns seat domain and persistence (Postgres + Redis).
- **Baggage service** (port 8001): baggage quotes, weight and fees, check-in session orchestration. Owns baggage domain.
- **Reservation service** (port 8002): reservation aggregate; orchestrates by calling the seat and baggage services over HTTP.

Domain models and logic live inside each service for high cohesion. The reservation service depends on `SEAT_SERVICE_URL` and `BAGGAGE_SERVICE_URL` (e.g. in Docker Compose).

## Running

- **Docker Compose**: `docker compose up` — starts Postgres, Redis, and all three services. Reservation service will call seat and baggage by service name.
- **Tests**: From repo root, `PYTHONPATH=. pytest tests/` (requires pytest and project deps).
