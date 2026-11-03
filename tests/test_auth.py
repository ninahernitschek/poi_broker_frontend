from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash

import poi_broker.auth as auth_module
from poi_broker.models import User


def test_normalize_email_validates_and_normalizes_address():
    normalized = auth_module.normalize_email('Test@Example.COM', check_deliverability=False)

    assert normalized == 'Test@example.com'


def test_normalize_email_rejects_invalid_address():
    assert auth_module.normalize_email('not-an-email', check_deliverability=False) is None


def test_signup_post_rejects_invalid_email(client, monkeypatch):
    monkeypatch.setattr(auth_module, 'normalize_email', lambda email, check_deliverability=True: None)

    response = client.post(
        '/signup',
        data={'email': 'invalid', 'name': 'Test User', 'password': 'Password123!'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert '/signup' in response.location


def test_signup_post_rejects_short_password(client, monkeypatch):
    monkeypatch.setattr(auth_module, 'normalize_email', lambda email, check_deliverability=True: 'user@example.com')

    response = client.post(
        '/signup',
        data={'email': 'user@example.com', 'name': 'Test User', 'password': 'short'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert '/signup' in response.location


def _flashed_messages(client):
    with client.session_transaction() as session:
        return [msg for _cat, msg in session.get('_flashes', [])]


def test_signup_post_rejects_duplicate_email(client, app, monkeypatch, user_factory):
    user_factory(email='duplicate@example.com')
    monkeypatch.setattr(auth_module, 'normalize_email', lambda email, check_deliverability=True: 'duplicate@example.com')

    response = client.post(
        '/signup',
        data={'email': 'duplicate@example.com', 'name': 'Duplicate User', 'password': 'Password123!'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert '/login' in response.location
    assert _flashed_messages(client) == [auth_module.SIGNUP_GENERIC_NOTICE]

    with app.app_context():
        users = User.query.filter_by(email='duplicate@example.com').all()
        assert len(users) == 1


def test_signup_post_creates_user_and_sends_verification_email(client, app, monkeypatch):
    monkeypatch.setattr(auth_module, 'normalize_email', lambda email, check_deliverability=True: email.lower())
    email_calls = []

    def fake_send_email(*args, **kwargs):
        email_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(auth_module, 'send_email', fake_send_email)

    response = client.post(
        '/signup',
        data={'email': 'SignupUser@example.com', 'name': 'Signup User', 'password': 'Password123!'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert '/login' in response.location
    assert _flashed_messages(client) == [auth_module.SIGNUP_GENERIC_NOTICE]
    assert email_calls, 'Expected send_email() to be called'

    with app.app_context():
        user = User.query.filter_by(email='signupuser@example.com').first()
        assert user is not None
        assert user.email_verified is False
        assert user.email_verification_token is not None
        assert user.email_verification_token_expires is not None
        assert user.email_verification_token_expires > int(datetime.now(timezone.utc).timestamp())


def test_login_post_invalid_credentials_redirects_to_forgot_password(client):
    response = client.post(
        '/login',
        data={'email': 'missing@example.com', 'password': 'wrong'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert 'forgot_password=True' in response.location


def test_login_post_unverified_user_redirects_to_login(client, user_factory):
    user_factory(email='pending@example.com', verified=False)

    response = client.post(
        '/login',
        data={'email': 'pending@example.com', 'password': 'Password123!'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert '/login' in response.location
    assert 'forgot_password' not in response.location


def test_login_post_success_redirects_to_profile(client, user_factory):
    user_factory(email='active@example.com', password='Password123!', verified=True)

    response = client.post(
        '/login',
        data={'email': 'active@example.com', 'password': 'Password123!'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert '/profile' in response.location


def test_verify_email_invalid_token_redirects_to_signup(client):
    response = client.get('/verify-email/invalid-token', follow_redirects=False)

    assert response.status_code == 302
    assert '/signup' in response.location


def test_verify_email_marks_user_verified(client, app):
    from poi_broker.auth import hash_token

    raw_token = 'verify-token'
    with app.app_context():
        user = User(
            email='verify@example.com',
            password=generate_password_hash('Password123!'),
            name='Verify User',
            email_verified=False,
            email_verification_token=hash_token(raw_token),
            email_verification_token_expires=int(datetime.now(timezone.utc).timestamp()) + 3600,
        )
        from poi_broker import db
        db.session.add(user)
        db.session.commit()

    # GET only validates the token and shows the confirmation page, so
    # link-scanner prefetches cannot consume the one-time token.
    page = client.get(f'/verify-email/{raw_token}', follow_redirects=False)
    assert page.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email='verify@example.com').first()
        assert user.email_verified is False
        assert user.email_verification_token is not None

    response = client.post(f'/verify-email/{raw_token}', follow_redirects=False)

    assert response.status_code == 302
    assert '/login' in response.location

    with app.app_context():
        user = User.query.filter_by(email='verify@example.com').first()
        assert user.email_verified is True
        assert user.email_verification_token is None
        assert user.email_verification_token_expires is None


def test_login_renders_success_and_danger_flash_categories(client, app):
    """F12: success flashes must not paint as alert-danger on /login."""
    from poi_broker.auth import hash_token

    raw_token = 'verify-flash-token'
    with app.app_context():
        user = User(
            email='verify-flash@example.com',
            password=generate_password_hash('Password123!'),
            name='Verify Flash User',
            email_verified=False,
            email_verification_token=hash_token(raw_token),
            email_verification_token_expires=int(datetime.now(timezone.utc).timestamp()) + 3600,
        )
        from poi_broker import db
        db.session.add(user)
        db.session.commit()

    confirm_page = client.get(f'/verify-email/{raw_token}', follow_redirects=True)
    assert confirm_page.status_code == 200

    success_page = client.post(f'/verify-email/{raw_token}', follow_redirects=True)
    assert success_page.status_code == 200
    body = success_page.get_data(as_text=True)
    assert 'alert-success' in body
    assert 'Email verified successfully!' in body
    assert 'alert-danger' not in body

    danger_page = client.post(
        '/login',
        data={'email': 'nobody@example.com', 'password': 'wrong'},
        follow_redirects=True,
    )
    assert danger_page.status_code == 200
    danger_body = danger_page.get_data(as_text=True)
    assert 'alert-danger' in danger_body
    assert 'Please check your login details' in danger_body
    assert 'alert-success' not in danger_body


def test_login_renders_info_flash_category(client, monkeypatch):
    """Signup generic notice must render as alert-info, not alert-danger."""
    monkeypatch.setattr(auth_module, 'normalize_email', lambda email, check_deliverability=True: email.lower())
    monkeypatch.setattr(auth_module, 'send_email', lambda *args, **kwargs: True)

    response = client.post(
        '/signup',
        data={'email': 'info-flash@example.com', 'name': 'Info Flash', 'password': 'Password123!'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'alert-info' in body
    assert auth_module.SIGNUP_GENERIC_NOTICE in body
    assert 'alert-danger' not in body


def test_forgot_password_post_generates_reset_token_and_emails_user(client, app, monkeypatch, user_factory):
    user_factory(email='reset@example.com', verified=True)
    sent = []
    monkeypatch.setattr(auth_module, 'send_email', lambda *args, **kwargs: sent.append((args, kwargs)) or True)

    response = client.post(
        '/forgot-password',
        data={'email': 'reset@example.com'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert '/login' in response.location
    assert _flashed_messages(client) == [auth_module.FORGOT_PASSWORD_GENERIC_NOTICE]

    with app.app_context():
        user = User.query.filter_by(email='reset@example.com').first()
        assert user.reset_token is None
        assert user.reset_token_expires is None
        assert user.password_changed_at is None
    assert len(sent) == 1
    assert '/reset-password/v1.' in sent[0][0][0]
    assert sent[0][1]['expire_time'] > int(datetime.now(timezone.utc).timestamp())


def test_forgot_password_post_unknown_email_uses_same_notice(client):
    response = client.post(
        '/forgot-password',
        data={'email': 'missing@example.com'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert '/login' in response.location
    assert _flashed_messages(client) == [auth_module.FORGOT_PASSWORD_GENERIC_NOTICE]


def test_verify_email_expired_token_redirects_to_signup(client, app):
    from poi_broker.auth import hash_token

    raw_token = 'expired-verify-token'
    with app.app_context():
        user = User(
            email='expired-verify@example.com',
            password=generate_password_hash('Password123!'),
            name='Expired Verify User',
            email_verified=False,
            email_verification_token=hash_token(raw_token),
            email_verification_token_expires=int(datetime.now(timezone.utc).timestamp()) - 10,
        )
        from poi_broker import db
        db.session.add(user)
        db.session.commit()

    response = client.get(f'/verify-email/{raw_token}', follow_redirects=False)

    assert response.status_code == 302
    assert '/signup' in response.location

    with app.app_context():
        user = User.query.filter_by(email='expired-verify@example.com').first()
        assert user.email_verified is False
        assert user.email_verification_token is not None


def test_reset_password_invalid_token_redirects_to_login(client):
    response = client.get('/reset-password/invalid-token', follow_redirects=False)

    assert response.status_code == 302
    assert '/login' in response.location


def test_verify_email_missing_expiry_is_rejected(client, app):
    from poi_broker.auth import hash_token

    raw_token = 'legacy-verify-token'
    with app.app_context():
        user = User(
            email='legacy-verify@example.com',
            password=generate_password_hash('Password123!'),
            name='Legacy Verify User',
            email_verified=False,
            email_verification_token=hash_token(raw_token),
            email_verification_token_expires=None,
        )
        from poi_broker import db
        db.session.add(user)
        db.session.commit()

    response = client.get(f'/verify-email/{raw_token}', follow_redirects=False)

    assert response.status_code == 302
    assert '/signup' in response.location

    with app.app_context():
        user = User.query.filter_by(email='legacy-verify@example.com').first()
        assert user.email_verified is False


def test_public_base_url_requires_http_scheme(tmp_path, monkeypatch):
    from poi_broker.settings import build_app_config

    monkeypatch.setenv('SECRET_KEY', 'settings-secret')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'evil.example')
    monkeypatch.setenv('ALERTS_DB_PATH', str(tmp_path / 'alerts.db'))
    monkeypatch.setenv('USERS_DB_PATH', str(tmp_path / 'users.db'))

    config, _, _ = build_app_config(tmp_path)
    assert config['PUBLIC_BASE_URL'] is None

    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://poi.example.edu/')
    config, _, _ = build_app_config(tmp_path)
    assert config['PUBLIC_BASE_URL'] == 'https://poi.example.edu'


def test_is_reset_token_expired_and_format_expire_time():
    assert auth_module._is_reset_token_expired(None)
    assert auth_module._is_reset_token_expired('bad')

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert auth_module._is_reset_token_expired(future) is False


def test_verify_email_rate_limit_is_env_configurable(tmp_path, monkeypatch):
    from poi_broker.settings import build_app_config

    monkeypatch.setenv('SECRET_KEY', 'settings-secret')
    monkeypatch.setenv('ALERTS_DB_PATH', str(tmp_path / 'alerts.db'))
    monkeypatch.setenv('USERS_DB_PATH', str(tmp_path / 'users.db'))
    monkeypatch.setenv('AUTH_RATE_LIMIT_VERIFY_EMAIL', '3 per hour')

    config, _, _ = build_app_config(tmp_path)
    assert config['AUTH_RATE_LIMIT_VERIFY_EMAIL'] == '3 per hour'
