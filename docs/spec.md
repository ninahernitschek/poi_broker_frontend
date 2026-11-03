# POI Broker — Project Specification (Spec-Anchored Development Reference)

**Version:** 1.1.0
**Last updated:** 2026-08-27
**Status:** Draft — derived from codebase inspection (app v3.5.0)
**Scope:** HTTP endpoints only (internal service-layer contracts intentionally excluded)

---

## 1. Project Overview

### 1.1 Purpose
The **Point of Interest (POI) Community Broker** is a transient alert software (Rubin Observatory / LSST Alert and Community Broker), currently tested against the ZTF alert stream. This repository contains the **web frontend** for the broker: browsing and filtering astronomical alert data, plotting lightcurves and features, and managing user watchlists/favorites.

### 1.2 Scope and Boundaries
- **In scope:** Web frontend — server-rendered Flask app + JSON API endpoints, user auth/account management, alert browsing/filtering, visual query builder, lightcurve/feature/classification plotting, favorites/watchlists, filter bookmarks, user observatories, observing tool, CSV export.
- **Out of scope:** Alert ingestion, annotation, classification, and forwarding pipelines (backend broker processing). The main alerts SQLite DB is intentionally external to the repo.

### 1.3 Key Stakeholders
- Astronomers (variable-star observers) — primary users.
- Internal development team.
- Rubin Observatory / LSST community broker program.

### 1.4 Tech Stack
- Python 3.12, Flask (app-factory pattern), Flask-SQLAlchemy, Flask-Login, Flask-WTF, Flask-Limiter.
- Astropy, NumPy, Matplotlib, Bokeh.
- SQLite (main alerts DB + separate users/auth DB bind).
- Jinja2 templates, jQuery, Bootstrap 4. See §1.5 for CDN/static versions and the Popper split.

### 1.5 Frontend JavaScript stack

Load order is fixed: jQuery → jQuery UI → **Bootstrap 4.6.2 `bootstrap.bundle`** (all pages) → catalog-only `@popperjs/core` v2 + Tempus Dominus 6 (`main_ui_js.html`).

**Popper (do not “unify”)**

Bootstrap 4 calls `new Popper(...)` (Popper **v1** API). Tempus Dominus 6’s peer is `@popperjs/core` (Popper **v2**, `Popper.createPopper`). Those cannot share `window.Popper`.

Current split:

- `common_js.html` loads `bootstrap.bundle.min.js` **4.6.2** (Popper v1 closed over inside the bundle; does not set `window.Popper`). Local fallback: `/static/js/bootstrap.bundle.min.js`.
- `main_ui_js.html` loads `@popperjs/core@2.11.8` only on the catalog page, immediately before Tempus Dominus.

**No-fix unless a major library upgrade.** Do not replace the bundle with a global `popper.js` 1.x, do not load v1 then v2 as globals (last write wins), and do not drop v2 while Tempus Dominus 6 remains. A single Popper is appropriate only with a **major** change: Bootstrap **5** (Popper v2 throughout) and/or replacing Tempus Dominus. Same bar for a global `.modal-backdrop` scrubber, `data-toggle` → `data-bs-toggle`, Font Awesome 5+, or jQuery **4** (Bootstrap 4 requires jQuery `< 4.0`).

CSS is already local Bootswatch **4.6.2**. JS was aligned to 4.6.2 bundle (was 4.3.1). 4.6.2 is the last Bootstrap 4 release (EOL 2023-01-01); there is no further 4.x point release.

| Library | In use | Upstream (as of 2026-08) | Notes |
|---|---|---|---|
| Bootstrap JS | 4.6.2 bundle | **4.6.2** (final 4.x) | Latest BS4. BS5 is a major rewrite. |
| Bootswatch CSS | 4.6.2 | 4.6.2 | Local `/static/css/bootstrap.min.css`. |
| jQuery | **3.7.1** | 3.7.1 (3.x); 4.0.0 | Current 3.x. Local fallback `/static/js/jquery.min.js`. jQuery 4 is incompatible with BS4. |
| jQuery UI | 1.14.2 | 1.14.2 | Loaded globally; catalog `.sortable` is a CSS class, not the UI Sortable widget. |
| `@popperjs/core` | 2.11.8 | 2.11.8 | Catalog page only. Keep off `common_js.html`. |
| Tempus Dominus | 6.10.4 | 6.10.4 | Inactive project; docs site still shows 6.9.4. Stay on 6.x + Popper v2 until a datepicker replacement. |
| jQuery QueryBuilder | 3.0.0 | **3.0.0** | Latest. 3.0.0 targets Bootstrap **5**; we stay on BS4 + `data-toggle`. Do not chase QB APIs that assume `data-bs-*` without a BS5 upgrade. |
| `@nobleclem/jquery-multiselect` | 2.4.26 | 2.4.26 | Catalog / settings feature-plot select. |
| Font Awesome | 4.7.0 | 4.7.0 last of v4 | Latest FA4. FA5+ is a class-name major upgrade (pair with BS5). |
| Bokeh JS | matches `bokeh==3.*` | 3.9.2 | Same-origin `GET /bokeh.min.js` from the installed package (`?v={{ bokeh_version }}` cache-bust). Python pin is the source of truth. CVE-2026-21883 is Bokeh **server** WebSocket origin; this app embeds `components()`, not a Bokeh server. |
| Aladin Lite | vendored `/static/js/aladin.js` (v3 snapshot) | v3 `latest` CDN | Intentionally pinned locally (not `.../v3/latest/...`). |

