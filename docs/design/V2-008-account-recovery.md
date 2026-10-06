# V2-008: Account recovery (backend design)

| | |
|---|---|
| **Story** | V2-008: As a registered user who has lost access to my account, I want to recover my access through a secure, defined recovery process, so that I am not permanently locked out. |
| **Owner** | Trudy (backend), Munguromo Irene (frontend) |
| **Sprint** | 1 (2 days) |
| **Status** | **Implemented** (6 Oct 2026). Method confirmed: email only, no SMS budget. |

## 1. What the backlog requires

1. The system **verifies the user's identity through a defined recovery method** before restoring access.
2. After recovery, the user's **account data and role are unchanged**.
3. For an account that does not exist, the system **neither confirms nor denies** that it exists.

## 2. Recovery method (decided)

| Who | How they recover |
|---|---|
| **Users with an email** (all interpreters, anyone who added one) | **6-digit code by email**, entered in the app with a new password/PIN |
| **Phone-only users** | **Admin-assisted reset.** An admin verifies the person's identity offline, then resets the PIN in the app. The admin must confirm with a **code sent to the admin's own email**. The user gets a temporary PIN and must change it at next sign-in. |
| SMS | Not now: no budget. The API is code-based, so SMS can be added later without changing the app. |

Why a code and not an emailed link: a link has to open the React Native app (deep linking), which is extra
frontend work and breaks when the email is opened on a different device. A code is typed into the app, and it is
exactly what SMS would send if it is added later.

**Encourage users to add an email** at registration or in their profile: it is the only way to recover an
account without an admin.

## 3. Flow

```
App                                   API                                     User's email
 |  POST /auth/recovery/request/        |                                          |
 |  { identifier }  ------------------> |  find user (phone or email)              |
 |                                      |  if found, active and has email:         |
 |                                      |    create code, invalidate old ones ---> |  "Your SignConnect code: 482913"
 |  <-- 200 (always the same message)   |                                          |
 |                                      |                                          |
 |  POST /auth/recovery/confirm/        |                                          |
 |  { identifier, code, new_password }->|  check code (hash, expiry, attempts)     |
 |                                      |  validate + set new password             |
 |                                      |  mark code used, sign out other devices  |
 |  <-- 200 + new JWT tokens            |                                          |
```

Two endpoints for self recovery, not three: the app shows one screen with the code and the new password together, so a separate
"verify code" step adds an API call and a temporary token without improving security.

## 4. Endpoints

All under `/api/v1/`, tag `Auth`, `AllowAny`, rate-limited.

### `POST /auth/recovery/request/`

Request: `{ "identifier": "0772123456" }` (phone or email)

Response: **always `200`** with the same body, whether or not the account exists:
```json
{ "detail": "If an account matches, we have sent a recovery code to its registered email. If you registered with a phone number only, please contact support to reset your PIN." }
```

Server side:
- Look up the user with `find_user_by_identifier()`.
- Send a code **only if** the user exists, is active and has an email. Otherwise do nothing.
- Invalidate any earlier unused codes for that user (only the newest code works).
- Per-user cooldown: if a code was sent less than 60 seconds ago, do not send another (still return the same 200).

### `POST /auth/recovery/confirm/`

Request:
```json
{ "identifier": "0772123456", "code": "482913", "new_password": "..." }
```

Success: **`200`** with the same body as `/auth/login/` (`access`, `refresh`, `user`), so the app signs the user
straight in.

Failure: **`400`** with one generic message for every code problem (unknown account, wrong code, expired, used,
too many attempts):
```json
{ "code": ["Invalid or expired code. Request a new one."] }
```
Password problems return the normal field errors under `new_password` (same rules as registration),
**only after** the code has been verified, so they cannot be used to probe accounts.

### Admin-assisted reset (phone-only users)

Admin only (`role = admin`). A regular admin cannot reset another admin, and nobody resets their own account
this way.

| Step | Endpoint | What happens |
|---|---|---|
| 0 | (offline) | The admin verifies the person's identity: call back the registered number, or meet in person with an interpreter. Written procedure: open question 2. |
| 1 | `POST /manage/users/{id}/reset-password/request/` | A 6-digit code is emailed to the **admin's own email**. Response says where (masked). Admins must have an email on their account. |
| 2 | `POST /manage/users/{id}/reset-password/confirm/` `{ code, temporary_password }` | Sets the user's temporary password/PIN, sets `must_change_password = true`, signs the user out everywhere, logs `admin_reset_completed` with both IDs. |
| 3 | (offline) | The admin gives the temporary PIN to the user in person. |

The code is tied to one target user: a code requested for user A cannot reset user B.

### `POST /auth/password/change/` (signed in)

`{ current_password, new_password }`. Used after an admin reset (the app sees `must_change_password: true` in the
sign-in response and opens this screen) and for normal password changes. Clears the flag, signs out other devices
and returns fresh tokens.

## 5. Data model (`apps/accounts/`)

