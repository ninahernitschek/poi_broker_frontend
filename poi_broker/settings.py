import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

def _env_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


# Load environment variables from .env once at import time.
load_dotenv()


def _resolve_sqlite_path(env_var_name, default_path):
	configured_path = os.environ.get(env_var_name)
	if configured_path:
		return Path(configured_path).expanduser().resolve()
	return default_path.resolve()


def build_app_config(base_dir):
	workspace_root = base_dir.parent
	default_db_dir = workspace_root.parent / '_broker_db'

	db_path = _resolve_sqlite_path(
		'ALERTS_DB_PATH',
		default_db_dir / 'ztf_alerts_stream.db'
	)
	db_uri = 'sqlite:///{}'.format(db_path.as_posix())

	login_db_path = _resolve_sqlite_path(
		'USERS_DB_PATH',
		default_db_dir / 'users.db'
	)
	login_db_uri = 'sqlite:///{}'.format(login_db_path.as_posix())

	secret_key = os.environ.get('SECRET_KEY')
	if not secret_key:
		raise RuntimeError("SECRET_KEY environment variable must be set (generate with: python -c 'import secrets; print(secrets.token_hex(32))')")

	debug_flag = _env_bool(os.environ.get('FLASK_DEBUG'), False)
	testing_flag = _env_bool(os.environ.get('FLASK_TESTING'), False)
	production_flag = (not debug_flag) and (not testing_flag)

	# Enforce secure cookies in production; allow explicit opt-in in non-prod.
	session_cookie_secure = True if production_flag else _env_bool(os.environ.get('SESSION_COOKIE_SECURE'), False)
	remember_cookie_secure = True if production_flag else _env_bool(os.environ.get('REMEMBER_COOKIE_SECURE'), False)

	session_samesite = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax').strip().capitalize()
	if session_samesite not in {'Lax', 'Strict', 'None'}:
		session_samesite = 'Lax'

	remember_samesite = os.environ.get('REMEMBER_COOKIE_SAMESITE', session_samesite).strip().capitalize()
	if remember_samesite not in {'Lax', 'Strict', 'None'}:
		remember_samesite = session_samesite

	try:
		session_lifetime_hours = int(os.environ.get('SESSION_LIFETIME_HOURS', '24'))
	except ValueError:
		session_lifetime_hours = 24
	if session_lifetime_hours <= 0:
		session_lifetime_hours = 24

	auth_login_limit = os.environ.get('AUTH_RATE_LIMIT_LOGIN', '10 per minute').strip() or '10 per minute'
	auth_signup_limit = os.environ.get('AUTH_RATE_LIMIT_SIGNUP', '5 per hour').strip() or '5 per hour'
	auth_forgot_limit = os.environ.get('AUTH_RATE_LIMIT_FORGOT_PASSWORD', '5 per hour').strip() or '5 per hour'
	auth_reset_limit = os.environ.get('AUTH_RATE_LIMIT_RESET_PASSWORD', '10 per hour').strip() or '10 per hour'
	auth_change_pw_limit = os.environ.get('AUTH_RATE_LIMIT_CHANGE_PASSWORD', '10 per hour').strip() or '10 per hour'
	auth_verify_email_limit = os.environ.get('AUTH_RATE_LIMIT_VERIFY_EMAIL', '10 per hour').strip() or '10 per hour'
	read_rate_limit_lax = os.environ.get('READ_RATE_LIMIT_LAX', '30 per minute').strip() or '30 per minute'
	read_rate_limit_medium = os.environ.get('READ_RATE_LIMIT_MEDIUM', '15 per minute').strip() or '15 per minute'

	# Canonical public origin for emailed verification/reset links. When unset,
	# url_for(_external=True) follows Host / X-Forwarded-Host (unsafe if a
	# request reaches Gunicorn directly or a proxy forwards the client Host).
	public_base_url = (os.environ.get('PUBLIC_BASE_URL') or '').strip().rstrip('/')
	if public_base_url and not (
		public_base_url.startswith('https://') or public_base_url.startswith('http://')
	):
		public_base_url = ''

	config = {
		'DEBUG': debug_flag,
		'TESTING': testing_flag,
		'TEMPLATES_AUTO_RELOAD': True,
		'APP_VERSION': '3.5.0',
		'SQLALCHEMY_DATABASE_URI': db_uri,
		'SQLALCHEMY_BINDS': {
			'users': login_db_uri,
		},
		'SQLALCHEMY_TRACK_MODIFICATIONS': False,
		# Set to zero to disable optional counts on slower deployments.
		'CATALOG_COUNT_BUDGET_SECONDS': max(0.0, float(os.environ.get('CATALOG_COUNT_BUDGET_SECONDS', '2.0'))),
		'SECRET_KEY': secret_key,
		# The app has no file uploads and its JSON payloads (query-builder
		# rules, favorites, settings) are KB-scale; bound request bodies so a
		# single oversized request cannot pin memory or bloat stored rows.
		'MAX_CONTENT_LENGTH': 1024 * 1024,
		'PERMANENT_SESSION_LIFETIME': timedelta(hours=session_lifetime_hours),
		'SESSION_COOKIE_SECURE': session_cookie_secure,
		'SESSION_COOKIE_HTTPONLY': True,
		'SESSION_COOKIE_SAMESITE': session_samesite,
		'SESSION_REFRESH_EACH_REQUEST': False,
		'REMEMBER_COOKIE_SECURE': remember_cookie_secure,
		'REMEMBER_COOKIE_HTTPONLY': True,
		'REMEMBER_COOKIE_SAMESITE': remember_samesite,
		'REMEMBER_COOKIE_DURATION': timedelta(days=14),
		'RATELIMIT_ENABLED': _env_bool(os.environ.get('RATELIMIT_ENABLED'), production_flag),
		# memory:// is per-process. Multi-worker Gunicorn needs a shared URI.
		'RATELIMIT_STORAGE_URI': os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'),
		'PUBLIC_BASE_URL': public_base_url or None,
		'AUTH_RATE_LIMIT_LOGIN': auth_login_limit,
		'AUTH_RATE_LIMIT_SIGNUP': auth_signup_limit,
		'AUTH_RATE_LIMIT_FORGOT_PASSWORD': auth_forgot_limit,
		'AUTH_RATE_LIMIT_RESET_PASSWORD': auth_reset_limit,
		'AUTH_RATE_LIMIT_CHANGE_PASSWORD': auth_change_pw_limit,
		'AUTH_RATE_LIMIT_VERIFY_EMAIL': auth_verify_email_limit,
		'READ_RATE_LIMIT_LAX': read_rate_limit_lax,
		'READ_RATE_LIMIT_MEDIUM': read_rate_limit_medium,
	}

	return config, db_path, login_db_path