---

## 2. Source-of-Truth Spec Inventory

| Source | Path | Authoritative For | Notes |
|---|---|---|---|
| This spec | `docs/spec.md` | System behavior, API contracts, requirements | Primary reference for spec-anchored development |
| App config | `poi_broker/settings.py` | Env vars, rate limits, security config | `APP_VERSION='3.5.0'` |
| Env template | `.env.example` | Required/optional env vars | `SECRET_KEY` mandatory |
| Data models | `poi_broker/models.py` | Entities, columns, relationships, binds | Alerts + users DB |
| Alerts schema | `tools/alertsdb_schema.sql` | Alerts-side schema reference | |
| Users schema | `tools/usersdb_schema.sql` | Users-side schema reference | |
| Tests | `tests/*.py` | Enforced behavior (de-facto spec) | See §8 |
| Coverage policy | `tests/coverage.md` | Coverage targets | 50–75%/module, 75%+ total |
| Repo map | `Repomap.md` | Module/route overview | |
| README | `README.md` | Install/usage/ops guidance | Operational, not behavioral spec |
| Huey ADR | `docs/async_export/adr_huey_sqlite.md` | Why Huey + SQLite for async export | Not Celery/Redis |
| Session-invalidation ADR | `docs/password_reset/adr_session_invalidation.md` | Why epoch-based session invalidation on password change/reset | No server-side session store |
| Deployment runbook | `docs/password_reset/deployment.md` | Manual SQL upgrade steps for the users DB | Apply-before-restart ordering |

> **Note:** No formal external spec document exists. All requirements below are **inferred from code** unless a test explicitly enforces them (marked accordingly).

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Enforced By |
|---|---|---|
| REQ-001 | User can sign up with email/name/password; email deliverability validated, min 8-char password | `tests/test_auth.py` |
| REQ-002 | User verifies email via emailed token (SHA-256 hashed) | `tests/test_auth.py` |
| REQ-003 | User can log in with email/password (optional remember-me) | `tests/test_auth.py` |
| REQ-004 | User can log out (secure session cookie cleared) | `tests/test_auth.py` |
| REQ-005 | User can request password reset (1-hour expiry token) and set new password; the reset ends every existing session and remembered login for the account | `tests/test_auth.py`, `tests/test_security_regressions.py` |
| REQ-006 | User can change password (requires current password); the change signs out all devices including the acting browser and clears its remember-me cookie | `tests/test_auth.py`, `tests/test_security_regressions.py` |
| REQ-007 | User can browse/filter alerts on main page (date/MJD, alert_id, object_id, passband, locus_id, RA/Dec, magnitude, prob_class; one sort key; 100 rows/page; every row reachable) | `tests/test_app.py`, `tests/test_alert_id_filter.py`, `tests/test_dec_filter.py`, `tests/test_ant_passband_filter.py`, `tests/test_catalog_list.py`, `tools/verify_catalog_pagination.py` |
| REQ-008 | User can download filtered alerts as CSV | `tests/test_app.py` |
| REQ-009 | User can query crossmatches for a locus | `tests/test_app.py` |
| REQ-010 | User can build visual queries (QueryBuilder) and preview SQL / get match count | `tests/test_visual_query*.py` |
| REQ-011 | User can save/delete named watchlists (private, per-user) | `tests/test_visual_query*.py` |
| REQ-012 | User can view lightcurve plot and export locus CSV | `tests/test_lightcurve*.py` |
| REQ-013 | User can view feature values for an alert and feature plots for a locus | `tests/test_features*.py` |
| REQ-014 | User can toggle favorites per locus and organize into groups | `tests/test_favorites.py` |
| REQ-015 | User can save/delete named filter bookmarks (whitelisted keys) | `tests/test_filter_bookmarks.py` |
| REQ-016 | User can CRUD custom observatories (name/lat/lon, auto timezone) | `tests/test_user_observatories*.py` |
| REQ-017 | User can view observing plot (AltAz visibility) for builtin/custom observatory | `tests/test_observing_tool*.py` |
| REQ-018 | User can view classification radar chart for an alert | `tests/test_classification.py` |
| REQ-019 | User can configure default feature-plot columns and last-selected observatory in settings | `tests/test_user_settings*.py` |
| REQ-020 | User can enqueue a visual-query CSV export (`POST /export`); one non-stale active task per user (409); stale `PENDING`/`RUNNING` past `EXPORT_STALE_MAX_AGE_SECONDS` is failed on retry so a down Huey consumer cannot block forever; the export and its pre-count are snapshot-bounded by `ExportTask.snapshot_mjd` (alert-time cutoff set at submit) so re-running the same rules reproduces the CSV | `tests/test_export_routes.py`, `tests/test_huey_tasks.py`, `tests/test_export_query.py` |

### 3.2 Non-Functional Requirements

