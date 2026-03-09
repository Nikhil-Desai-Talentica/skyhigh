# Architecture

High-level architecture of the SkyHigh reservation system, with diagrams and design principles.

---

## 1. System Context

The system exposes a **single API** (Reservation Service) to clients. It coordinates **seat availability** and **baggage fees** via two backend microservices and uses **Postgres** and **Redis** for persistence and events.

```mermaid
flowchart TB
    subgraph external["External"]
        Passenger[Passenger / Travel Agent]
        Automation[Automation / Bots]
    end

    subgraph system["SkyHigh System"]
        Reservation[Reservation API :8002]
        Seat[Seat Service :8000]
        Baggage[Baggage Service :8001]
        Postgres[(PostgreSQL)]
        Redis[(Redis)]
    end

    Passenger -->|HTTPS| Reservation
    Automation -->|HTTPS| Reservation
    Reservation -->|HTTP| Seat
    Reservation -->|HTTP| Baggage
    Reservation -->|Read/Write| Redis
    Seat -->|Read/Write| Postgres
    Seat -->|Read/Write| Redis
```

**In plain terms:**

- **Users** (passengers, agents, or front-end apps) call only the **Reservation API**.
- **Reservation API** calls **Seat Service** and **Baggage Service** over HTTP and uses **Redis** for events and abuse detection.
- **Seat Service** uses **Postgres** (seats, assignments) and **Redis** (cache, waitlist, event queue).
- **Baggage Service** is stateless; no database in the diagram.

---

## 2. Container / Deployment View

How the system is run with Docker Compose: one process per service, shared Postgres and Redis.

```mermaid
flowchart TB
    subgraph clients[" "]
        C[Client Apps / Browsers]
    end

    subgraph runtime["Runtime (Docker Compose)"]
        subgraph reservation["Reservation Service :8002"]
            R[FastAPI App]
            R --> R_Listener[Event Listener Thread]
            R --> R_Abuse[Abuse Detector]
        end

        subgraph seat["Seat Service :8000"]
            S[FastAPI App]
        end

        subgraph baggage["Baggage Service :8001"]
            B[FastAPI App]
        end

        subgraph data["Data Stores"]
            PG[(PostgreSQL :5432)]
            Redis[(Redis :6379)]
        end
    end

    C -->|HTTP| R
    R -->|HTTP| S
    R -->|HTTP| B
    R -->|BLPOP / GET/SET| Redis
    R_Abuse -->|INCR / SETEX| Redis
    S -->|SQL| PG
    S -->|Cache, List, RPUSH| Redis
```

**Dependencies:**

| Service            | Depends on        | Purpose                          |
|--------------------|-------------------|----------------------------------|
| Reservation        | Seat, Baggage     | Orchestration                    |
| Reservation        | Redis             | Events (hold-expired), abuse     |
| Seat               | Postgres, Redis   | Persistence, cache, waitlist     |
| Baggage            | —                 | Stateless                        |

---

## 3. Service Boundaries and Responsibilities

Each service owns a **bounded context** and its own data (or no persistent data).

```mermaid
flowchart LR
    subgraph reservation["Reservation Service (Orchestrator)"]
        direction TB
        R_API[HTTP API]
        R_Domain[Reservation Aggregate]
        R_Clients[Seat + Baggage HTTP Clients]
        R_Events[Event Listener]
        R_Abuse[Abuse Detector]
        R_API --> R_Domain
        R_API --> R_Clients
        R_API --> R_Abuse
        R_Events --> R_Domain
    end

    subgraph seat["Seat Service (Seat Domain)"]
        direction TB
        S_API[HTTP API]
        S_App[Orchestration + Lifecycle]
        S_Domain[Seat, Assignment, Waitlist]
        S_Infra[Postgres + Redis]
        S_API --> S_App
        S_App --> S_Domain
        S_App --> S_Infra
    end

    subgraph baggage["Baggage Service (Baggage Domain)"]
        direction TB
        B_API[HTTP API]
        B_Domain[Weight, Fee, Check-in]
        B_API --> B_Domain
    end

    R_Clients --> S_API
    R_Clients --> B_API
```

