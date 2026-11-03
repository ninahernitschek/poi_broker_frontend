# POI Broker Web Frontend — Copilot Agent Instructions

## 1. Project Context & Scope
- **Domain:** Astronomical alert data (ZTF/LSST). Features include browsing/filtering alerts, plotting lightcurves/features, and managing user watchlists/favorites.
- **Data Architecture:** Runtime data comes from SQLite databases that live **outside** the repo by default (`../_broker_db/ztf_alerts_stream.db` and `../_broker_db/users.db`). Do not attempt to seed or create local databases in the repo root.
- **Tech Stack:** Python 3.12, Flask (App-Factory pattern), Flask-SQLAlchemy, Flask-Login, Flask-WTF, Flask-Limiter.
- **Scientific Stack:** Astropy, NumPy, Matplotlib, Bokeh.
- **Frontend:** Server-rendered HTML/Jinja2, jQuery, Bootstrap 4.

## 2. Architecture & File Map
When proposing changes, refer to this structure:
- `wsgi.py`: WSGI entrypoint (`app = create_app()`).
- `poi_broker/__init__.py`: App factory, extension init, SQLite pragmas, blueprint registration. Read this first for app context.
- `poi_broker/settings.py`: Environment loading and DB binds.
- `poi_broker/models.py`: SQLAlchemy ORM. (Alerts: `Ztf`, `Crossmatches`, `Classification`. Users: `User`, `Favorite`, `Watchlist`, etc.).
- `poi_broker/routes/`: API blueprints (`favorites.py`, `visual_query.py`, `lightcurve.py`, `export.py`, etc.).
- `poi_broker/services/`: Business logic (`querybuilder_translator.py`, feature fetching, plotting).
- **Async CSV export:** Huey + SQLite, not Celery/Redis. ADR: `docs/async_export/adr_huey_sqlite.md`. Consumer must target `poi_broker.worker.huey` (not `extensions.huey`). Config: `huey_config.py`, tasks: `tasks.py`.
- `poi_broker/templates/`: Jinja2 templates.
- `tests/`: Pytest suite (use `conftest.py` for fixtures).

## 3. Coding Directives (Strict)
- **Workflow for New Entities:** 1. SQLAlchemy Model -> 2. Service Layer -> 3. API Blueprint Route -> 4. Pytest Integration Test.
- **Style:** Functional, declarative programming. Use early returns and guard clauses; put the happy path at the end of the function.
- **Typing:** Use Python type hints for all function signatures.
- **Surgical Edits:** Modify only what is required. Do not refactor unrelated code or reformat files unnecessarily. 
- **Ambiguity Check:** If you are unsure whether to create a new module/Blueprint or how to structure an auth decorator, STOP and ask the user. Do not invent requirements.

## 4. Environment & Execution
Always run these commands from the repo root to preserve relative imports and `.env` loading.
- **Prerequisites:** `SECRET_KEY` must be set in `.env` or commands will raise a `RuntimeError`.
- **Install:** `python -m pip install -r requirements.txt -r requirements-dev.txt`
- **Test:** use focused test files while developing; full validation is `python -m pytest -n 2 -q --cov=poi_broker --cov-fail-under=75`. See `pre-commit.md` for commit hooks and CI triggers.
- **Run (Dev Server):** `python -m flask --app wsgi:app run --debug`
- **Validate (No Server):** `python -m flask --app wsgi:app routes` (Validates app import and blueprint registration).

## 5. Pre-Commit Validation Checklist
Before finalizing a task, silently verify:
1. Did I run the tests relevant to changed behavior? Use the full suite for broad/shared changes; documentation-only edits need lightweight checks.
2. Did I run `python -m pre_commit run --all-files`? Hooks report correctness/configuration issues without rewriting files or running tests.
3. For startup or blueprint changes, validate route registration with isolated test configuration; do not open production databases just to check imports.
4. If UI/endpoint behavior changed, do a quick manual browser/API sanity check.