| ID | Requirement | Value / Source |
|---|---|---|
| NFR-001 | Auth rate limiting | login 10/min, signup 5/hr, forgot-pw 5/hr, reset-pw 10/hr, change-pw 10/hr (`AUTH_RATE_LIMIT_*`). `memory://` is per-process; multi-worker Gunicorn needs a shared `RATELIMIT_STORAGE_URI` |
| NFR-002 | Read rate limiting | `/`, `/api/catalog-count`, `/query_crossmatches`, `/query_features`, `/query_featureplot_data`, `/query_lightcurve_data`, `/locus_plot_csv`, `/query_observing_plot`, `/api/export-query`, `POST /export` 30/min (LAX); `/download_alerts_csv` 15/min (MEDIUM) |
| NFR-003 | CSRF protection | Flask-WTF `CSRFProtect` on all POST forms; JSON 400 for `/api/*` |
| NFR-004 | Security headers | HSTS (HTTPS), `nosniff`, `X-Frame-Options: DENY`, Referrer-Policy, Permissions-Policy, strict CSP |
| NFR-005 | Cookie security | HttpOnly, Secure (prod), SameSite=Lax; remember cookie 14 days |
| NFR-006 | Password hashing | Werkzeug `generate_password_hash`/`check_password_hash` |
| NFR-007 | SQLite performance | WAL, `synchronous=NORMAL`, 64MB cache, 256MB mmap, `temp_store=MEMORY` |
| NFR-008 | Caching | `lru_cache` on MJD formatting (8192) and builtin observatories (1); per-request `UserSettings` reuse; 5-min in-process cache for unfiltered catalog `COUNT(*)` |
| NFR-009 | Coverage targets | 50–75% per module, 75%+ total (`tests/coverage.md`) |
| NFR-010 | Proxy support | `ProxyFix` enabled in production (trust `X-Forwarded-*`). Emailed links use `PUBLIC_BASE_URL` when set. Accepted residual risk: `x_host=1` trusts `X-Forwarded-Host`, but Gunicorn binds to loopback behind nginx (`proxy_pass http://127.0.0.1:8000`, nginx sets `X-Forwarded-Host: $server_name`), app redirects use relative `url_for()`, and `PUBLIC_BASE_URL` overrides the host for emailed links — so a spoofed forwarded host has no effect unless the proxy is bypassed or misconfigured |
| NFR-011 | Session invalidation | Password write stamps `user.password_changed_at`; `user_loader` rejects sessions without integer `login_at > password_changed_at` (remember-cookie restores included). Schema upgrades are manual SQL only — the app never writes DDL | `docs/password_reset/adr_session_invalidation.md`, `tests/test_security_regressions.py` |

### 3.3 Performance Notes

The main page (`start()`) loads an authenticated user's `UserSettings` row **once** per request and reuses it across the feature-plot-column and observatory-selection lookups, avoiding a redundant database query on every main-page load.

- `poi_broker/user_settings.py` exposes `get_user_settings(user_id)` to load the row once.
- `get_saved_feature_plot_columns()` and `get_saved_last_selected_observatory()` accept an optional pre-loaded `settings` row; when omitted they fall back to loading it themselves (backward compatible).
- This is a **per-request** optimization only — there is no cross-request cache, so no invalidation is needed when a user updates their settings in the profile UI. Each request reads the current row from the database.

Catalog listing (`GET /`) does **not** run Flask-SQLAlchemy `paginate()` / a joined `COUNT(*)` on every request:

- Every sort is keyset-paginated (`before_*` / `after_*` / `last=1`) over the cursor `(sort_column, alert_id, locus_id)`. Only a bare `?page=N` jump (`N>1`) uses `LIMIT+1` + `OFFSET`; it exists for hand-typed and legacy bookmarked URLs, is capped by `MAX_PAGE`, and its 404 names the cursor-based alternative.
- Exactly one `sort__*` key is honoured per request (first in `SORT_KEYS` order); the clicked column leads the `ORDER BY`, so the sort is real. The cursor is a total order because `(alert_id, locus_id)` is unique.
- A **nullable** sort column is walked as two segments, dispatched in Python: rows carrying a value first, rows without last, each ordered by the requested direction. NULL therefore sorts last in *both* directions rather than SQLite's native NULLs-first ascending. A single-phase cursor cannot do this — `NULL > NULL` is false, so a cursor inside a NULL tie group matches nothing and the walk stops dead — and each segment is a separate point seek on the `(sort_column, alert_id, locus_id)` index, since `IS NULL` / `IS NOT NULL` are contiguous leading-column key ranges. No `typeof` filter is applied, so **Rows** is the true catalog total for every sort.
  - `SortSpec.nullable` is read off the model column rather than a hand-kept key list. A cursor inside the null segment carries `<prefix>_null=1` instead of a value; a segment jump is `?nulls=1`. Both are stripped from generated filter links and bookmarks (`PAGINATION_KEYS`).
  - The `?page=N` OFFSET path walks the same segments, splitting the global offset with per-segment counts from `cached_segment_counts()` (TTL-cached, one `COUNT(*)` per filter+sort key), so page N by number and page N by cursor agree. A page may straddle the boundary.
  - A cursor whose value cannot be parsed raises `MalformedCursor` rather than being treated as absent, which would re-render page 1 and make Next an unbreakable loop.
