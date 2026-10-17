"""Check that this machine is ready to work on vri-api.

Run from the repo root (with Docker running, or with your .venv active):

    python scripts/check_setup.py

Then paste the whole output to the tech lead.
Uses only the Python standard library, so it runs even before you install anything.
"""

import importlib.metadata as md
import json
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_URL = "http://localhost:8000"
results = []  # (status, label, detail)


def record(status, label, detail=""):
    results.append((status, label, detail))


def run(cmd, timeout=60):
    try:
        p = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def http_get(path):
    try:
        with urllib.request.urlopen(APP_URL + path, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001  # any other failure means "not reachable"
        return None, str(exc)


def version_ok(installed, spec):
    """Supports the simple specs used in requirements.txt, e.g. '>=5.2,<5.3'."""

    def as_tuple(v):
        return tuple(int(x) for x in re.findall(r"\d+", v)[:3])

    iv = as_tuple(installed)
    for part in filter(None, (s.strip() for s in spec.split(","))):
        op, ver = re.match(r"(>=|<=|==|<|>)\s*(.+)", part).groups()
        rv = as_tuple(ver)
        n = len(rv)
        ok = {
            ">=": iv[:n] >= rv,
            "<=": iv[:n] <= rv,
            "==": iv[:n] == rv,
            "<": iv[:n] < rv,
            ">": iv[:n] > rv,
        }[op]
        if not ok:
            return False
    return True


# 1. Git
code, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
if code:
    record("FAIL", "Git repository", "Run this from inside the vri-api folder.")
else:
    record(
        "OK" if branch != "main" else "WARN",
        "Current branch",
        branch + ("" if branch != "main" else " - switch to dev or a feature branch"),
    )
    run(["git", "fetch", "origin", "--quiet"])
    code, behind = run(["git", "rev-list", "--count", "HEAD..origin/dev"])
    if code == 0 and behind.isdigit():
        n = int(behind)
        record(
            "OK" if n == 0 else "FAIL",
            "Up to date with origin/dev",
            "yes" if n == 0 else f"{n} commit(s) behind - run: git pull origin dev",
        )

# 2. pre-commit hook
hook = ROOT / ".git" / "hooks" / "pre-commit"
if hook.exists() and "pre-commit" in hook.read_text(errors="ignore"):
    record("OK", "pre-commit hook installed")
else:
    record(
        "FAIL",
        "pre-commit hook installed",
        "run: pip install pre-commit  then  pre-commit install",
    )

# 3. .env
env_file, example = ROOT / ".env", ROOT / ".env.example"
if not env_file.exists():
    record("FAIL", ".env file", "missing - run: cp .env.example .env")
else:

    def keys(p):
        return {
            ln.split("=", 1)[0].strip()
            for ln in p.read_text().splitlines()
            if "=" in ln and not ln.lstrip().startswith("#")
        }

    missing = sorted(keys(example) - keys(env_file))
    record(
        "OK" if not missing else "FAIL",
        ".env has all variables",
        "yes" if not missing else "missing: " + ", ".join(missing),
    )

# 4. Running app (Docker or runserver)
status, body = http_get("/api/v1/health/")
app_running = status == 200 and '"database": "ok"' in body.replace('":"', '": "')
if status == 503:
    record(
        "FAIL",
        "API running at " + APP_URL,
        "server is up but cannot reach the database",
    )
elif status == 200 and not app_running:
    record(
        "WARN",
        "API running at " + APP_URL,
        "server is up but on old code - run: git pull, then restart it",
    )
if app_running:
    record("OK", "API running at " + APP_URL, "health check ok, database connected")
    s, _ = http_get("/api/docs/")
    record(
        "OK" if s == 200 else "FAIL",
        "Swagger at /api/docs/",
        "loads" if s == 200 else "not loading",
    )
    s, schema = http_get("/api/schema/?format=json")
    if s == 200:
        try:
            record("OK", "API schema version", json.loads(schema)["info"]["version"])
        except (ValueError, KeyError):
            pass
elif status is None:
    record(
        "WARN",
        "API running at " + APP_URL,
        "not reachable - start it with: docker compose up  (or python manage.py runserver)",
    )

# 5. Local Python environment (only matters if you run Django outside Docker)
py = platform.python_version()
in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
local_django = True
try:
    md.version("Django")
except md.PackageNotFoundError:
    local_django = False

if local_django:
    record(
        "OK" if py.startswith("3.12") else "WARN",
        "Python version",
        py + ("" if py.startswith("3.12") else " (team/Docker/CI use 3.12)"),
    )
    record(
        "OK" if in_venv else "WARN",
        "Virtual environment active",
        "yes" if in_venv else "no - packages are global; use .venv to match the team",
    )
    bad = []
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z0-9_.\-]+)(\[[^\]]+\])?\s*(.*)", line)
        name, spec = m.group(1), m.group(3)
        try:
            v = md.version(name)
            if spec and not version_ok(v, spec):
                bad.append(f"{name} {v} (need {spec})")
        except md.PackageNotFoundError:
            bad.append(f"{name} not installed")
    record(
        "OK" if not bad else "FAIL",
        "Packages match requirements.txt",
        "yes"
        if not bad
        else "; ".join(bad) + " - run: pip install -r requirements-dev.txt",
    )
    if not app_running:
        code, out = run([sys.executable, "manage.py", "check"])
        record(
            "OK" if code == 0 else "FAIL",
            "Django system check",
            "passed" if code == 0 else out.splitlines()[-1][:200],
        )
        code, out = run([sys.executable, "manage.py", "showmigrations", "--plan"])
        if code == 0:
            pending = out.count("[ ]")
            record(
                "OK" if pending == 0 else "FAIL",
                "Migrations applied",
                "all applied"
                if pending == 0
                else f"{pending} pending - run: python manage.py migrate",
            )
        else:
            host = (
                re.search(r"^DATABASE_HOST=(.*)$", env_file.read_text(), re.MULTILINE)
                if env_file.exists()
                else None
            )
            hint = (
                " - set DATABASE_HOST=localhost in .env when not using Docker"
                if host and host.group(1).strip() == "db"
                else " - is PostgreSQL running?"
            )
            record("FAIL", "Database connection", out.splitlines()[-1][:200] + hint)
elif status is None:
    record(
        "FAIL",
        "Way to run the app",
        "neither Docker app nor local Django found - start Docker, or set up .venv (see README)",
    )

# Report
print("\nvri-api setup check")
print("=" * 60)
for status, label, detail in results:
    print(f"[{status:4}] {label}" + (f": {detail}" if detail else ""))
print("=" * 60)
fails = sum(1 for s, *_ in results if s == "FAIL")
user = run(["git", "config", "user.name"])[1] or "unknown"
print(f"User: {user} | OS: {platform.system()} {platform.release()} | Python {py}")
print(
    "RESULT: READY" if fails == 0 else f"RESULT: NOT READY ({fails} problem(s) to fix)"
)
sys.exit(1 if fails else 0)
