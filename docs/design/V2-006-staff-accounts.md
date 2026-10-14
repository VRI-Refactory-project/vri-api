# V2-006: Institution staff accounts (backend design)

| | |
|---|---|
| **Story** | V2-006: As an institutional administrator, I want to create, modify, and revoke staff accounts within my organisation's account, so that individual staff members (e.g., doctors, police officers) have access matched to their current role and employment status. |
| **Owner** | Zipporah T (backend), Irene Katusiime (web) |
| **Sprint** | 2 |
| **Status** | **Implemented** (14 Oct 2026), waiting for review. |

## 1. Sources

- **SRS 2.4:** *Institutional User* (start/schedule sessions, request interpreters, institution-scoped history) and
  *Institution Admin* (all of that **plus** manage institution staff access).
- **SRS FR-INS-006:** institution administrators manage staff access within the organisation scope. **AT-08:** no
  access outside the user's role/organisation.
- **SRS 8.1:** *Institution Staff* = user-to-institution membership, role, permissions and status. *Audit Event* =
  actor, action, **target**.
- **User flow (Institutional User):** verified institution → create staff accounts → **send account details to
  staff** → staff log in → institution admin manages staff accounts.

## 2. Decisions

| Question | Decision |
|---|---|
| Roles | `ADMIN` (manage staff + everything) and `STAFF` (Institutional User). The registrant is the first `ADMIN`. A free-text `job_title` (Doctor, Police officer) is shown only; it is not a permission. |
| Platform role | All institution people keep the user role `institution`; `InstitutionStaff` says which institution and which role inside it. |
| Login details | Email is **required**. A temporary password is emailed; it must be changed at first sign-in. If the email fails, nothing is created (`503`). |
| What an admin can change | Name, job title, role. **Not** phone or email (the person's sign-in details). |
| Revoke | Deactivates the account, cancels every refresh token, and the access token in use fails on the very next request (`401`). The account and history are kept. |
| Reinstate | Allowed: access comes back with the same password and role. |
| Safety | An admin cannot revoke themselves, and the last active admin cannot be demoted or revoked. Revoking twice changes nothing. |

## 3. Endpoints (signed-in ADMIN of an APPROVED institution)

All under `/api/v1/institutions/me/staff/`; only the caller's own institution, so another institution's staff
member is `404`.

| Method and path | Does |
|---|---|
| `GET  /me/staff/?status=ACTIVE\|REVOKED` | List staff (WEB-01) |
| `POST /me/staff/` | Add: `full_name`, `phone_number`, `email` (required), `job_title`, `role` (`ADMIN`/`STAFF`, default `STAFF`) |
| `GET  /me/staff/{id}/` | One staff member |
| `PATCH /me/staff/{id}/` | Change `full_name`, `job_title`, `role` (WEB-02/03) |
| `POST /me/staff/{id}/revoke/` | Revoke access immediately (WEB-03) |
| `POST /me/staff/{id}/reinstate/` | Give access back |

Staff member shape:

```json
{ "id": 12, "user_id": 31, "full_name": "Dr Sarah Nakato", "phone_number": "+256772111000",
  "email": "sarah@mulago.example", "role": "STAFF", "job_title": "Doctor", "status": "ACTIVE",
  "last_login": null, "created_at": "...", "revoked_at": null }
```

Errors: `400` field errors (same format as registration) or `{"detail": ...}` for the safety rules; `403` with
`code: institution_admin_required` (a STAFF user), `institution_pending` / `institution_rejected`; `503` email failed.

`user` in login, registration and `/auth/me/` gains `institution_role`: `ADMIN`, `STAFF` or `null`.

## 4. Contract for the web app (Irene)

- Show staff management only when `user.institution_role === "ADMIN"`.
- New staff sign in with the emailed password and get `must_change_password: true`: send them to "choose your own
  password" (`POST /auth/password/change/`).
- After a revoke, the person is signed out on their next request (`401`).

## 5. Code

| Where | What |
|---|---|
| `apps/institutions/models.py` | `StaffRole`, `InstitutionStaff`; migration `0003` also makes every existing registrant an `ADMIN` |
| `apps/institutions/staff.py` | create (email + rollback), update, revoke, reinstate, keep-an-admin rule |
| `apps/institutions/serializers.py`, `views.py`, `urls.py` | Staff endpoints |
| `apps/core/permissions.py` | `user_staff_membership()`, `user_institution()` (now via membership), `IsInstitutionAdmin` |
| `apps/audit/*` | `target_user` and events `staff_created/updated/revoked/reinstated` (migration `0004`) |

**For later stories:** institution endpoints for sessions, bookings and history use `IsApprovedInstitution` (any
active staff) and `user_institution()`; staff-management endpoints use `IsInstitutionAdmin`.

## 6. Tests

`apps/institutions/test_staff.py` (22): registrant is admin; create (email, temporary password, audit, rollback,
validation); first sign-in; list and status filter; isolation between institutions; STAFF vs ADMIN; pending/
rejected institutions; other roles; update and the last-admin rule; revoke (access token rejected at once, refresh
token cancelled, sign-in refused, account kept, audit), revoke twice, self-revoke, reinstate.
