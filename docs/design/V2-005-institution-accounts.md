# V2-005: Institution accounts (backend design)

| | |
|---|---|
| **Story** | V2-005: As a system administrator, I want to register a new institutional account for an organisation such as a hospital, court, or government office, so that its staff can access VRI services under a structured, isolated account. |
| **Owner** | Zipporah T (backend), Irene Katusiime (frontend) |
| **Sprint** | 2 |
| **Status** | **Implemented** (14 Oct 2026), waiting for review. |

## 1. Two ways an institution joins

| | Path | Status | Task |
|---|---|---|---|
| A | The institution registers itself on the web (`POST /auth/register/institution/`, already on `dev`) | **PENDING** until a platform admin approves or rejects it (V2-046, separate branch) | BE-01 |
| B | A platform admin creates it (API or Django admin site) | **APPROVED** at once | BE-02 |

## 2. Decisions

| Question | Decision |
|---|---|
| Credentials for admin-created institutions | The server generates a **temporary password** (like `K7PM-4Q9X`) and **emails** it to the institution's administrator. The platform admin never sees it. It must be changed at first sign-in (`must_change_password`). **No expiry**, like the admin password reset. |
| Email fails to send | **Nothing is created** (rolled back), and the admin is told to check the email settings. No account exists whose password nobody knows. |
| Where admins create institutions | `POST /api/v1/manage/institutions/` and the **Django admin site** ("Add institution profile"). Both use the same code (`apps/institutions/credentials.py`). |
| Terms and Privacy | Only the institution can accept them, so `terms_accepted_at` stays empty for admin-created accounts. |
| Isolation | Institution endpoints use `IsApprovedInstitution` and read data only through `user_institution(request.user)`, never an id from the request. |

## 3. Endpoints

### `POST /api/v1/manage/institutions/` (platform admins only)

Request: the same fields as self-registration, minus `password` and `terms_accepted` (ignored if sent).
`full_name`, `phone_number` and `email` (required) describe the institution's administrator.

```json
{ "organisation_name": "Mulago Hospital", "organisation_type": "HOSPITAL", "organisation_type_other": "",
  "full_name": "Grace Namutebi", "phone_number": "0772555000", "email": "grace@mulago.example" }
```

`201`:

```json
{ "detail": "Institution created and approved. Login details sent to gr***@mulago.example.",
  "institution": { "id": 7, "organisation_name": "Mulago Hospital", "organisation_type": "HOSPITAL",
                   "organisation_type_other": "", "status": "APPROVED", "created_at": "..." },
  "user": { "id": 31, "role": "institution", "must_change_password": true, "institution_status": "APPROVED",
            "...": "..." } }
```

Errors: `400` same field errors as self-registration (duplicate phone/email, missing email, "Other" without a
description), `401`/`403` not a platform admin, `503` the email could not be sent (nothing created).

### `GET /api/v1/institutions/me/` (approved institutions only)

The caller's own institution (same shape as `institution` above). `403` with
`code: "institution_pending"` or `"institution_rejected"` and a readable `detail` for institutions that are not
approved; `403` for other roles.

### Change to existing responses

`user` in login, registration and `GET /auth/me/` gains `institution_status`: `PENDING`, `APPROVED` or `REJECTED`
for institution accounts, `null` for every other role.

## 4. Contract for the frontend (Irene)

- **WEB-03** (`PendingReviewPage`): read `session.user.institution_status` (from login or `/auth/me/`) to show
  "waiting for review", "approved" or "not approved", instead of the fixed text. Call `/auth/me/` again to refresh.
- Admin-created institutions sign in with the emailed password and get `must_change_password: true`: the app
  must send them to a "choose your own password" screen (`POST /auth/password/change/`).
- Institution pages should handle `403` with `code: institution_pending / institution_rejected`.

## 5. Data model

No new tables. `audit.AuditLog` gets the event `institution_created` (who created it; the institution is in the
server log line `institution_created_by_admin`). `InstitutionProfile.status` is set to `APPROVED` at creation.

## 6. Tests

`apps/institutions/test_admin_create.py` (11): admin-only, validation, email content and recipient, the admin never
sees the password, rollback when the email fails, audit entry, first sign-in and forced password change, and the
Django admin form. `apps/institutions/test_access.py` (6): `institution_status` in `/auth/me/`, approved-only
access with codes for pending/rejected, other roles blocked, own institution only.

## 7. Files

`apps/institutions/{serializers,credentials,manage_views,manage_urls,views,urls,admin}.py`,
`apps/institutions/templates/admin/institutions/create_institution.html`; `apps/core/permissions.py`;
`apps/accounts/serializers.py`; `apps/audit/models.py` and migration `0002`; `config/urls.py`.

## 8. Notes

- The approve/reject endpoints (V2-046) are on `feat/sprint-2-institutional-availability`, not yet in `dev`. It
  also creates `apps/institutions/urls.py` and `views.py` and adds audit events: merging the two is "keep both".
- Staff accounts inside an institution (V2-006) should use `IsApprovedInstitution` and `user_institution()`.