- Exact **Rows** on the list request when the result is short, the catalog is unfiltered (cached `COUNT(*)` on `featuretable` only), or a high-cardinality equality filter is applied (`alert_id` full id, `locus_id`, `ztf_object_id`).
- Unselective filters show **Rows: 100+** and a Count control that calls `GET /api/catalog-count` with the same filters.

---

## 4. Data Model

Two SQLite databases via SQLAlchemy binds: **alerts** (default bind) and **users** (`__bind_key__='users'`).

### 4.1 Alerts DB
| Model | Table | Key Columns / PK | Notes |
|---|---|---|---|
| `Ztf` | `featuretable` | Composite PK `(date_alert_mjd, alert_id, locus_id)` | ~200 feature columns; `locus_ra`, `locus_dec`, `ant_mag_corrected`, `ant_passband`, per-band `_magn_r/_g`, `_flux_r/_g`, `anomaly_score/mask/type`, `g_r_max/mean`, `best_period*`, `power_rate_*`, `mhps_*`, `drw_*`; rel `classification` (1:1 via alert_id, viewonly) |
| `Crossmatches` | `crossmatches` | `id` PK | `locus_id`, `catalog`, `object`, `ra_cat`, `dec_cat`, `separation` |
| `Classification` | `classification` | `alert_id` PK | `p_cvnova`, `p_e`, `p_lpv`, `p_puls`, `p_periodic_other`, `p_quas`, `p_sn`, `p_yso`, `prob_class` |

### 4.2 Users DB
| Model | Table | Key Columns / Constraints | Notes |
|---|---|---|---|
| `User` | `user` | `id` PK, `email` unique, `password` (hashed), `name`, `role` (default `'user'`), `email_verified`, `email_verification_token`, `email_verification_token_expires`, `reset_token`, `reset_token_expires`, `password_changed_at` (epoch s; session-invalidation watermark, NULL until first rotation) | Methods `has_role`, `is_admin`; decorator `role_required(role)` |
| `FavoriteGroup` | `favorite_group` | `id`, `user_id` FK→user CASCADE, `name`, `created_at`; unique `(user_id, name)` | |
| `Favorite` | `favorite` | `id`, `user_id` FK CASCADE, `locus_id`, `group_id` FK→favorite_group SET NULL, `created_at`; unique `(user_id, locus_id)` | |
| `Watchlist` | `watchlist` | `id`, `user_id` FK CASCADE, `name`, `rules_json` (Text), `sql_where` (Text), `created_at` (epoch); unique `(user_id, name)` | `rules_json` is the executable source of truth. `sql_where` is a display preview only — never concatenate or execute it. |
| `FilterBookmark` | `filter_bookmark` | `id`, `user_id` FK CASCADE, `name`, `query_json` (Text), `created_at` | |
| `UserObservatory` | `user_observatory` | `id`, `user_id` FK CASCADE, `name` (max 100, NOCASE), `latitude`, `longitude`, `timezone_name`, `created_at`; unique `(user_id, name)` | |
| `UserSettings` | `user_settings` | `id`, `user_id` FK CASCADE, `default_feature_plot_columns` (JSON Text), `last_selected_observatory_json` (JSON Text); unique `user_id` | |
| `ExportTask` | `export_task` | `id`, `user_id` FK CASCADE, `status` (`PENDING`/`RUNNING`/`SUCCESS`/`FAILED`), `created_at`, `updated_at`, `file_path`, `error_message`, `snapshot_mjd`; partial unique one active (`PENDING`/`RUNNING`) per user | Heartbeat refreshes `updated_at` while `RUNNING`. Stale guard / age-aware `POST /export` use `updated_at` vs `EXPORT_STALE_MAX_AGE_SECONDS`. `snapshot_mjd` is the alert-time cutoff computed at submit: the export and its pre-count only include rows with `date_alert_mjd` below it, so the CSV is reproducible independent of queue latency. The worker reads `snapshot_mjd` from the ExportTask row, never from the queue payload, so web app and Huey consumer can deploy independently. |

---

## 5. API Contracts (HTTP Endpoints Only)

Global error handlers registered in `poi_broker/__init__.py`:
- `413 Request Entity Too Large`: returns JSON `{"error": "Request body too large"}` (status 413) on `/api/*` or JSON requests; plain text for other requests. Bound by `MAX_CONTENT_LENGTH` (1 MiB).
- `OverflowError`: client-supplied integers exceeding SQLite's 64-bit INTEGER range return JSON `{"error": "Numeric value out of range"}` (status 400) on `/api/*` or JSON requests; plain text 400 for HTML endpoints (e.g. `?page=N`).
- `RecursionError`: overly nested payloads (e.g. query builder rules) return JSON `{"error": "Request payload is too deeply nested"}` (status 400) on `/api/*` or JSON requests; plain text 400 for other requests.
- `CSRFError`: returns JSON `{"error": "csrf validation failed", "message": "..."}` (status 400) on `/api/*` or JSON requests; flash message and redirect for HTML form submissions.

