# Contributing to vri-api

The rules for working on the SignConnect backend. Read this once before your first pull request.
Questions go to the tech lead (Trudy).

## 1. Before you write any code

1. Follow the setup in the [README](README.md) (Docker, `.env`, `pre-commit install`).
2. Run the setup check and send the full output to the tech lead:
   ```bash
   python scripts/check_setup.py
   ```
3. Start coding only when it says `RESULT: READY`.

## 2. Branches

| Branch | What it is | Who changes it |
|---|---|---|
| `main` | Stable releases | Tech lead only, through a pull request from `dev` |
| `dev` | Integration branch | Pull requests only. Never commit to it directly |
| `feature/...` | Your work on one story | You |

- **One branch per story**, created from an up-to-date `dev`:
  ```bash
  git checkout dev
  git pull
  git checkout -b feature/V2-001-deaf-registration
  ```
- **Name it** `feature/<story-id>-<short-description>` (use the ID from Trello). Use `fix/...` for bug fixes
  and `chore/...` for tooling or config changes.
- **Delete your branch** after it is merged (GitHub shows a button).
- **Keep it up to date:** if `dev` moves while you work, run `git pull origin dev` on your branch and fix any conflicts
  before asking for review.

## 3. Pull requests

- **Open a PR into `dev`**, never into `main`.
- **Keep it small:** one story, or part of one. Big PRs take longer to review and conflict more.
- **Describe** what it does, which story it covers, and anything the reviewer should test.
- **Before opening it,** make sure these pass on your machine:
  ```bash
  docker compose exec backend python manage.py test
  docker compose exec backend python manage.py spectacular --validate --fail-on-warn --file schema.yml
  ```
- **CI must be green.** A red check means the PR is not ready.
- **The tech lead reviews and merges every PR.** Do not merge your own.

## 4. Commits

- **Commit often,** with messages that say what changed, in the present tense:
  `Add DeafUserProfile model`, not `changes` or `fixed stuff`.
- **pre-commit runs on every commit.** If it says it fixed files, run `git add .` and commit again.
  If flake8 fails, fix the line it points to.
- **Never commit** `.env`, `.venv/`, database files or anything with real passwords or keys.

## 5. Where code goes

- **Each role has its own app under `apps/`** (`apps/deaf/`, `apps/interpreters/`, `apps/institutions/`,
  `apps/administration/`). Work in the app you own. See the roles doc for owners.
- **Shared code** (permissions, helpers) lives in `apps/core/`.
- **New app:**
  ```bash
  mkdir apps/<name>
  docker compose exec backend python manage.py startapp <name> apps/<name>
  ```
  Then set `name = "apps.<name>"` in its `apps.py`, add it to `INSTALLED_APPS` in `config/settings/base.py`,
  and include its `urls.py` under `/api/v1/` in `config/urls.py`.
- **Never name an app** `admin`, `sessions` or `auth`. Those clash with Django's built-in apps.

## 6. Users, registration and sign-in

- **One `User` model for every role** (`apps/accounts/models.py`). Never add role-specific fields to it;
  put them in your app's profile model, linked one-to-one to the user.
- **Refer to the user model** with `settings.AUTH_USER_MODEL` in models and `get_user_model()` in code.
  Never import `django.contrib.auth.models.User`.
- **Every registration endpoint subclasses `BaseRegistrationSerializer`** (`apps/accounts/serializers.py`).
  Do not write your own phone validation, duplicate checks or password hashing.
- **Look up users by phone or email** with `find_user_by_identifier()` and normalise phone numbers with
  `normalize_phone()` (both in `apps/accounts/utils.py`).
- **Protect endpoints** with the permission classes in `apps/core/permissions.py`. Every endpoint needs a
  permission class; only registration, sign-in and public lists use `AllowAny`.

## 7. Database migrations

- **Only run `makemigrations` for the app you own.**
- **Always `git pull` on `dev` before running `makemigrations`.**
- **Commit the migration file in the same PR** as the model change.
- **Never edit or delete a migration** that is already on `dev`. Add a new one instead.
- **Two PRs changed the same app?** The second one pulls `dev`, deletes its own new migration and runs
  `makemigrations` again.
- **Seed data** (for example the communication methods) goes in a data migration, so every machine gets it.

## 8. API design

- **All endpoints live under `/api/v1/`.** Admin-only endpoints go under `/api/v1/manage/`.
- **URLs are lowercase with hyphens and end with a slash:** `/api/v1/communication-methods/`.
- **Status codes:**

  | Code | When |
  |---|---|
  | 200 / 201 | Success / created |
  | 400 | Validation error, with the field errors in the response |
  | 401 | Not signed in |
  | 403 | Signed in, but not allowed (wrong role or not the owner) |
  | 404 | Does not exist |

- **Registration creates the user and profile in one transaction.** `BaseRegistrationSerializer` already does
  this. If you add extra steps, keep them inside `create_profile()`.

## 9. Swagger documentation

- **Every view gets `@extend_schema`** with a `tags` and a `summary`:
  ```python
  @extend_schema(tags=["Auth - Deaf Users"], summary="Register a Deaf/HoH user")
  ```
- **Tags:** `Auth`, `Auth - Deaf Users`, `Auth - Interpreters`, `Auth - Institutions`,
  `Deaf Users`, `Interpreters`, `Institutions`, `Manage`, `Health`.
- **The schema must validate with no warnings** (CI checks this).
- **Check your endpoint in Swagger** at http://localhost:8000/api/docs/ before opening the PR. To try
  protected endpoints, sign in with `POST /auth/login/`, click **Authorize** and paste the `access` token.

## 10. Tests

Every PR that adds or changes an endpoint includes tests for:

- the success case;
- a missing required field (400);
- the permission rules: someone who is not signed in (401) and someone with the wrong role (403);
- anything specific to the story, for example a duplicate phone number or an invalid communication method.

Tests go in your app's `tests.py` (or a `tests/` folder once it grows). Run them with
`docker compose exec backend python manage.py test`.

## 11. Packages

- **New package?** Add it to `requirements.txt` (or `requirements-dev.txt` for tools) **in the same PR**,
  with a version range, and mention it in the PR description.
- **After pulling a change to `requirements.txt`,** rebuild: `docker compose up --build`
  (or `pip install -r requirements-dev.txt` without Docker).

## 12. Secrets and settings

- **Settings come from `.env`.** If you add a setting, add it to `.env.example` with a safe placeholder and
  tell the team.
- **Settings changes in `config/`** go through the config owner (Shadia) and the tech lead's review.
- **Never put** passwords, keys or real phone numbers in code, tests or commits. Use made-up test data.

## 13. Getting help

- **Setup problem?** Run `python scripts/check_setup.py` and send the output.
- **Stuck for more than an hour?** Ask in the group chat with the error message and what you tried.
- **Not sure where something belongs?** Ask before building it. Moving code later costs more.
