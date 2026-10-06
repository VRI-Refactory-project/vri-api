# vri-api
SignConnect backend API (Django + Django REST Framework + PostgreSQL).

## Requirements
- Python 3.14 (see `.python-version`)
- PostgreSQL running locally

## Setup (Windows, Git Bash)
```bash
git checkout dev && git pull
python -m venv .venv
source .venv/Scripts/activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements/dev.txt
pre-commit install                   # blocks commits to main on your machine
cp .env.example .env                 # then edit SECRET_KEY and DATABASE_URL
```
Create the database named in `DATABASE_URL` (e.g. `vri_db`) in PostgreSQL, then:
```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## API docs
| URL | What |
|---|---|
| http://127.0.0.1:8000/api/docs/ | Swagger UI |
| http://127.0.0.1:8000/api/redoc/ | ReDoc |
| http://127.0.0.1:8000/api/schema/ | Raw OpenAPI schema (YAML) |

All endpoints live under `/api/v1/` and appear in Swagger automatically.
Use `@extend_schema(tags=[...], summary=...)` on views to group and describe them.
Before opening a PR, check the schema has no warnings:
```bash
python manage.py spectacular --validate --fail-on-warn --file schema.yml
```

## Project layout
```
config/            project settings (settings/base.py, dev.py, prod.py) and root urls
apps/accounts/     custom User model (role: deaf_user / interpreter / institution)
requirements/      base.txt (runtime), dev.txt (tools)
```
New features go in their own app under `apps/` (`python manage.py startapp <name> apps/<name>`,
then set `name = "apps.<name>"` in its `apps.py`). Do not name an app `sessions`;
it clashes with Django's built-in sessions app.

## Branches
- `main`: protected, release only. Never commit to it directly.
- `dev`: integration branch. Branch off `dev` for each story and open a PR back into `dev`.

## Production
Set `DJANGO_SETTINGS_MODULE=config.settings.prod`. In prod, the API docs are visible to staff users only.