### 5.1 Main Blueprint (`poi_broker/app.py`)
| Method | Path | Auth | Request | Response | Errors |
|---|---|---|---|---|---|
| GET | `/` | Public | Query: `page`, `last`, `before_v`/`after_v` + `*_alert_id`/`*_locus_id` cursors, `date`, `date_alert_mjd`, `alert_id`, `ztf_object_id`, `ant_passband`, `locus_id`, `locus_ra`, `locus_dec`, `magpsf`, `prob_class`, `sort__*` | HTML `main.html` (100 rows/page; keyset, `?page=N` legacy OFFSET) | 404 empty `?page=N`, 404 empty `?nulls=1`, 404 `page > MAX_PAGE` (message names cursor alternative), 404 `page < 1`, 400 integer out of range |
| GET | `/help` | Public | — | HTML `help.html` | — |
| GET | `/contact` | Public | — | HTML `contact.html` | — |
| GET | `/profile` | Login | — | HTML `profile.html` | — |
| GET | `/download_alerts_csv` | Public | Query: `alert_id` (repeatable, max 100 = one page) | CSV | 400 missing, 400 too many `alert_id`s, 404 no records, 500 error |
| GET | `/query_crossmatches` | Public | Query: `locusId` | JSON array | 400 missing, 500 error |
| GET | `/api/catalog-count` | Public | Same filter query params as `/` (sort/page ignored) | JSON `{count}` | — |

### 5.2 Auth Blueprint (`poi_broker/auth.py`)

Flash messages are rendered once in `site.html` via `get_flashed_messages(with_categories=True)` using Bootstrap alert classes `danger`, `success`, `info`, and `warning` (unknown categories fall back to `danger`).

`POST /login` records an epoch `login_at` marker in the signed session; both password-write routes stamp `user.password_changed_at` and terminate all sessions (see §6.1).

| Method | Path | Auth | Request | Response | Errors |
|---|---|---|---|---|---|
| GET | `/login` | Public | `?forgot_password=true` | HTML `login.html` | — |
| POST | `/login` | Public | Form: `email`, `password`, `remember` | Redirect → `main.profile` | — |
| GET | `/signup` | Public | — | HTML `signup.html` | — |
| POST | `/signup` | Public | Form: `email`, `name`, `password` | Sends verification email. Duplicate/new/uniqueness-race share one generic flash and redirect to `/login`. | — |
| GET | `/verify-email/<token>` | Public | Token | Verifies email (24h expiry) | — |
| POST | `/logout` | Login | — | Clears session | — |
| GET | `/forgot-password` | Public | — | HTML `forgot_password.html` | — |
| POST | `/forgot-password` | Public | Form: `email` | Same generic flash whether the email exists; sends reset email (1h) when it does | — |
| GET | `/reset-password/<token>` | Public | Token | HTML `reset_password.html` | — |
| POST | `/reset-password/<token>` | Public | Form: new password | Sets new password; ends all sessions and remembered logins; drops any session held by the submitting browser | — |
| GET | `/security` | Login | — | HTML `security.html` (shows Last Password Change via `epoch_utc_date` filter when `password_changed_at` is set) | — |
| POST | `/change-password` | Login | Form: `current_password`, `new_password`, `new_password_confirm` | Changes password; signs out everywhere including this browser (remember cookie cleared) → redirect `/login` | — |

### 5.3 Favorites Blueprint (`poi_broker/routes/favorites.py`, prefix `/api`) — all Login
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/api/favorite` | Query: `locusId` | `{"fav": bool}` | — |
| GET | `/api/favorites` | Query: `groupId` (or null) | `{"favorites":[{"id","locusId"}]}` | — |
| POST | `/api/favorite` | JSON: `{"locusId","fav","groupId"}` | `{"status":"ok"}` | 400/401/404/409/500 |
| PATCH | `/api/favorite/<int:favorite_id>/group` | JSON: `{"groupId"}` | `{"status":"ok","groupId"}` | — |
| GET | `/api/favorite-groups` | — | `{"groups":[{"id","name","count"}]}` (incl. "Ungrouped") | — |
| POST | `/api/favorite-groups` | JSON: `{"name"}` | `{"status":"ok","id","name"}` 201 | — |
| DELETE | `/api/favorite-groups/<int:group_id>` | — | `{"status":"ok"}` (orphans favorites) | — |

### 5.4 Filter Bookmarks Blueprint (`poi_broker/routes/filter_bookmarks.py`, prefix `/api`) — all Login
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/api/filter-bookmarks` | — | `{"filterBookmarks":[{"id","name","params","path","created_at"}]}` | — |
| POST | `/api/filter-bookmarks` | JSON: `{"name","params"}` | `{"status":"ok",...}` 201 | 409 duplicate name |
| DELETE | `/api/filter-bookmarks/<int:bookmark_id>` | — | `{"status":"ok"}` | 404 |

> Whitelist: `ALLOWED_KEYS` (17 keys); `MAX_BOOKMARK_JSON_BYTES=16384`; `MAX_PARAM_VALUE_LEN=512`.