| Service            | Owns                                                                 | Does not own                         |
|--------------------|----------------------------------------------------------------------|--------------------------------------|
| **Reservation**    | Reservation aggregate (id, passenger, flight, seat, status, baggage, fees). Abuse state (Redis or memory). | Seat state, assignments, waitlist, baggage rules. |
| **Seat**           | Seat lifecycle, assignments, waitlist. Seat cache and event stream.  | Reservations, baggage, abuse.        |
| **Baggage**        | Weight rules, fee calculation, check-in session model.              | Reservations, seats.                  |

---

## 4. Communication Patterns

### 4.1 Synchronous: HTTP

All **user-initiated** actions go through the Reservation API, which calls Seat and Baggage over **HTTP** (sync).

```mermaid
sequenceDiagram
    participant Client
    participant Reservation
    participant Seat
    participant Baggage

    Client->>Reservation: POST /reservations
    Reservation->>Seat: POST /seats/.../hold
    Seat-->>Reservation: 200
    Reservation-->>Client: 200

    Client->>Reservation: POST /reservations/.../baggage
    Reservation->>Baggage: POST /baggage/quote
    Baggage-->>Reservation: 200
    Reservation-->>Client: 200
```

- **Reservation → Seat:** hold, hold-status, confirm, cancel, join waitlist.
- **Reservation → Baggage:** quote (weight → fee).
- Timeouts and retries are the responsibility of the reservation service (e.g. via HTTP client settings).

### 4.2 Asynchronous: Redis List (Events)

The **Seat Service** publishes domain events to a Redis list. The **Reservation Service** consumes them in a background thread to keep reservation status in sync (e.g. hold expired → reservation FAILED).

```mermaid
flowchart LR
    subgraph seat["Seat Service"]
        S[Handler]
    end
    subgraph redis["Redis"]
        Q[events:seat List]
    end
    subgraph reservation["Reservation Service"]
        L[Listener Thread]
        R[Reservation Repo]
    end

    S -->|RPUSH| Q
    Q -->|BLPOP| L
    L -->|Update reservation FAILED| R
```

- **Producer:** Seat service (on hold expiry during confirm, or when another passenger takes a held seat).
- **Consumer:** Reservation service (filters for `HoldExpiredEvent`, updates matching reservation).
- **Shape:** Single list; consumer pulls and processes one event at a time.

---

## 5. Data Ownership and Storage

Who writes what, and where it lives.

```mermaid
flowchart TB
    subgraph reservation_data["Reservation Service"]
        R_Mem[(In-Memory Reservations)]
        R_Redis[Redis: abuse:count, abuse:blocked]
    end

    subgraph seat_data["Seat Service"]
        PG[(Postgres: seats, seat_assignments)]
        S_Redis[Redis: seat cache, waitlist, events:seat]
    end

    subgraph baggage_data["Baggage Service"]
        B_None[No persistent store]
    end

    reservation_data --- R_Mem
    reservation_data --- R_Redis
    seat_data --- PG
    seat_data --- S_Redis
```

| Data                  | Owner        | Storage              | Accessed by              |
|-----------------------|-------------|----------------------|--------------------------|
| Reservations          | Reservation | In-memory (current)  | Reservation only         |
| Abuse counters/block  | Reservation | Redis                | Reservation only         |
| Seat state            | Seat        | Postgres + Redis cache | Seat only              |
| Seat assignments      | Seat        | Postgres             | Seat only                |
| Waitlist              | Seat        | Redis list           | Seat only                |
| Seat events           | Seat        | Redis list           | Seat (write), Reservation (read) |
| Baggage rules / quote  | Baggage     | In-process           | Baggage only             |

