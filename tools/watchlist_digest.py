#!/usr/bin/env python3
"""Send daily watchlist digests to users.

This script is intended for cron usage. It reads all watchlists from users.db,
rebuilds each filter from stored rules_json (never executes sql_where), and
emails the top N matching alert IDs created yesterday (UTC midnight-to-midnight)
to each watchlist owner.

What it does:

- Reads all configured watchlists from users.db.
- Resolves the UTC window for yesterday as midnight-to-midnight.
- Re-runs each watchlist's querybuilder rules against featuretable with:
date_alert_mjd >= yesterday_utc_midnight_mjd
date_alert_mjd < today_utc_midnight_mjd
LIMIT 1000 (configurable via --limit)
- Groups results by watchlist owner and sends one HTML email per user.
- Renders each matching alert_id as a clickable link to:
BASE_URL/?alert_id=<alert_id>
BASE_URL comes from POI_BROKER_BASE_URL (default http://localhost:5000)

Implemented behavior:
- Top 1000 per watchlist.
- Only rows from yesterday UTC based on midnight boundaries.
- Email recipient is the registered email of the user who created the watchlist.
- HTML email contains clickable alert_id links to the main table filter URL.

Operational options included:
```
--dry-run to test without sending any email, including administrator error notifications
--only-email to target a single user by email address
--limit to change per-watchlist cap (default 1000)
--log-level for diagnostics (default WARNING)
--skip-empty to skip sending emails when a user has zero matches
```

Gotcha — SECRET_KEY and tools/.env:

The digest rebuilds filters through create_app() so it can run ORM queries
from rules_json (sql_where is display-only and must never be executed).
create_app() requires SECRET_KEY. tools/.env is a separate file from the
web app .env; copying only the digest template used to omit SECRET_KEY and
the cron job then crashed before any watchlist ran.

If SECRET_KEY is unset, this script sets a local CLI placeholder and logs a
warning. That is enough because the digest is not an HTTP server. Prefer
copying the web app SECRET_KEY into tools/.env so the warning stays off.

Expected environment variables:
- SECRET_KEY: optional; reuse the web app value. Placeholder used if unset.
- ALERTS_DB_PATH: absolute/relative path to ztf_alerts_stream.db (optional)
- USERS_DB_PATH: absolute/relative path to users.db (optional)
- POI_BROKER_BASE_URL: app base URL for alert link generation (default: http://localhost:5000)
- ADMIN_EMAIL: email address to receive watchlist processing error notifications
- SMTP settings via existing mail flow: SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_APP_PASSWORD


Example cron line (adjust paths):
```
0 0 * * * /usr/bin/python3 /path/to/repo/tools/watchlist_digest.py --log-level INFO >> /var/log/watchlist_digest.log 2>&1
```
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import cast
from urllib.parse import quote

from astropy.time import Time
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent

# Load .env file from script directory
dotenv_path = SCRIPT_DIR / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)

if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from poi_broker import create_app, db  # noqa: E402
from poi_broker.services.email_service import send_email  # noqa: E402
from poi_broker.services.query_service import execute_watchlist  # noqa: E402


logger = logging.getLogger("watchlist_digest")

# create_app() requires SECRET_KEY for Flask session config. The digest never
# serves HTTP, so a local placeholder is enough when tools/.env has none.
_DIGEST_SECRET_KEY_FALLBACK = "watchlist-digest-cli"


def _ensure_digest_secret_key() -> None:
    if os.environ.get("SECRET_KEY"):
        return
    os.environ["SECRET_KEY"] = _DIGEST_SECRET_KEY_FALLBACK
    logger.warning(
        "SECRET_KEY is unset; using a digest-only placeholder so create_app() "
        "can rebuild ORM filters. Set SECRET_KEY in tools/.env to silence this."
    )


@dataclass
class WatchlistResult:
    watchlist_id: int
    watchlist_name: str
    sql_where: str
    alert_ids: list[str]
    error: str | None = None


def _resolve_sqlite_path(env_var_name: str, default_path: Path) -> Path:
    configured_path = os.environ.get(env_var_name)
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return default_path.resolve()


def resolve_db_paths() -> tuple[Path, Path]:
    default_db_dir = WORKSPACE_ROOT.parent / "_broker_db"
    alerts_db = _resolve_sqlite_path("ALERTS_DB_PATH", default_db_dir / "ztf_alerts_stream.db")
    users_db = _resolve_sqlite_path("USERS_DB_PATH", default_db_dir / "users.db")
    return alerts_db, users_db


def utc_yesterday_mjd_range(now_utc: datetime | None = None) -> tuple[float, float, str]:
    now_utc = now_utc or datetime.now(timezone.utc)
    today_midnight = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    yesterday_midnight = today_midnight - timedelta(days=1)

    start_mjd = cast(float, Time(yesterday_midnight, scale="utc").to_value("mjd", subfmt="float"))
    end_mjd = cast(float, Time(today_midnight, scale="utc").to_value("mjd", subfmt="float"))
    day_label = yesterday_midnight.date().isoformat()
    return start_mjd, end_mjd, day_label


def load_watchlists(users_db: Path, only_email: str | None = None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(users_db)
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT w.id AS watchlist_id, w.user_id, w.name AS watchlist_name, "
            "w.rules_json, w.sql_where, "
            "u.email AS user_email, u.name AS user_name "
            "FROM watchlist w "
            "JOIN user u ON u.id = w.user_id "
            "WHERE u.email IS NOT NULL AND TRIM(u.email) <> '' "
            "ORDER BY w.user_id, w.id"
        )
        params: tuple[object, ...] = ()
        if only_email:
            sql = sql.replace("ORDER BY", "AND lower(u.email) = lower(?) ORDER BY")
            params = (only_email,)
        rows = conn.execute(sql, params).fetchall()
        return rows
    finally:
        conn.close()


def parse_watchlist_rules(rules_json: str) -> dict:
    """Deserialize persisted rules_json. Raises ValueError if it is not a rules dict."""
    try:
        payload = json.loads(rules_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid watchlist rules_json: {exc}') from exc
    if not isinstance(payload, dict) or 'rules' not in payload:
        raise ValueError('Invalid watchlist rules_json: expected a querybuilder rules object')
    return payload


def build_alert_link(base_url: str, alert_id: str) -> str:
    return f"{base_url}/?alert_id={quote(alert_id, safe='')}"


def build_email_content(base_url: str, user_name: str | None, day_label: str, results: list[WatchlistResult]) -> tuple[str, str, str]:
    greeting = user_name or "there"
    subject = f"POI Broker Daily Watchlist Digest - {day_label} UTC"

    plain_lines = [
        f"Hi {greeting},",
        "",
        f"POI Broker daily watchlist results for {day_label} (UTC)",
        "",
    ]

    total_matches = sum(len(item.alert_ids) for item in results)
    html_parts = [
        "<html><body style=\"font-family:Segoe UI, Arial, sans-serif; color:#111827;\">",
        "<div style=\"max-width:760px; margin:0 auto; padding:16px;\">",
        "<h2 style=\"margin:0 0 8px; color:#0f172a;\">POI Broker</h2>",
        "<p style=\"margin:0 0 16px; color:#334155;\">Daily Watchlist Digest</p>",
        f"<p>Hi {escape(greeting)},</p>",
        f"<p>Here are your results for <strong>{escape(day_label)}</strong> (UTC). Total matches: <strong>{total_matches}</strong>.</p>",
    ]

    for item in results:
        plain_lines.append(f"Watchlist: {item.watchlist_name}")
        html_parts.append("<div style=\"border:1px solid #e2e8f0; border-radius:8px; padding:12px; margin:12px 0;\">")
        html_parts.append(f"<h4 style=\"margin:0 0 8px;\">Watchlist: {escape(item.watchlist_name)}</h4>")

        if item.error:
            plain_lines.append("  Error: An internal error occurred while processing this watchlist; the administrator has been notified.")
            html_parts.append("<p style=\"margin:0; color:#b91c1c;\"><em>An internal error occurred while processing this watchlist; the administrator has been notified.</em></p>")
            html_parts.append("</div>")
            plain_lines.append("")
            continue

        if not item.alert_ids:
            plain_lines.append("  No matches for this day.")
            html_parts.append("<p style=\"margin:0; color:#475569;\">No matches for this day.</p>")
            html_parts.append("</div>")
            plain_lines.append("")
            continue

        plain_lines.append(f"  Matches: {len(item.alert_ids)}")
        html_parts.append(f"<p style=\"margin:0 0 8px;\">Matches: <strong>{len(item.alert_ids)}</strong></p>")
        html_parts.append("<ul style=\"margin:0; padding-left:20px;\">")

        for alert_id in item.alert_ids:
            link = build_alert_link(base_url, alert_id)
            plain_lines.append(f"  - {alert_id}: {link}")
            html_parts.append(f"<li style=\"margin:2px 0;\"><a href=\"{escape(link, quote=True)}\" style=\"color:#1d4ed8; text-decoration:none;\">{escape(alert_id)}</a></li>")

        html_parts.append("</ul>")
        html_parts.append("</div>")
        plain_lines.append("")

    plain_lines.append("This message was sent by POI Broker.")
    plain_lines.append("This is an automated email.")
    html_parts.append("<p style=\"margin-top:16px; color:#475569;\">This message was sent by <strong>POI Broker</strong>. This is an automated email.</p>")
    html_parts.append("</div></body></html>")

    return subject, "\n".join(plain_lines), "".join(html_parts)


def send_admin_error_notification(admin_email: str | None, user_email: str, user_name: str | None, row: sqlite3.Row, error: str, day_label: str) -> None:
    if not admin_email:
        logger.warning(
            "ADMIN_EMAIL is not configured; cannot notify admin about watchlist id=%s for user %s",
            row["watchlist_id"],
            user_email,
        )
        return

    subject = f"POI Broker Watchlist Digest Error - {day_label} UTC"
    user_label = user_name or user_email
    plain_lines = [
        f"POI Broker encountered an error while processing watchlist {row['watchlist_id']} for {user_label}",
        "",
        f"User email: {user_email}",
        f"User name: {user_name or '<none>'}",
        f"Watchlist id: {row['watchlist_id']}",
        f"Watchlist name: {row['watchlist_name']}",
        "",
        "SQL filter:",
        str(row["sql_where"]),
        "",
        "Error:",
        error,
    ]
    html_parts = [
        "<html><body style=\"font-family:Segoe UI, Arial, sans-serif; color:#111827;\">",
        "<div style=\"max-width:760px; margin:0 auto; padding:16px;\">",
        f"<h2 style=\"margin:0 0 8px; color:#0f172a;\">POI Broker Watchlist Digest Error</h2>",
        f"<p style=\"margin:0 0 8px;\">Watchlist id <strong>{escape(str(row['watchlist_id']))}</strong> for <strong>{escape(user_label)}</strong> failed during digest processing for {escape(day_label)} UTC.</p>",
        "<table style=\"width:100%; border-collapse:collapse; margin-top:12px;\">",
        "<tbody>",
        f"<tr><td style=\"padding:6px; border:1px solid #e2e8f0; font-weight:700;\">User email</td><td style=\"padding:6px; border:1px solid #e2e8f0;\">{escape(user_email)}</td></tr>",
        f"<tr><td style=\"padding:6px; border:1px solid #e2e8f0; font-weight:700;\">User name</td><td style=\"padding:6px; border:1px solid #e2e8f0;\">{escape(user_name or '<none>')}</td></tr>",
        f"<tr><td style=\"padding:6px; border:1px solid #e2e8f0; font-weight:700;\">Watchlist name</td><td style=\"padding:6px; border:1px solid #e2e8f0;\">{escape(str(row['watchlist_name']))}</td></tr>",
        f"<tr><td style=\"padding:6px; border:1px solid #e2e8f0; font-weight:700;\">Date</td><td style=\"padding:6px; border:1px solid #e2e8f0;\">{escape(day_label)} UTC</td></tr>",
        "</tbody>",
        "</table>",
        "<h3 style=\"margin:16px 0 8px;\">SQL filter</h3>",
        f"<pre style=\"white-space:pre-wrap; background:#f8fafc; padding:12px; border:1px solid #e2e8f0;\">{escape(str(row['sql_where']))}</pre>",
        "<h3 style=\"margin:16px 0 8px;\">Error details</h3>",
        f"<pre style=\"white-space:pre-wrap; background:#f8fafc; padding:12px; border:1px solid #e2e8f0;\">{escape(error)}</pre>",
        "</div></body></html>",
    ]

    ok = send_email(
        message="\n".join(plain_lines),
        to_email=admin_email,
        subject=subject,
        html_text="".join(html_parts),
    )
    if not ok:
        logger.error("Failed to send admin notification to %s for watchlist id=%s", admin_email, row["watchlist_id"])


def run(limit: int, dry_run: bool, only_email: str | None, skip_empty: bool) -> int:
    alerts_db, users_db = resolve_db_paths()

    if not users_db.exists():
        logger.error("Users DB not found: %s", users_db)
        return 2
    if not alerts_db.exists():
        logger.error("Alerts DB not found: %s", alerts_db)
        return 2

    start_mjd, end_mjd, day_label = utc_yesterday_mjd_range()
    base_url = os.environ.get("POI_BROKER_BASE_URL", "http://localhost:5000").rstrip("/")
    admin_email = os.environ.get("ADMIN_EMAIL")

    logger.info("Users DB: %s", users_db)
    logger.info("Alerts DB: %s", alerts_db)
    logger.info("Window: [%s, %s) MJD for UTC date %s", start_mjd, end_mjd, day_label)

    watchlist_rows = load_watchlists(users_db, only_email=only_email)
    if not watchlist_rows:
        logger.info("No watchlists found.")
        return 0

    per_user: dict[tuple[int, str, str | None], list[WatchlistResult]] = defaultdict(list)

    _ensure_digest_secret_key()
    app = create_app()
    with app.app_context():
        for row in watchlist_rows:
            user_key = (int(row["user_id"]), str(row["user_email"]), row["user_name"])
            try:
                rules_payload = parse_watchlist_rules(str(row["rules_json"]))
                alert_ids = execute_watchlist(
                    rules_payload,
                    start_mjd=start_mjd,
                    end_mjd=end_mjd,
                    limit=limit,
                )
                per_user[user_key].append(
                    WatchlistResult(
                        watchlist_id=int(row["watchlist_id"]),
                        watchlist_name=str(row["watchlist_name"]),
                        sql_where=str(row["sql_where"]),
                        alert_ids=alert_ids,
                    )
                )
            except Exception as exc:
                logger.exception("Failed watchlist id=%s", row["watchlist_id"])
                db.session.rollback()
                if not dry_run:
                    send_admin_error_notification(
                        admin_email=admin_email,
                        user_email=str(row["user_email"]),
                        user_name=row["user_name"],
                        row=row,
                        error=str(exc),
                        day_label=day_label,
                    )
                per_user[user_key].append(
                    WatchlistResult(
                        watchlist_id=int(row["watchlist_id"]),
                        watchlist_name=str(row["watchlist_name"]),
                        sql_where=str(row["sql_where"]),
                        alert_ids=[],
                        error="An internal error occurred while processing this watchlist; the administrator has been notified.",
                    )
                )

    sent_count = 0
    failed_count = 0

    for (_user_id, user_email, user_name), results in per_user.items():
        total_matches = sum(len(r.alert_ids) for r in results)
        if skip_empty and total_matches == 0:
            logger.info("Skipping %s (no matches across %d watchlists)", user_email, len(results))
            continue

        subject, plain_text, html_text = build_email_content(
            base_url=base_url,
            user_name=user_name,
            day_label=day_label,
            results=results,
        )

        if dry_run:
            logger.info("[DRY-RUN] Would send to %s (%d watchlists, %d matches)", user_email, len(results), total_matches)
            sent_count += 1
            continue

        ok = send_email(
            message=plain_text,
            to_email=user_email,
            subject=subject,
            html_text=html_text,
        )
        if ok:
            sent_count += 1
            logger.info("Sent digest to %s", user_email)
        else:
            failed_count += 1
            logger.error("Failed sending digest to %s", user_email)

    logger.info("Done. sent=%d failed=%d users=%d", sent_count, failed_count, len(per_user))
    return 0 if failed_count == 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send daily watchlist digest emails.")
    parser.add_argument("--limit", type=int, default=1000, help="Max rows per watchlist (default: 1000)")
    parser.add_argument("--dry-run", action="store_true", help="Build digests but do not send emails")
    parser.add_argument("--skip-empty", action="store_true", help="Skip sending emails when a user has zero matches")
    parser.add_argument("--only-email", type=str, default=None, help="Only process watchlists for one email")
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    if args.limit <= 0:
        logger.error("--limit must be > 0")
        return 2
    return run(limit=args.limit, dry_run=args.dry_run, only_email=args.only_email, skip_empty=args.skip_empty)


if __name__ == "__main__":
    raise SystemExit(main())
