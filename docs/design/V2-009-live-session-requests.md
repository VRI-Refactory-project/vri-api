# V2-009: Request an interpreter now (backend design)

| | |
|---|---|
| **Story** | V2-009: As a Deaf user, I want to request an interpreter immediately for a live video session, so that I can communicate in real time with a hearing person using Uganda Sign Language without pre-scheduling. |
| **Owner** | Zipporah T (backend), Hope Trudy N (frontend) |
| **Sprint** | 3 |
| **Status** | **Implemented** (20 Oct 2026), waiting for review. |

Builds on Catherine's `ServiceRequest` (#42) and V2-022's offers and routing (#44). This story adds what V2-009 still
needed on top of them.

## 1. Status names

The team keeps the names already on `dev`. Where the cards use other words:

| Card says | Status |
|---|---|
| CREATED | `CREATED` (new in this story) |
| QUEUED | `PENDING` |
| OFFERED | `OFFERED` |
| ACCEPTED (V2-010) | `ASSIGNED` |

```
CREATED -> OFFERED    an interpreter was found (V2-022 routing)
        -> PENDING    nobody suitable is free right now
PENDING -> CANCELLED  the requester's app stopped checking for longer than the grace window, or they cancelled
```

`CREATED` only lasts inside the create call: the response is always `OFFERED` or `PENDING`. It counts as open,
so the one-open-request rule covers it. `services.check_transition` refuses moves that are not allowed (for example
a cancelled request can never be offered).

## 2. What this story adds

| Need | How |
|---|---|
| CREATED state (BE-01) | New status; `create_request` starts there, then routing moves it on. |
| Allowed moves (BE-03) | `ALLOWED_TRANSITIONS` and `check_transition`, used when creating and offering. |
| Context | `GENERAL` (default), `MEDICAL` or `LEGAL`. Optional: leaving it out used to crash the create call. |
| Duplicate requests (BE-04, C-12 unresolved) | Already on `dev` (one open request, `request_key`). The `409` now also returns `open_request_id`. |
| Disconnect (BE-05, `REQUESTER_DISCONNECT_GRACE_WINDOW` TBD) | Placeholder **2 minutes** (`REQUESTER_DISCONNECT_GRACE_WINDOW_SECONDS`). Every read by the requester's app (detail, list, `mine/`) counts as "still here". A `CREATED`/`PENDING` request whose app has been silent longer is cancelled with `cancel_reason = REQUESTER_DISCONNECTED`. A request never checked on counts from when it was made. Once offered, the rule no longer applies. |
| Timeouts without a worker | The free hosting has no background worker. Timeouts are applied whenever a request is read, and by the sweep for requests nobody reads. |
| Audit | `AuditLog.details` is new. Every request logs `request_created` (by the requester, with context, priority and role), `request_offered` (target: the interpreter) and `request_cancelled` for a disconnect (target: the requester). |

## 3. Endpoints

Already on `dev` (unchanged paths): `POST/GET /api/v1/requests/`, `GET /api/v1/requests/mine/`,
`GET /api/v1/requests/{id}/`, `POST /api/v1/requests/{id}/cancel/`.

New:

| Method and path | Who | Does |
|---|---|---|
| `POST /api/v1/requests/sweep/` | Uptime monitor | Header `X-Sweep-Token: <REQUEST_SWEEP_TOKEN>`. Applies timeouts; safe to call repeatedly. `404` when no token is configured. |

`python manage.py sweep_requests` runs the same sweep locally.

New response fields: `cancelled_at`, `cancel_reason`. Apps should poll the detail endpoint well within the grace
window (for example every 5 to 10 seconds) while waiting.

## 4. Not in this story

- Accept, decline, offer expiry: V2-010.
- Queue processing: V2-023. Queue timeout (`FAILED`), institution staff requests and emergency audit: V2-024.
