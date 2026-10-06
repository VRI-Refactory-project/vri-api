# vri-api

SignConnect backend API for the VRI (Video Relay Interpreting) platform.
Built with Django, Django REST Framework and PostgreSQL, and run with Docker.

## What you need first

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running)
- Git
- Python 3.12 (for pre-commit, and if you want to run Django without Docker)

## Quick start

```bash
git clone https://github.com/VRI-Refactory-project/vri-api.git
cd vri-api
cp .env.example .env
docker-compose up --build
```

The first build takes a few minutes. After that, rebuilds take seconds.

When it is ready:

- API: http://localhost:8000
- Admin: http://localhost:8000/admin

Tables are created automatically on start (`migrate` runs before the server).

## Check your setup

With Docker running (or your `.venv` active), run from the project root:

```bash
python scripts/check_setup.py
```

It checks your branch, the pre-commit hook, `.env`, the database connection and Swagger,
and ends with `RESULT: READY` or a list of what to fix. Paste the full output to the tech lead.
It only uses the Python standard library, so it works before you install anything.

## API docs (Swagger)

| URL | What |
|---|---|
| http://localhost:8000/api/docs/ | Swagger UI: browse and try every endpoint |
| http://localhost:8000/api/redoc/ | ReDoc: read-only reference |
| http://localhost:8000/api/schema/ | Raw OpenAPI schema (YAML) for the web and mobile teams |

- All endpoints live under `/api/v1/` and appear in Swagger automatically.
- Decorate views with `@extend_schema(tags=[...], summary=...)` so they are grouped and described.
  See `apps/accounts/views.py` for an example.
- In production (`config.settings.prod`) the docs are only visible to staff users.
- Before opening a PR, check the schema has no warnings (CI also runs this):

```bash
docker-compose exec backend python manage.py spectacular --validate --fail-on-warn --file schema.yml
```

## Users and signing in

- One `User` table for all roles (`deaf_hoh`, `interpreter`, `institution`, `admin`). Role-specific data goes in
  one-to-one profile models in each role's app.
- Users sign in with **phone number or email** plus a **password or PIN** (minimum 6 characters; common ones like
  `123456` are rejected). Local numbers like `0772123456` are stored as `+256772123456`.
- Login returns JWT tokens. Clients send `Authorization: Bearer <access token>`. Access tokens last 30 minutes;
  get a new one from `POST /api/v1/auth/token/refresh/` with the refresh token (valid 7 days).

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/auth/login/` | Sign in with `{"identifier": "<phone or email>", "password": "..."}` |
| `POST /api/v1/auth/token/refresh/` | New access token from a refresh token |
| `GET /api/v1/auth/me/` | The signed-in user |
| `POST /api/v1/auth/password/change/` | Change my password/PIN |
| `POST /api/v1/auth/recovery/request/` | Forgot password: email me a 6-digit code |
| `POST /api/v1/auth/recovery/confirm/` | Code + new password, returns tokens |
| `POST /api/v1/manage/users/{id}/reset-password/...` | Admin reset for phone-only users (see `docs/design/V2-008-account-recovery.md`) |

**Recovery codes in development** are not really emailed: they are printed in the backend logs
(`docker compose logs -f backend`).

**Create your first admin** (you will be asked for a phone number, full name and password):

```bash
docker compose exec backend python manage.py createsuperuser
```

**Try authenticated endpoints in Swagger:** call `/auth/login/`, copy the `access` value, click **Authorize**
at the top of the page and paste it. Swagger then sends the token with every request.

**Building a registration endpoint:** subclass `BaseRegistrationSerializer` from `apps/accounts/serializers.py`.
It already validates and normalises the phone number, rejects duplicates, checks the password, hashes it with
Argon2 and wraps user + profile creation in one transaction. See its docstring for an example.

## Running without Docker (optional)

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1          # Git Bash: source .venv/Scripts/activate
pip install -r requirements-dev.txt
```

In your `.env`, set `DATABASE_HOST=localhost` (Postgres from `docker-compose up db`, or a local install),
then run `python manage.py migrate` and `python manage.py runserver`.

## Environment variables

`.env` is **never committed**. Copy `.env.example` and fill in your own values.