There is **no shared database** between services; only **Redis** is shared for events and abuse, with clear key namespaces.

---

## 6. Layered View (Reservation and Seat Services)

Internal structure of the two main services.

### 6.1 Reservation Service

```mermaid
flowchart TB
    subgraph api["API Layer"]
        Routes[FastAPI Routes]
        Deps[Dependencies: Abuse, Repo, Clients]
    end

    subgraph app["Application"]
        Orchestration[Reservation Flows]
        Clients[Seat + Baggage HTTP Clients]
        Events[Hold-Expired Listener]
        Abuse[Abuse Detector]
    end

    subgraph domain["Domain"]
        Reservation[Reservation, Status]
        Repo[ReservationRepository]
    end

    Routes --> Deps
    Routes --> Orchestration
    Orchestration --> Clients
    Orchestration --> domain
    Events --> domain
    Abuse --> Redis
```

- **API:** HTTP endpoints and dependencies (abuse check, repo, clients).
- **Application:** Orchestration (create, add baggage, complete, cancel, waitlist), HTTP clients, event listener, abuse logic.
- **Domain:** Reservation aggregate and repository protocol; no direct dependency on Seat/Baggage types.

### 6.2 Seat Service

```mermaid
flowchart TB
    subgraph api["API Layer"]
        Routes[FastAPI Routes]
    end

    subgraph app["Application"]
        Orchestration[SeatOrchestrationService]
        Events[Event Publisher]
    end

    subgraph domain["Domain"]
        Seat[Seat, SeatState]
        Lifecycle[SeatLifecycleService]
        Assignment[SeatAssignment, Waitlist]
    end

    subgraph infra["Infrastructure"]
        Postgres[Postgres Repositories]
        Redis[Redis Cache, Waitlist, Events]
    end

    Routes --> Orchestration
    Orchestration --> domain
    Orchestration --> Events
    Orchestration --> Postgres
    Orchestration --> Redis
```

- **API:** Seat and waitlist HTTP endpoints.
- **Application:** Orchestration (hold, confirm, cancel, hold-status, join waitlist), event publishing.
- **Domain:** Seat lifecycle, assignments, waitlist types and services.
- **Infrastructure:** Postgres (seats, assignments) and Redis (cache, waitlist, event list).

---

## 7. Design Principles

| Principle | How it’s applied |
|-----------|-------------------|
| **Single client entry point** | All user traffic goes to the Reservation Service; no direct client calls to Seat or Baggage. |
| **Bounded contexts** | Reservation, Seat, and Baggage each own their domain and data; no shared domain models across services. |
| **Sync for commands** | User actions are carried out via HTTP from Reservation to Seat/Baggage so the response can reflect success or failure immediately. |
| **Async for reactions** | Hold-expired and similar side effects are propagated via Redis so Reservation can update state without the user waiting. |
| **No shared database** | Seat uses Postgres; Reservation uses in-memory (and Redis for abuse/events). Only Redis is shared, with separate key namespaces. |
| **Abuse at the edge** | Rate limiting and blocking are implemented in the Reservation Service, the only public API. |

---

## 8. Diagram Index

| Diagram | Section | Content |
|---------|---------|--------|
| System context | §1 | Users, Reservation API, Seat/Baggage, Postgres/Redis |
| Deployment | §2 | Containers and data stores |
| Service boundaries | §3 | Responsibilities and ownership per service |
| HTTP sequence | §4.1 | Sync calls Reservation → Seat/Baggage |
| Event flow | §4.2 | Redis list: Seat publishes, Reservation consumes |
| Data ownership | §5 | Where each data type is stored and who writes it |
| Reservation layers | §6.1 | API, application, domain inside Reservation Service |
| Seat layers | §6.2 | API, application, domain, infrastructure in Seat Service |

For flow-level detail (create reservation, complete, cancel, waitlist, abuse), see **WORKFLOW_DESIGN.md**.
