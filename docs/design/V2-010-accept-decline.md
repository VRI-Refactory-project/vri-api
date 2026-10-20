# V2-010: Accept or decline an offered request (backend design)

| | |
|---|---|
| **Story** | V2-010: As an interpreter, I want to accept or decline a session request that has been routed to me within a defined response window, so that I only take sessions I can properly handle, and unaccepted requests are promptly re-routed. |
| **Owner** | Zipporah T (backend), Irene M (web, already built) |
| **Sprint** | 3 |
| **Status** | **Implemented** (20 Oct 2026), waiting for review. |

Builds on V2-009 (#45) and V2-022 (#44, #46). Status names follow `dev`: the card's ACCEPTED is `ASSIGNED`.

## 1. Already on `dev` from V2-022 #46

| Task | Where |
|---|---|
| BE-05 offer expiry | `expire_stale_offers`. The card's `INTERPRETER_RESPONSE_TIMEOUT` is `REQUEST_OFFER_EXPIRY_SECONDS` (60 s, TBD). |
| BE-06 expired offers back to routing | The request returns to `PENDING`; `refresh_request` / `sweep` offer it to the next interpreter. |
| BE-07 separate outcomes | `RequestOffer.outcome`: `ACCEPTED`, `DECLINED`, `EXPIRED`, `WITHDRAWN`, each with `responded_at`. |

## 2. What this story adds

| Task | How |
|---|---|
| BE-01 accept | `services.respond_to_offer(offer, "accept")`: offer `ACCEPTED`, request `ASSIGNED` with `assigned_at`, in one locked transaction. |
| BE-02 accepted before the session starts | The request is `ASSIGNED` before anything else happens. Connecting the session is V2-011. |
| BE-03 refuse expired or inactive offers | The offer's expiry is applied first. Expired, withdrawn (requester cancelled), already answered differently, or no longer this interpreter's: `409` with a message. The same answer twice returns `200` (safe retry). |
| BE-04 decline | Offer `DECLINED`; the request goes straight to the next eligible interpreter (never the one who declined), or back to `PENDING`. |
| Busy interpreter | Accepting switches the interpreter's `is_available` off, so routing does not offer them another request mid-session. They switch it back on from their availability screen. V2-011 (Sprint 4) should switch it back automatically when the session ends. |
| Audit | `offer_accepted` and `offer_declined` (by the interpreter, target: the requester), `offer_expired` (target: the interpreter). |

## 3. Endpoints (signed-in interpreter)

These match the offers screen Irene built (`vri-web/src/services/offersApi.ts`), so it needs no changes. **They use
camelCase names and lowercase statuses on purpose**, unlike the rest of the API; renaming them breaks the screen.

| Method and path | Does |
|---|---|
| `GET /api/v1/interpreters/me/offers/` | Offers waiting for an answer: `id` (offer id, text), `title`, `institutionName`, `sector`, `language`, `participants`, `expiresAt`. |
| `POST /api/v1/interpreters/me/offers/{id}/respond/` | Body `{"action": "accept" \| "decline"}`. `200 {"status": "accepted" \| "declined"}`, `409 {"detail", "code": "offer_not_active"}`. |
| `GET /api/v1/interpreters/me/offers/{id}/state/` | `{"status": "pending" \| "accepted" \| "declined" \| "expired" \| "rerouted"}`. `rerouted` means the requester cancelled (`WITHDRAWN`). |

Another interpreter's offer is `404`. `institutionName` is the institution's name for institution staff, otherwise
the requester's name. `sector` is the context (General, Medical, Legal).

V2-022's `GET /api/v1/interpreters/me/requests/` is unchanged.

## 4. Not in this story

- What happens when the requester is offline as the interpreter accepts (`REQUESTER_RECONNECT_WINDOW`): the session, V2-011.
