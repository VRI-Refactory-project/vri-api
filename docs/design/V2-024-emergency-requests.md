# V2-024: Emergency interpreter requests (backend design)

| | |
|---|---|
| **Story** | V2-024: Emergency interpreter requests. |
| **Owner** | Zipporah T (backend) |
| **Sprint** | 3 |
| **Status** | **Implemented** (21 Oct 2026), waiting for review. |

Builds on V2-009 (#45), V2-010 (#47) and V2-022 (#44, #46). Status names follow `dev`: the card's QUEUED is
`PENDING`.

## 1. Where each task is done

| Task | Where |
|---|---|
| Emergency priority, chosen when the request is made | V2-022 #44 (`priority`). This story adds tests that it can never change: no endpoint edits a request (`PATCH`/`PUT` are `405`), and a retry with the same `request_key` returns the original. |
| Queue order: emergency first, then oldest | V2-022 #46 (`dispatch_pending`). |
| Safe retries (`request_key`) | V2-022 #44. |
| Disconnect grace window | V2-009 #45. |
| **Who may request** | **This story.** Deaf users and staff (admins and staff) of an **approved** institution. Other roles get `403`; pending or rejected institutions get `403` with `institution_pending` / `institution_rejected`. Staff only ever see their own requests. |
| **Requester cancel** | Cancel itself is V2-022 #44 (requester only, only while open). **This story** records `cancel_reason = REQUESTER_CANCELLED` and audits it. |
| **Timeout -> FAILED** | The timeout is V2-022 #46 (`REQUEST_MAX_WAIT_SECONDS`, 300 s, TBD). **This story** adds what happens next, below. |
| **Audit** | `request_created` (V2-009, includes priority and role), plus **this story's** `request_cancelled` (by the requester) and `request_failed`. |

## 2. When nobody takes a request in time

`fail_overdue_requests` marks the request `FAILED` (reason `no_interpreter_available`) and, in the same transaction:

- **The requester** gets a notification: "No interpreter available. We couldn't find an interpreter in time. Please try
  again." (`data`: `request_id`, `screen: request-details`, `priority`).
- **For an emergency only,** every active platform admin gets: "Emergency request not answered. No interpreter took
  emergency request #id in time. Please follow up." Normal requests do not alert admins, so they are not flooded.
- An audit entry `request_failed` (target: the requester, with reason and priority).

Notifications use the existing `general` category; push messages are sent only after the change is saved. It happens
once per request, whether the app's poll or the sweep notices first.

## 3. Endpoints

No new endpoints. `POST/GET /api/v1/requests/`, `GET /api/v1/requests/mine/`, `GET /api/v1/requests/{id}/` and
`POST /api/v1/requests/{id}/cancel/` now also accept staff of approved institutions.