### 5.5 Visual Query Blueprint (`poi_broker/routes/visual_query.py`) — all Login
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/visual_query` | — | HTML `visual_query.html` | — |
| POST | `/api/preview-query` | JSON rules | `{"sql":"..."}` | 400 invalid rules / missing JSON / oversized in-list / nested, 413 body too large, 500 error |
| POST | `/api/export-query` | JSON rules | `{"count": int}` | 400 invalid rules / missing JSON / oversized in-list / nested, 413 body too large, 500 error |
| POST | `/api/watchlist` | JSON: `{"name","rules"}` | `{"status":"ok","id","name"}` 201 | 400 invalid name or rules / missing JSON / oversized in-list / nested, 409 dup name, 413 body too large, 500 error |
| GET | `/api/watchlist` | — | `{"watchlists":[{"id","name","sql_where","created_at"}]}` | — |
| DELETE | `/api/watchlist/<int:wid>` | — | `{"status":"ok"}` | 404 |

### 5.6 Lightcurve Blueprint (`poi_broker/routes/lightcurve.py`) — Public
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/query_lightcurve_data` | Query: `locusId` | JSON `{div, script}` Bokeh components | 400/500 |
| GET | `/locus_plot_csv` | Query: `locusId` | CSV `locus_id,date_alert_mjd,ant_mag_corrected` | — |

### 5.7 Features Blueprint (`poi_broker/routes/features.py`) — Public
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/query_features` | Query: `alert_id` | JSON of all feature values | 400/404/500 |
| GET | `/query_featureplot_data` | Query: `locusId`, `features` | JSON `{div, script}` Bokeh components | 400/500 |

### 5.8 User Observatories Blueprint (`poi_broker/routes/user_observatories.py`, prefix `/api`) — all Login
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/api/user-observatories` | — | `{"userObservatories":[{"id","name","latitude","longitude","timezone_name","created_at"}]}` | — |
| POST | `/api/user-observatories` | JSON: `{"name","latitude","longitude"}` | `{"status":"ok",...}` 201 | 409; idempotent 200 on duplicate |
| DELETE | `/api/user-observatories/<int:observatory_id>` | — | `{"status":"ok"}` | idempotent 200 `already_deleted`; may include `warning`/`fallback` |
| POST | `/api/last-observatory` | JSON: `{"source":"builtin","name"}` or `{"source":"custom","id"}` | `{"status":"ok"}` | 400 invalid/unowned/unknown builtin; CSRF required. Builtin `name` must be in `EarthLocation.get_site_names()`. |

### 5.9 User Settings Blueprint (`poi_broker/user_settings.py`) — all Login
| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/settings` | — | HTML `user_settings.html` |
| POST | `/settings` | Form: `default_feature_plot_columns[]` | Saves default feature plot columns |

### 5.10 Observing Tool Blueprint (`poi_broker/observing_tool.py`) — Public
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/query_observing_plot` | Query: `obs_loc`, `obs_date`, `obs_tz`, `ra`, `dec` | JSON `{image, moonHtml}` (moon up), `{image, moonMessage}` (moon down), or `{message}` (not visible) | 401 for custom observatory without auth. Does not persist last-selected observatory. |