| Variable | What it does |
|---|---|
| `SECRET_KEY` | Django secret key. Use any long random string locally. |
| `DJANGO_DEBUG` | `True` for local development, `False` in production. |
| `ALLOWED_HOSTS` | Comma-separated hosts Django will serve. |
| `CORS_ALLOW_ALL_ORIGINS` | `True` allows any frontend (local only). |
| `CORS_ALLOWED_ORIGINS` | Frontend addresses allowed when the one above is `False`. |
| `POSTGRES_DB` | Database name. |
| `POSTGRES_USER` | Database user. |
| `POSTGRES_PASSWORD` | Database password. |
| `DATABASE_HOST` | `db` with Docker (the service name in `docker-compose.yml`); `localhost` without Docker. |
| `DATABASE_PORT` | Keep as `5432`. |

Both containers read the same `.env`, so the database password only needs to be set once.

## Everyday commands

Run these from the project root.

```bash
docker-compose up -d                  # start in the background
docker-compose down                   # stop (keeps your database data)
docker-compose logs -f backend        # watch backend logs
docker-compose exec backend python manage.py createsuperuser
docker-compose exec backend python manage.py makemigrations
docker-compose exec backend python manage.py migrate
docker-compose exec backend python manage.py test
```

To wipe the database and start fresh, run `docker-compose down -v`.
This deletes all data in the local database.

## Git workflow

The full team rules (branches, pull requests, migrations, tests, Swagger) are in
[CONTRIBUTING.md](CONTRIBUTING.md). Read it before your first pull request.


- Branches: `main` (stable) and `dev` (integration).
- Create your own branch from `dev`, for example `feature/interpreter-model`.
- Open a pull request into `dev`. Do not commit directly to `main`.
- GitHub Actions runs flake8, Django checks, a missing-migrations check, the tests and
  Swagger schema validation on every push and pull request to `dev` and `main`.

### One-time setup: pre-commit hooks

The hooks block commits to `main`, fix whitespace and line endings, and run flake8.

```bash
pip install -r requirements-dev.txt    # or just: pip install pre-commit
pre-commit install
```

If a hook fixes a file, the commit stops. Run `git add .` and commit again.

## Project structure

```
vri-api/
├── apps/
│   └── accounts/           User model, login (JWT), shared registration serializer
├── config/
│   ├── settings/           base.py (shared), dev.py, prod.py
│   └── urls.py             Root URLs, incl. /api/v1/ and the Swagger docs
├── manage.py
├── requirements.txt        Runtime packages (used by Docker and CI)
├── requirements-dev.txt    Adds pre-commit and flake8
├── Dockerfile
├── docker-compose.yml
├── .env.example            Template for your local .env
├── .pre-commit-config.yaml
└── .github/workflows/ci.yml
```

### Adding a new app

```bash
mkdir apps/<name>
docker-compose exec backend python manage.py startapp <name> apps/<name>
```

Then in `apps/<name>/apps.py` set `name = "apps.<name>"`, add `"apps.<name>"` to `INSTALLED_APPS`
in `config/settings/base.py`, and include its urls under `/api/v1/` in `config/urls.py`.

- Use the custom user via `settings.AUTH_USER_MODEL` or `get_user_model()`, never `django.contrib.auth.models.User`.
- Do not name an app `sessions`; it clashes with Django's built-in sessions app.

## Troubleshooting

**Container name already in use** (`vri_postgres_db` or `vri_django_backend`)
An old container is left over. Remove it and start again:

```bash
docker rm -f vri_postgres_db vri_django_backend
docker-compose up -d
```

**Port 5432 already in use**
You have Postgres running on your machine. In `docker-compose.yml`, change `"5432:5432"` to `"5433:5432"`.

**Backend keeps restarting**
Read the reason with `docker-compose logs backend`.

**Database login fails after changing the password**
Postgres only reads the password the first time it creates the database. Run `docker-compose down -v`, then `docker-compose up --build`.

**Swagger (`/api/docs/`) shows a blank white page**
The Swagger UI files come from the `drf-spectacular-sidecar` package (served by us, not a CDN, because some networks block
`cdn.jsdelivr.net`). If the page is blank, you are on old packages: run `docker-compose up --build`.

## Before deploying

- Remove `*` from `ALLOWED_HOSTS`.
- Set `DJANGO_DEBUG=False` and `CORS_ALLOW_ALL_ORIGINS=False`.
- Use a new `SECRET_KEY` and a strong database password.
- Replace `runserver` with `gunicorn`.
- Set `DJANGO_SETTINGS_MODULE=config.settings.prod`.