```
User (new field)
└── must_change_password   true after an admin reset, false after the user changes it

RecoveryCode
├── user          who receives the code (the user, or the admin for admin resets)
├── target_user   admin resets only: the account being reset
├── purpose       self_recovery / admin_reset
├── channel       "email"
├── code_hash     hash of the 6-digit code (never stored in plain text)
├── created_at, expires_at (+15 minutes)
├── attempts      failed tries on this code (max 5)
├── used_at       null until used
└── requested_ip
```

A code is valid only if: newest unused code for that user, purpose and target; not expired; `attempts < 5`.

## 6. Security rules

| Rule | Value |
|---|---|
| Code | 6 digits from Python's `secrets` module |
| Storage | Hashed (Django `make_password` / `check_password`, constant-time comparison) |
| Lifetime | 15 minutes, single use |
| Wrong attempts | 5 per code, then the code is dead |
| Rate limit: request | 5 per hour per IP (`recovery_request` throttle) + 60-second cooldown per user |
| Codes per account | Max 5 per hour and 10 per day |
| Account lock | 15 wrong codes in 24 hours locks recovery for that account for 24 hours (stops slow guessing from many IPs) |
| Email failure | Logged, the code is deleted, and the response stays the same neutral 200 |
| Rate limit: confirm | 10 per minute per IP (`recovery_confirm` throttle) |
| Deactivated accounts | No code is sent and confirm fails with the generic error. Recovery never reactivates an account. |
| After success | Other unused codes deleted; other devices signed out (refresh tokens blacklisted; access tokens already issued expire within 30 minutes) |
| Unchanged | Only the password changes. Role, profile, verification status and `is_active` are untouched. |
| Temporary password | Enforced by the server: after an admin reset, every endpoint except `/auth/password/change/` and `/auth/me/` returns 403 `password_change_required` until the user picks a new one |
| Same code twice | Claimed atomically: two simultaneous requests cannot both use one code |
| Pending accounts | Pending interpreters/institutions can recover; they stay pending. |
| Logging | Log `recovery_requested`, `recovery_succeeded`, `recovery_failed` with user id and IP (never the code or password) |

**Known limit:** sending email takes time, so a request for an existing account can respond slightly slower than
one for a non-existent account. Acceptable for Sprint 1. The fix later is to send email in the background
(a task queue), which the notification stories will need anyway.

## 7. Delivery

- All sending goes through `_send_code_email()` in `apps/accounts/recovery.py`. If SMS is funded later, add
  an SMS branch there; the endpoints and the app do not change.
- **Development:** `EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"`. Codes print in the
  backend logs (`docker compose logs -f backend`), so nobody needs a real mailbox to test.
- **Production:** set `EMAIL_URL` (e.g. `smtp+tls://user:password@smtp.example.com:587`) and
  `DEFAULT_FROM_EMAIL` in `.env`. Choosing the email provider is a deployment decision, not Sprint 1.
- Email text: short, plain, in English. No link. Says the code expires in 15 minutes and to ignore the email if
  they did not ask for it.

## 8. Contract for the frontend (Irene)

| Screen | Calls | Shows |
|---|---|---|
| "Forgot password/PIN" | `/auth/recovery/request/` | The `detail` message as is, then the code screen |
| "Enter code + new PIN" | `/auth/recovery/confirm/` | On 200: sign in with the returned tokens. On 400: the error under `code` or `new_password`. "Resend code" button disabled for 60 seconds. |
| On 429 (too many tries) | | "Too many attempts. Please wait and try again." |
| After sign-in, if `user.must_change_password` is `true` | `/auth/password/change/` | "Choose a new PIN" screen before anything else |

## 9. Tests

`apps/accounts/test_recovery.py`, 29 tests: everything in the original list (neutral responses, cooldown, rate
limits, expiry, single use, 5-attempt limit, weak password keeps the code, role unchanged, deactivated accounts,
old refresh tokens blacklisted) plus the admin flow (code goes to the admin's email, temporary password and flag,
code tied to one user, non-admin 403, not signed in 401, admin without email, admin-on-admin, self-reset blocked)
and password change.

## 10. Files

| File | What |
|---|---|
| `apps/accounts/recovery.py` | All the rules: issuing and checking codes, sending email, signing out everywhere |
| `apps/accounts/views.py`, `urls.py` | The five endpoints |
| `apps/accounts/serializers.py` | Request/confirm/change-password validation |
| `apps/accounts/permissions.py` | `IsActiveAdmin` (to be replaced by Shadia's `IsAdmin`) |
| `apps/accounts/migrations/0002_...` | `RecoveryCode` + `must_change_password` |
| `config/settings/base.py` | Token blacklist app, throttles, email and logging settings |

## 11. Open questions

1. ~~Recovery method~~ **Decided:** email codes; admin-assisted reset confirmed by an email code to the admin.
2. **Identity check procedure for admin resets:** how does the admin confirm the person is who they say
   (call back the registered number, in person with an interpreter)? Write it down before launch.
3. ~~SMS~~ **No budget now.** Revisit if many phone-only users get locked out.
4. **Support contact** for phone-only users (shown by the app next to the recovery message): phone number,
   email or WhatsApp?
5. **Email provider for production** (deployment decision).