### 5.11 Classification Blueprint (`poi_broker/classification.py`) — Public
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/query_classification` | Query: `alertId` (max 128) | JSON `{div, script}` Bokeh radar chart; empty data is 200 warning `div` (same as lightcurve/feature) | 400 missing/too long. Does not reflect `alertId`. |

### 5.12 Export Blueprint (`poi_broker/routes/export.py`, prefix `/export`) — all Login
| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| GET | `/export` | — | HTML `export.html` (query builder + recent task) | — |
| POST | `/export` | JSON rules (query-builder shape) | `{"success":true,"task_id"}` 202 | 400 invalid / exceeds `MAX_EXPORT_ROWS` / oversized in-list / nested, 409 if a **non-stale** active task exists, 413 body too large, 500 error; stale active row (past `EXPORT_STALE_MAX_AGE_SECONDS`) is failed for this user then enqueue proceeds. A `snapshot_mjd` cutoff is computed at submit and applied to the pre-count and the export (rows with `date_alert_mjd >= snapshot_mjd` excluded) |
| GET | `/export/status/<int:task_id>` | — | JSON status (no `file_path`; includes `snapshot_mjd` and `data_as_of`) | 403/404 |
| GET | `/export/download/<int:task_id>` | — | CSV attachment | redirect + flash if not SUCCESS / missing file |

---

## 6. Workflows

### 6.1 Authentication Lifecycle
Signup → email verification (SHA-256 token, 24h expiry) → login (Flask-Login) → authenticated browsing. Password reset via emailed token (1h expiry). Change password requires current password. Role-based access via `role_required`.

Any password write (reset or change) bumps `user.password_changed_at`; the `user_loader` then rejects any session whose `login_at` marker is not newer than the watermark or absent — which ends every session and remembered login for the account (remember-cookie restores carry no `login_at`). The acting browser is logged out too and its remember-me cookie expired. Accounts with `password_changed_at IS NULL` behave exactly as before, so deployment logs nobody out. Schema for this feature was applied manually with a one-shot script (`tools/apply_password_changed_at.sql`, before restart; runbook `docs/password_reset/deployment.md` — the script was removed after production was migrated, see `docs/adr/adr_db_schema_migration_strategy.md`) — the application never alters the DB (ADR: `docs/password_reset/adr_session_invalidation.md`).

### 6.2 Browse & Filter Alerts
`GET /` builds a `Ztf` query with optional filters (date/MJD, alert_id prefix/full, object_id, passband, locus_id, RA/Dec ranges, magnitude, prob_class) and one sort key. Uses `catalog_query` + `SearchService` + `FilterService`. Keyset pagination 100/page for every sort (`?page=N>1` falls back to capped OFFSET+1 for legacy URLs). Exact row counts when cheap; otherwise `GET /api/catalog-count` on demand.

### 6.3 Visual Query → Watchlist
`GET /visual_query` → QueryBuilder UI → `POST /api/preview-query` (SQL preview) → `POST /api/export-query` (match count) → `POST /api/watchlist` (persist `rules_json`; store compiled SQL in `sql_where` as a display preview only). Uses `query_service` + `querybuilder_translator.py`.

Daily digest: `tools/watchlist_digest.py` re-runs `rules_json` through the ORM via `create_app()`. It never executes `sql_where`. **Gotcha:** the digest reads `tools/.env`, which is separate from the web app `.env`. `create_app()` requires `SECRET_KEY`; if that file omits it, older deploys crashed before any watchlist ran. The script now falls back to a CLI placeholder and logs a warning. Prefer the same `SECRET_KEY` as the web app (see `tools/.env.example`).

### 6.4 Lightcurve / Features / Classification
- Lightcurve: `GET /query_lightcurve_data` (JSON `{div, script}`) + `/locus_plot_csv`.
- Features: `GET /query_features` (values) + `/query_featureplot_data` (JSON `{div, script}`).
- Classification: `GET /query_classification` (JSON `{div, script}` radar chart).
- Observing last-selected site: `POST /api/last-observatory` after a successful plot (not the GET plot itself).

**Gotcha — empty plot ≠ HTTP error.** A missing classification row, empty lightcurve, or empty feature plot is HTTP **200** with `bokeh_warning_payload()` (static warning `div`, empty `script`). Do not “fix” that to 404/`{error}`: the modal `.fail` path is a red hard error, so empty tabs look like load failures. 400 is only invalid/missing params; never interpolate `alertId`/`locusId` into the warning.

### 6.5 Favorites & Groups
Toggle favorite per locus → organize into groups → list with counts. Deleting a group orphans its favorites (group_id SET NULL).

### 6.6 Filter Bookmarks
Save/restore main-table filter param sets (whitelisted keys) as named bookmarks.

### 6.7 Observatories & Observing Tool
CRUD custom observatories (auto timezone via `TimezoneFinder`); last selection persisted in `UserSettings`. Observing tool computes AltAz visibility from builtin/custom observatory.

### 6.8 Edge Cases
- Duplicate names → 409 (favorite groups, watchlists, filter bookmarks, observatories).
- Group delete → favorites orphaned (not deleted).
- Observatory delete → idempotent 200 `already_deleted`; may return `warning`/`fallback`.
- Watchlists strictly private (per-user).
- Active export slot → 409 only while a **non-stale** `PENDING`/`RUNNING` exists for the user; past `EXPORT_STALE_MAX_AGE_SECONDS`, `POST /export` fails that row and enqueues.

---

## 7. Feature-to-Spec Mapping

| Feature | Spec Requirement(s) | Primary Module(s) |
|---|---|---|
| Auth/account | REQ-001..006 | `poi_broker/auth.py` |
| Alert browsing/filtering | REQ-007 | `poi_broker/app.py`, `services/catalog_query.py`, `services/catalog_list.py`, `services/search_service.py`, `services/filter_service.py` |
| CSV download | REQ-008 | `poi_broker/app.py` |
| Crossmatch query | REQ-009 | `poi_broker/app.py` |
| Visual query / watchlists | REQ-010, REQ-011 | `routes/visual_query.py`, `services/query_service.py`, `querybuilder_translator.py` |
| Lightcurve | REQ-012 | `routes/lightcurve.py`, `services/plotting_service.py` |
| Features | REQ-013 | `routes/features.py`, `services/feature_service.py` |
| Favorites/groups | REQ-014 | `routes/favorites.py`, `services/favorites_service.py` |
| Filter bookmarks | REQ-015 | `routes/filter_bookmarks.py`, `services/filter_service.py` |
| Observatories | REQ-016 | `routes/user_observatories.py` |
| Observing tool | REQ-017 | `observing_tool.py` |
| Classification | REQ-018 | `classification.py` |
| Settings | REQ-019 | `user_settings.py` |
| Async CSV export | REQ-020 | `routes/export.py`, `tasks.py` |

---

## 8. Code-to-Spec Traceability Matrix

| Spec Requirement | Code (route/function) | Test |
|---|---|---|
| REQ-001..006 | `auth.py` (login/signup/verify/logout/forgot/reset/change) | `tests/test_auth.py`, `tests/test_security_regressions.py` (session invalidation) |
| REQ-007 | `app.py` `start()` + `catalog_query`/`catalog_list` + `SearchService`/`FilterService` | `tests/test_catalog_list.py`, `tests/test_app.py`, `test_alert_id_filter.py`, `test_dec_filter.py`, `test_ant_passband_filter.py` |
| REQ-008 | `app.py` `/download_alerts_csv` | `tests/test_app.py` |
| REQ-009 | `app.py` `/query_crossmatches` | `tests/test_app.py` |
| REQ-010, 011 | `routes/visual_query.py` + `QueryService` | `tests/test_visual_query*.py` |
| REQ-012 | `routes/lightcurve.py` + `PlottingService` | `tests/test_lightcurve*.py` |
| REQ-013 | `routes/features.py` + `FeatureService` | `tests/test_features*.py` |
| REQ-014 | `routes/favorites.py` + `FavoritesService` | `tests/test_favorites.py` |
| REQ-015 | `routes/filter_bookmarks.py` + `FilterService` | `tests/test_filter_bookmarks.py` |
| REQ-016 | `routes/user_observatories.py` | `tests/test_user_observatories*.py` |
| REQ-017 | `observing_tool.py` | `tests/test_observing_tool*.py` |
| REQ-018 | `classification.py` | `tests/test_classification.py` |
| REQ-019 | `user_settings.py` | `tests/test_user_settings*.py` |
| REQ-020 | `routes/export.py` `export_submit` + `tasks.reset_stale_export_tasks` | `tests/test_export_routes.py`, `tests/test_huey_tasks.py` |
| NFR-001..010 | `settings.py`, `__init__.py` (limiter, CSRF, headers, pragmas) | `tests/test_security_regressions.py`, `tests/test_smoke_routes.py` |

---

## 9. Known Gaps / Mismatches

| # | Gap | Detail | Source |
|---|---|---|---|
| G1 | No formal external spec | All requirements inferred from code; no authoritative external document | — |
| G2 | Resolved: CI and commit checks | GitHub Actions runs full tests/coverage; commit hooks run lightweight checks only | `.github/workflows/ci.yml`, `pre-commit.md` |
| G3 | Accepted: one-shot migrations | Manual SQL scripts are the chosen strategy, not missing implementation; original upstream main defines completion; the app never writes DDL | `docs/adr/adr_db_schema_migration_strategy.md`, `docs/adr/adr_migration_completion_status.md` |
| G4 | `requirements2.txt` not guaranteed installable | Reference freeze only; may not install on all systems | `requirements2.txt` |
| G5 | Per-module coverage not gated | CI enforces 75% total; individual-module targets remain advisory | `tests/coverage.md` |
| G6 | Internal service contracts undocumented | `QueryService`, `FavoritesService`, etc. excluded (per scope decision) | `services/` |
| G7 | Dual Popper (BS4 v1 bundle + TD6 v2) | Required while staying on Bootstrap 4 + Tempus Dominus 6. Do not unify without a major upgrade (§1.5) | `common_js.html`, `main_ui_js.html` |

---

## 10. Assumptions and Ambiguities

| # | Item | Status | Note |
|---|---|---|---|
| A1 | All functional requirements are inferred from code, not an authoritative spec | **Inferred** | Marked in §3 |
| A2 | `role`/`role_required` behavior beyond `user`/`admin` | **Uncertain** | Only `user` default and `is_admin` observed |
| A3 | Exact error message bodies for auth failures | **Uncertain** | Behavior tested, exact strings not enumerated here |
| A4 | Watchlist `rules_json` schema | **Uncertain** | Defined by `querybuilder_translator.py`; not enumerated in this doc |
| A5 | Observing tool custom-observatory auth boundary | **Uncertain** | 401 without auth; exact UX not specified |

---

## 11. Recommended Implementation Order

1. **Auth & account** (REQ-001..006) — foundational, blocks user-scoped features.
2. **Alert browsing/filtering + CSV + crossmatch** (REQ-007..009) — core read path.
3. **Favorites/groups** (REQ-014) — first user-scoped data feature.
4. **Filter bookmarks** (REQ-015) — depends on filter infrastructure.
5. **Visual query + watchlists** (REQ-010, 011) — depends on query builder.
6. **Lightcurve / features / classification** (REQ-012, 013, 018) — plotting layer.
7. **Observatories + observing tool** (REQ-016, 017).
8. **Settings** (REQ-019) — depends on observatories + feature columns.

---

## 12. Validation Strategy (tied to spec)

1. **Per-requirement tests:** Each REQ-xxx maps to an existing test file (§8). New features must add a matching `pytest` test.
2. **Full suite:** `python -m pytest -q` (or `python -m pytest -n auto -q --tb=no` for parallel).
3. **Route registration sanity:** `python -m flask --app wsgi:app routes` with `SECRET_KEY` set.
4. **Coverage gate:** aim 50–75%/module, 75%+ total per `tests/coverage.md`.
5. **Security regressions:** `tests/test_security_regressions.py` (CSRF/auth/rate-limit) must pass on any auth/security change.
6. **Manual smoke:** browser/API sanity check for any UI/endpoint change.

---

## 13. Documentation and validation considerations

1. **No authoritative external spec** — requirements are code-inferred; this document is the first formal reference.
2. **Per-module coverage enforcement** — CI gates total coverage; module targets remain advisory.
3. **Schema migration policy is settled** — one-shot SQL scripts are intentional; use the migration ledger for remaining upstream integration work.
4. **Validation baselines** — correctness lint is enforced; broader typing/security lint baselines are deferred.
5. **Internal service contracts undocumented** — service-layer APIs excluded from this spec (scope decision); may need a follow-up doc.
