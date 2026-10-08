# V2-045: Interpreter verification (backend design)

| | |
|---|---|
| **Story** | V2-045: As a system administrator, I want to review interpreter certification and activate or deactivate interpreter accounts, so that only qualified, verified interpreters can provide services. |
| **Owner** | Zipporah (backend), John Busuulwa (frontend) |
| **Estimate** | 2 days |
| **Status** | In progress |

## 1. What the backlog requires

**Acceptance criteria**

1. Given a new interpreter has submitted certification, when an admin reviews it, then the admin can **approve or reject** their verification (V2-003).
2. Given an interpreter's certification lapses or a concern is raised, when an admin **deactivates** their account, then they **no longer receive new requests, effective immediately**.

**Backend tasks**

| Task | What it means here |
|---|---|
| BE-01 | Approve (and so activate) a pending interpreter |
| BE-02 | Reject an interpreter and record the decision |
| BE-03 | Deactivate an interpreter; new request routing stops immediately |
| BE-04 | Record every admin decision in an audit log |
| BE-05 | Email the interpreter when they are approved or rejected |
| BE-06 | Include the rejection reason in that email |

The frontend (WEB-01..04) needs a verification queue, a review page that shows the certificates, approve/reject
actions with a reason, and a deactivate action. The read-only endpoints below exist to serve those screens.

## 2. Statuses and transitions

`InterpreterProfile.verification_status` already has `INACTIVE` (pending review, the default at registration),
`APPROVED` and `REJECTED`. This story adds **`DEACTIVATED`**, so "pending" and "switched off by an admin" are not
mixed up.

```text
INACTIVE (pending) --approve--> APPROVED --deactivate--> DEACTIVATED
      |                            ^                          |
      +--reject--> REJECTED --approve+ <------reactivate-------+
```

| Action | Allowed from | Result | Reason |
|---|---|---|---|
| approve | `INACTIVE`, `REJECTED` | `APPROVED` | optional; needs at least one uploaded certificate |
| reject | `INACTIVE` | `REJECTED`, `is_available = False` | **required** |
| deactivate | `APPROVED` | `DEACTIVATED`, `is_available = False` | **required** |
| reactivate | `DEACTIVATED` | `APPROVED` (the interpreter switches themselves to available again) | optional |

Any other transition returns **400** and changes nothing. Each action locks the profile row
(`select_for_update`) inside a transaction, so two admins acting at once cannot both succeed.

**Why "effective immediately" holds:** routing uses `services.get_eligible_interpreters()`, which only returns
`APPROVED`, active, available interpreters. Deactivation changes the status and sets `is_available = False` in one
transaction, so the very next routing query excludes the interpreter. No routing code changes.

A deactivated interpreter can still sign in and see their status. Blocking sign-in entirely is a separate
action (`User.is_active`, Django admin).

## 3. Audit log

New model **`VerificationDecision`** (in `apps/interpreters/`): `interpreter`, `admin`, `action`, `reason`,
`previous_status`, `new_status`, `created_at`. Both foreign keys use `PROTECT`, so the history cannot be deleted
by deleting a user. It is read-only in Django admin. Every decision is also logged
(`interpreter_verification_<action> admin=<id> interpreter=<id>`), like V2-008's admin reset.

On rejection the reason is also copied to the reviewed certificate's existing `rejection_reason`, which the
interpreter already sees in `GET /interpreters/me/`.

## 4. Endpoints

All are **admin only** (`apps.core.permissions.IsAdminRole`): not signed in = 401, any other role = 403.
They live under `/api/v1/manage/`, Swagger tag `Manage`.

| Method | URL | Purpose |
|---|---|---|
| GET | `/manage/interpreters/?status=INACTIVE` | Verification queue (interpreter accounts only) |
| GET | `/manage/interpreters/{id}/` | Review page: profile, certificates, decision history |
| GET | `/manage/interpreters/{id}/certifications/{cert_id}/file/` | Download a certificate (admins only, never a public URL) |
| POST | `/manage/interpreters/{id}/approve/` | `{ "reason": "" }` optional |
| POST | `/manage/interpreters/{id}/reject/` | `{ "reason": "..." }` required |
| POST | `/manage/interpreters/{id}/deactivate/` | `{ "reason": "..." }` required |
| POST | `/manage/interpreters/{id}/reactivate/` | `{ "reason": "" }` optional |

Each action returns the updated interpreter (status, certificates, decision history).

## 5. Notifications

Approval and rejection send an email to the interpreter (email is required at interpreter registration).
The rejection email includes the reason. Emails are sent **after the transaction commits**; a failed email is
logged and does not undo the decision. In development emails are printed in the backend logs, as for recovery codes.

## 6. Certificate files

Uploads are saved under `MEDIA_ROOT` (new setting, `<project>/media/`, already git-ignored). Certificates are
personal documents, so `MEDIA_URL` is **not** served publicly; admins download them through the protected
endpoint above.

## 7. Out of scope / open questions

1. **Resubmission after rejection.** Uploading a new certificate does not move a rejected interpreter back to
   pending. A later story should decide this.
2. **Persistent storage on staging.** Render's disk is wiped on redeploy, so uploads (all of them, not just
   certificates) are lost there until storage is made persistent (Render disk or S3-compatible storage).
3. **Emails for deactivation/reactivation** are not required by the backlog and are not sent.
4. Interpreter self-service routes (`/interpreters/me/`, certificate upload) still accept any signed-in user
   (noted under NEW-01). The verification queue only lists `role = interpreter` accounts, so stray profiles do
   not appear in it.
