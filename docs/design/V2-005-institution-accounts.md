# V2-005: Institution accounts (backend design)

| | |
|---|---|
| **Story** | V2-005: As a system administrator, I want to register a new institutional account for an organisation such as a hospital, court, or government office, so that its staff can access VRI services under a structured, isolated account. |
| **Owner** | Zipporah T (backend), Irene Katusiime (frontend) |
| **Sprint** | 2 |
| **Status** | **Implemented** (14 Oct 2026). Revised the same day: admin-created institutions removed (see section 5). |

## 1. Flow (from the SRS and the Institutional User flow chart)

```
Institution signs up (self-registration)  ->  PENDING
System admin verifies the account (V2-046) ->  APPROVED, or REJECTED with a rejection email
Approved institution signs in              ->  its administrator creates staff accounts (V2-006)
```

- SRS FR-UM-006: "organization registration, administrative provisioning, credentials, privileges and staff accounts".
- SRS FR-INS-006: institution administrators manage staff access within the organisation scope.
- The person who registers the institution is its administrator.

## 2. What this story provides

| Piece | Where |
|---|---|
| Self-registration creates the institution as **PENDING** | `POST /api/v1/auth/register/institution/` (V2-001/003, unchanged) |
| The institution can see its verification result | `institution_status` in the `user` object of login, registration and `GET /auth/me/`: `PENDING`, `APPROVED` or `REJECTED` (`null` for other roles) |
| Only approved institutions get institution access | `IsApprovedInstitution` (`apps/core/permissions.py`). Pending/rejected institutions can sign in but get `403` with `code: institution_pending / institution_rejected` |
| Each institution only sees its own data | `user_institution(request.user)`: institution endpoints never take an institution id from the request |
| The institution's own details | `GET /api/v1/institutions/me/` (approved institutions only) |

## 3. Endpoint

### `GET /api/v1/institutions/me/`

```json
{ "id": 7, "organisation_name": "Mulago Hospital", "organisation_type": "HOSPITAL",
  "organisation_type_other": "", "status": "APPROVED", "created_at": "..." }
```

`403` with a `code` for pending/rejected institutions, `403` for other roles, `401` when not signed in.

## 4. Contract for the frontend (Irene)

- **WEB-03** (`PendingReviewPage`): read `session.user.institution_status` to show "waiting for review", "approved"
  or "not approved". Call `/auth/me/` again to refresh it.
- Institution pages should handle `403` with `code: institution_pending / institution_rejected`.

## 5. Removed: system admins creating institutions

An earlier version also let a system admin create an already-approved institution
(`POST /api/v1/manage/institutions/` and a Django admin form). It was removed because neither the SRS nor the user
flow has that path: institutions register themselves and the system admin verifies them. Audit migration `0003`
removes its `institution_created` event (`0002` stays, as it may already be applied).

## 6. Tests

`apps/institutions/test_access.py`: `institution_status` in `/auth/me/`, approved-only access with codes for
pending/rejected, other roles blocked, own institution only.
