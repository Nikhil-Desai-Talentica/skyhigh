# Product Requirements Document: SkyHigh Reservation System

## Problem Statement

Airlines and travel platforms need a **reliable, scalable way to manage flight seat reservations** that:

- Prevents **double-booking** and race conditions when many users select the same seat.
- Handles the **time-sensitive** nature of seat holds (e.g. a short window to pay before the hold expires).
- Supports **baggage** and overweight fees as part of the reservation flow.
- Gives passengers a way to **join a waitlist** when a preferred seat is taken, and to receive that seat if it is later released.
- Operates at **scale** without a single monolith, so seat, baggage, and reservation concerns can evolve and scale independently.
- Reduces **abuse and bots** (e.g. scrapers or scripts that rapidly probe seat availability and degrade service for real users).

Without a system that addresses these, operators risk overbookings, inconsistent fees, poor UX around hold expiry and waitlist, and vulnerability to automated abuse.

---

## Goals of the System

1. **Safe bookings at scale**  
   Ensure each seat can be held and confirmed for at most one passenger at a time, with conflict-free assignment and clear lifecycle (hold → confirm or expire → cancel).

2. **Single, clear API for users**  
   Expose one client-facing surface (the reservation service) for creating reservations, adding baggage, completing or abandoning payment, cancelling, and joining waitlists—while delegating seat and baggage rules to dedicated services.

3. **Transparent hold and payment behavior**  
   - Hold a seat for a limited time (e.g. 120 seconds).  
   - Before payment, verify with the seat service that the hold is still valid; if it has expired, mark the reservation failed and return a clear message.  
   - Allow users to complete (pay) or abandon (fail) the reservation explicitly.

4. **Integrated baggage and fees**  
   Let users add baggage to a reservation, compute overweight charges via the baggage service, and mark the reservation as awaiting payment when fees apply.

5. **Waitlist and re-use of released seats**  
   Allow users to join a waitlist for a specific seat; when a seat is released (e.g. after cancellation), the system can assign it to the next eligible waitlisted passenger.

6. **Resilience to abuse and bots**  
   Detect clients that rapidly access seat-related operations (e.g. many reservations or waitlist requests in a short window) and temporarily block further access with a clear message and retry-after guidance.

7. **Event-aware and consistent**  
   When a hold expires or a seat is released, the reservation service can update reservation state (e.g. mark failed) so that later payment attempts receive a consistent, helpful response (e.g. “seat has been released”).

---

## Key Users

| User | Description | Primary interactions |
|------|-------------|----------------------|
| **Passenger (traveler)** | End customer booking a flight and optional baggage. | Creates a reservation (hold seat), adds baggage, completes payment or abandons, cancels a completed reservation, joins waitlist for a seat. |
| **Travel agent / front-end app** | Human or application acting on behalf of passengers (e.g. airline website, mobile app, agent desktop). | Calls the same reservation APIs as the passenger flow: create reservation, add baggage, complete or fail, cancel, join waitlist. |
| **Operations / support** | Internal staff or systems that need to understand reservation and seat state. | May query reservation and seat state (e.g. GET reservation) to assist passengers or troubleshoot. |
| **System / automation** | Downstream services (e.g. seat service, event listeners). | Seat service emits events (e.g. hold expired); reservation service consumes them to keep reservation status in sync and to block or inform abusive clients. |

---

## Out of Scope (for this PRD)

- Flight search, schedule, or inventory management.
- User authentication and authorization (identity is represented by identifiers such as `passengerId`).
- Actual payment processing (the system records “payment” or “abandon” and may integrate with a payment provider later).
- Check-in and boarding pass issuance (baggage and fees are in scope; full check-in flow is not defined here).
