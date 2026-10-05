# vri-api
# vri-api

SignConnect backend API for the VRI (Video Relay Interpreting) platform.
Built with Django, Django REST Framework and PostgreSQL, and run with Docker.

## What you need first

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running)
- Git
- Python 3.12 (only needed for pre-commit, not to run the app)

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
| `DATABASE_HOST` | Keep as `db` (the service name in `docker-compose.yml`). |
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

- Branches: `main` (stable) and `dev` (integration).
- Create your own branch from `dev`, for example `feature/interpreter-model`.
- Open a pull request into `dev`. Do not commit directly to `main`.
- GitHub Actions runs flake8 on every push and pull request to `dev` and `main`.

### One-time setup: pre-commit hooks

The hooks block commits to `main`, fix whitespace and line endings, and run flake8.

```bash
pip install pre-commit
pre-commit install
```

If a hook fixes a file, the commit stops. Run `git add .` and commit again.

## Project structure

```
vri-api/
├── config/                 Django project settings and URLs
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example            Template for your local .env
├── .pre-commit-config.yaml
└── .github/workflows/ci.yml
```

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

## Before deploying

- Remove `*` from `ALLOWED_HOSTS`.
- Set `DJANGO_DEBUG=False` and `CORS_ALLOW_ALL_ORIGINS=False`.
- Use a new `SECRET_KEY` and a strong database password.
- Replace `runserver` with `gunicorn`.
