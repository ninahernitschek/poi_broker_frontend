import re
import time

from werkzeug.security import generate_password_hash


def _extract_hidden_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, "Could not find hidden csrf_token input"
    return match.group(1)


def _extract_meta_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
    assert match, "Could not find csrf-token meta tag"
    return match.group(1)


def _create_verified_user(db, User, email: str, password: str, name: str):
    user = User(
        email=email,
        password=generate_password_hash(password),
        name=name,
        email_verified=True,
    )
    db.session.add(user)
    db.session.commit()
    return user.id


def _force_login(client, user_id: int, login_age_seconds: int = 0) -> None:
    """Forge a session exactly the way POST /login leaves one behind,
    including the login_at marker validated by the user_loader."""
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        session["login_at"] = int(time.time()) - login_age_seconds


def test_api_csrf_missing_token_rejected(secure_app, secure_client):
    from poi_broker import db
    from poi_broker.models import User

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "csrf1@example.com", "Password123!", "CSRF User")

    _force_login(secure_client, user_id)

    response = secure_client.post("/api/favorite", json={"locusId": "locus-csrf-1", "fav": True, "groupId": None})
    assert response.status_code == 400
    assert response.is_json
    payload = response.get_json()
    assert payload.get("error") == "csrf validation failed"


def test_api_csrf_header_allows_authenticated_post(secure_app, secure_client):
    from poi_broker import db
    from poi_broker.models import User

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "csrf2@example.com", "Password123!", "CSRF User 2")

    _force_login(secure_client, user_id)

    html = secure_client.get("/").get_data(as_text=True)
    csrf_token = _extract_meta_csrf_token(html)

    response = secure_client.post(
        "/api/favorite",
        json={"locusId": "locus-csrf-2", "fav": True, "groupId": None},
        headers={"X-CSRFToken": csrf_token},
    )
    assert response.status_code == 200
    assert response.is_json
    assert response.get_json().get("status") == "ok"


def test_last_observatory_csrf_missing_token_rejected(secure_app, secure_client):
    from poi_broker import db
    from poi_broker.models import User

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "csrf-obs1@example.com", "Password123!", "CSRF Obs User")

    _force_login(secure_client, user_id)

    response = secure_client.post(
        "/api/last-observatory",
        json={"source": "builtin", "name": "Palomar"},
    )
    assert response.status_code == 400
    assert response.is_json
    payload = response.get_json()
    assert payload.get("error") == "csrf validation failed"


def test_last_observatory_csrf_header_allows_authenticated_post(secure_app, secure_client):
    from poi_broker import db
    from poi_broker.models import User
    from poi_broker.user_settings import get_saved_last_selected_observatory

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "csrf-obs2@example.com", "Password123!", "CSRF Obs User 2")

    _force_login(secure_client, user_id)

    html = secure_client.get("/").get_data(as_text=True)
    csrf_token = _extract_meta_csrf_token(html)

    response = secure_client.post(
        "/api/last-observatory",
        json={"source": "builtin", "name": "Palomar"},
        headers={"X-CSRFToken": csrf_token},
    )
    assert response.status_code == 200
    assert response.is_json
    assert response.get_json().get("status") == "ok"

    with secure_app.app_context():
        assert get_saved_last_selected_observatory(user_id) == {"source": "builtin", "name": "Palomar"}


def test_cross_user_cannot_delete_other_watchlist(secure_app, secure_client):
    from poi_broker import db
    from poi_broker.models import User, Watchlist

    with secure_app.app_context():
        owner_id = _create_verified_user(db, User, "owner@example.com", "Password123!", "Owner")
        intruder_id = _create_verified_user(db, User, "intruder@example.com", "Password123!", "Intruder")

        wl = Watchlist(
            user_id=owner_id,
            name="Owner Watchlist",
            rules_json='{"condition":"AND","rules":[{"field":"featuretable.alert_id","operator":"is_not_null"}]}',
            sql_where="featuretable.alert_id IS NOT NULL",
            created_at=1711929600,
        )
        db.session.add(wl)
        db.session.commit()
        watchlist_id = wl.id

    _force_login(secure_client, intruder_id)
    html = secure_client.get("/").get_data(as_text=True)
    csrf_token = _extract_meta_csrf_token(html)

    response = secure_client.delete(
        f"/api/watchlist/{watchlist_id}",
        headers={"X-CSRFToken": csrf_token},
    )
    assert response.status_code == 404


    with secure_app.app_context():
        still_exists = Watchlist.query.filter_by(id=watchlist_id).first()
        assert still_exists is not None


def test_signup_verification_link_uses_public_base_url(client, app, monkeypatch):
    import poi_broker.auth as auth_module

    app.config['PUBLIC_BASE_URL'] = 'https://poi.example.edu'
    monkeypatch.setattr(
        auth_module, 'normalize_email', lambda email, check_deliverability=True: email.lower()
    )
    captured = []

    def fake_send_email(*args, **kwargs):
        captured.append((args, kwargs))
        return True

    monkeypatch.setattr(auth_module, 'send_email', fake_send_email)

    response = client.post(
        '/signup',
        data={'email': 'hostpoison@example.com', 'name': 'Host User', 'password': 'Password123!'},
        headers={'Host': 'evil.example'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert captured
    message = captured[0][1].get('message') or captured[0][0][0]
    html_text = captured[0][1].get('html_text') or ''
    assert 'https://poi.example.edu/verify-email/' in message
    assert 'evil.example' not in message
    assert 'evil.example' not in html_text


def test_forgot_password_reset_link_uses_public_base_url(client, app, monkeypatch, user_factory):
    import poi_broker.auth as auth_module

    user_factory(email='reset-host@example.com', verified=True)
    app.config['PUBLIC_BASE_URL'] = 'https://poi.example.edu'
    captured = []

    def fake_send_email(*args, **kwargs):
        captured.append((args, kwargs))
        return True

    monkeypatch.setattr(auth_module, 'send_email', fake_send_email)

    response = client.post(
        '/forgot-password',
        data={'email': 'reset-host@example.com'},
        headers={'Host': 'evil.example'},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert captured
    message = captured[0][0][0]
    assert 'https://poi.example.edu/reset-password/' in message
    assert 'evil.example' not in message


def test_signup_preserves_leading_trailing_spaces_in_password(client, app, monkeypatch):
    """A password registered with outer spaces must log in with those spaces intact."""
    import poi_broker.auth as auth_module

    monkeypatch.setattr(
        auth_module, 'normalize_email', lambda email, check_deliverability=True: email.lower()
    )
    monkeypatch.setattr(auth_module, 'send_email', lambda *args, **kwargs: True)
    spaced_password = '  Password123!  '

    signup_response = client.post(
        '/signup',
        data={'email': 'spaces@example.com', 'name': 'Spaces User', 'password': spaced_password},
        follow_redirects=False,
    )
    assert signup_response.status_code == 302

    from werkzeug.security import check_password_hash

    from poi_broker.models import User

    with app.app_context():
        user = User.query.filter_by(email='spaces@example.com').first()
        assert user is not None
        assert check_password_hash(user.password, spaced_password)
        user.email_verified = True
        from poi_broker import db as users_db

        users_db.session.commit()

    # Raw value (spaces included) authenticates; the trimmed copy does not.
    success = client.post(
        '/login',
        data={'email': 'spaces@example.com', 'password': spaced_password},
        follow_redirects=False,
    )
    assert success.status_code == 302
    assert '/profile' in success.headers['Location']

    failure = client.post(
        '/login',
        data={'email': 'spaces@example.com', 'password': 'Password123!'},
        follow_redirects=False,
    )
    assert failure.status_code == 302
    assert 'forgot_password=True' in failure.headers['Location']


def test_signup_rejects_whitespace_only_password(client, app, monkeypatch):
    """Without the trim, eight spaces would pass the length check — they must not."""
    import poi_broker.auth as auth_module

    monkeypatch.setattr(
        auth_module, 'normalize_email', lambda email, check_deliverability=True: email.lower()
    )
    captured = []

    def fake_send_email(*args, **kwargs):
        captured.append((args, kwargs))
        return True

    monkeypatch.setattr(auth_module, 'send_email', fake_send_email)

    response = client.post(
        '/signup',
        data={'email': 'blanks@example.com', 'name': 'Blank User', 'password': ' ' * 8},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/signup')
    assert not captured

    from poi_broker.models import User

    with app.app_context():
        assert User.query.filter_by(email='blanks@example.com').first() is None


def test_boot_does_not_alter_legacy_users_db(tmp_path, monkeypatch):
    """create_app() must never write DDL: schema upgrades are manual SQL
    scripts only (tools/*.sql, docs/password_reset/deployment.md)."""
    import sqlite3

    from sqlalchemy import inspect

    users_db = tmp_path / 'users_legacy.db'
    alerts_db = tmp_path / 'alerts_legacy.db'
    conn = sqlite3.connect(users_db)
    conn.execute(
        """
        CREATE TABLE user (
            id INTEGER PRIMARY KEY,
            email VARCHAR(100),
            password VARCHAR(100),
            name VARCHAR(1000),
            role VARCHAR(20),
            email_verified INTEGER DEFAULT 0,
            email_verification_token VARCHAR(128),
            reset_token VARCHAR(128),
            reset_token_expires INTEGER
        )
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv('SECRET_KEY', 'legacy-schema-secret')
    monkeypatch.setenv('FLASK_TESTING', '1')
    monkeypatch.setenv('FLASK_DEBUG', '0')
    monkeypatch.setenv('ALERTS_DB_PATH', str(alerts_db))
    monkeypatch.setenv('USERS_DB_PATH', str(users_db))

    from poi_broker import create_app, db

    app = create_app()
    with app.app_context():
        columns = {col['name'] for col in inspect(db.engines['users']).get_columns('user')}
    assert 'email_verification_token_expires' not in columns
    assert 'password_changed_at' not in columns


def test_html_csrf_failure_ignores_external_referrer(secure_client):
    response = secure_client.post(
        "/login",
        data={"email": "nobody@example.com", "password": "wrong-password"},
        headers={"Referer": "https://evil.example/phish"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "evil.example" not in location
    assert location.endswith("/login")


def test_login_post_rate_limited_after_retries(secure_client):
    login_page = secure_client.get("/login")
    csrf_token = _extract_hidden_csrf_token(login_page.get_data(as_text=True))

    statuses = []
    for _ in range(4):
        r = secure_client.post(
            "/login",
            data={
                "email": "nobody@example.com",
                "password": "wrong-password",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        statuses.append(r.status_code)

    assert statuses[:3] == [302, 302, 302]
    assert statuses[3] == 429


def test_login_unknown_email_still_burns_one_hash(client, monkeypatch):
    """Unknown emails must run one password-hash comparison too, or response
    timing reveals whether an account exists (CWE-208)."""
    import poi_broker.auth as auth_module

    checked = []
    real_check = auth_module.check_password_hash

    def spy(hashed_password, password):
        checked.append(hashed_password)
        return real_check(hashed_password, password)

    monkeypatch.setattr(auth_module, "check_password_hash", spy)

    response = client.post(
        "/login",
        data={"email": "ghost@example.com", "password": "Whatever123!"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert checked == [auth_module.DUMMY_PASSWORD_HASH]


def test_authenticated_watchlist_crud(secure_app, secure_client):
    """Test authenticated watchlist CRUD operations with CSRF enabled."""
    from poi_broker import db
    from poi_broker.models import User

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "watchlist@example.com", "Password123!", "Watchlist User")

    _force_login(secure_client, user_id)
    html = secure_client.get("/").get_data(as_text=True)
    csrf_token = _extract_meta_csrf_token(html)

    # Match current UI contract: watchlists are saved from non-empty rules only.
    minimal_rules = {
        "condition": "AND",
        "rules": [
            {
                "field": "featuretable.alert_id",
                "operator": "is_not_null",
            }
        ],
    }

    # Create watchlist
    r = secure_client.post(
        "/api/watchlist",
        json={"name": "Security Watchlist", "rules": minimal_rules},
        headers={"X-CSRFToken": csrf_token},
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    wl_id = r.get_json()["id"]

    # List watchlists
    r = secure_client.get("/api/watchlist")
    assert r.status_code == 200
    names = [w["name"] for w in r.get_json()["watchlists"]]
    assert "Security Watchlist" in names

    # Delete watchlist
    r = secure_client.delete(
        f"/api/watchlist/{wl_id}",
        headers={"X-CSRFToken": csrf_token},
    )
    assert r.status_code == 200
    assert r.is_json
    assert r.get_json().get("status") == "ok"


def test_export_query_rate_limited_on_cheap_400_path(secure_app, secure_client):
    """LAX limit on /api/export-query via invalid payload (no COUNT)."""
    from poi_broker import db
    from poi_broker.models import User

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "export-limit@example.com", "Password123!", "Export Limit User")

    _force_login(secure_client, user_id)
    csrf_token = _extract_meta_csrf_token(secure_client.get("/").get_data(as_text=True))

    for i in range(31):
        response = secure_client.post(
            "/api/export-query",
            json={},
            headers={"X-CSRFToken": csrf_token},
        )
        if i < 30:
            assert response.status_code == 400
        else:
            assert response.status_code == 429


def test_query_classification_rate_limited_on_cheap_400_path(secure_app, secure_client):
    """LAX limit on /query_classification via missing alertId (no DB / Bokeh)."""
    for i in range(31):
        response = secure_client.get("/query_classification")
        if i < 30:
            assert response.status_code == 400
        else:
            assert response.status_code == 429


def test_export_submit_rate_limited_on_cheap_400_path(secure_app, secure_client):
    """LAX limit on POST /export via invalid payload (no COUNT / enqueue)."""
    from poi_broker import db
    from poi_broker.models import User

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "export-post-limit@example.com", "Password123!", "Export Post Limit User")

    _force_login(secure_client, user_id)
    csrf_token = _extract_meta_csrf_token(secure_client.get("/").get_data(as_text=True))

    for i in range(31):
        response = secure_client.post(
            "/export",
            json={},
            headers={"X-CSRFToken": csrf_token},
        )
        if i < 30:
            assert response.status_code == 400
        else:
            assert response.status_code == 429


def test_favorites_write_rate_limited_on_cheap_400_path(secure_app, secure_client):
    """MEDIUM limit on POST /api/favorite via invalid payload (no DB write)."""
    from poi_broker import db
    from poi_broker.models import User

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "fav-limit@example.com", "Password123!", "Fav Limit User")

    _force_login(secure_client, user_id)
    csrf_token = _extract_meta_csrf_token(secure_client.get("/").get_data(as_text=True))

    for i in range(16):
        response = secure_client.post(
            "/api/favorite",
            json={},
            headers={"X-CSRFToken": csrf_token},
        )
        if i < 15:
            assert response.status_code == 400
        else:
            assert response.status_code == 429


def test_password_reset_invalidates_existing_sessions(client, app, user_factory):
    """A session cookie stolen before a password reset must stop working."""
    from datetime import datetime, timezone

    from poi_broker import db
    from poi_broker.auth import hash_token
    from poi_broker.models import User

    user_factory(email="stolen-session@example.com")
    raw_token = "reset-stolen-session-token"
    with app.app_context():
        user = User.query.filter_by(email="stolen-session@example.com").first()
        user.reset_token = hash_token(raw_token)
        user.reset_token_expires = int(datetime.now(timezone.utc).timestamp()) + 3600
        db.session.commit()
        user_id = user.id

    _force_login(client, user_id, login_age_seconds=60)  # attacker's pre-change session

    response = client.post(
        f"/reset-password/{raw_token}",
        data={"password": "NewPassword123!", "password_confirm": "NewPassword123!"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    # The pre-reset session must no longer authenticate.
    resp = client.get("/security", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_watermark_second_belongs_to_old_credential(client, app, user_factory):
    """Second-granularity boundary of the session-invalidation watermark:
    a session stamped in the watermark second is evicted (a same-second
    steal dies), while a login proving the current credential in that
    second is stamped past the watermark and survives."""
    from poi_broker import db
    from poi_broker.models import User

    EMAIL = "watermark-second@example.com"
    user_factory(email=EMAIL)
    with app.app_context():
        user_id = User.query.filter_by(email=EMAIL).first().id

    _force_login(client, user_id, login_age_seconds=0)
    with client.session_transaction() as session:
        login_at = session["login_at"]

    # Simulate a password write stamped in the same second as the login.
    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        user.password_changed_at = login_at
        db.session.commit()

    resp = client.get("/security", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.location

    # A login with the current password in the watermark second authenticates.
    resp = client.post(
        "/login",
        data={"email": EMAIL, "password": "Password123!"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    resp = client.get("/security", follow_redirects=False)
    assert resp.status_code == 200


def test_change_password_signs_out_everywhere_including_current_browser(client, app, user_factory):
    """Password change ends every session — the acting browser included."""
    from poi_broker.models import User

    user_factory(email="multi-session@example.com")
    with app.app_context():
        user_id = User.query.filter_by(email="multi-session@example.com").first().id

    other_device = app.test_client()
    _force_login(other_device, user_id, login_age_seconds=60)  # session predating the change

    # The acting device performs a genuine login first.
    login_page = client.post(
        "/login",
        data={"email": "multi-session@example.com", "password": "Password123!"},
        follow_redirects=False,
    )
    assert login_page.status_code == 302

    response = client.post(
        "/change-password",
        data={
            "current_password": "Password123!",
            "new_password": "NewPassword123!",
            "new_password_confirm": "NewPassword123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" in response.location

    # Both devices are signed out; the changing one is redirected to login...
    for c in (client, other_device):
        resp = c.get("/security", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.location

    # ...and re-authentication succeeds everywhere with the new password.
    for c in (client, other_device):
        relogin = c.post(
            "/login",
            data={"email": "multi-session@example.com", "password": "NewPassword123!"},
            follow_redirects=False,
        )
        assert relogin.status_code == 302
        assert c.get("/security", follow_redirects=False).status_code == 200


def test_change_password_clears_remember_me_cookie(client, user_factory):
    """The remember-me cookie of the changing browser is expired on logout."""
    user_factory(email="remember-change@example.com")

    client.post(
        "/login",
        data={"email": "remember-change@example.com", "password": "Password123!", "remember": "on"},
        follow_redirects=False,
    )
    assert client.get_cookie("remember_token") is not None

    response = client.post(
        "/change-password",
        data={
            "current_password": "Password123!",
            "new_password": "NewPassword123!",
            "new_password_confirm": "NewPassword123!",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    cleared = [h for h in response.headers.getlist("Set-Cookie") if h.startswith("remember_token=")]
    assert cleared, "change-password did not expire the remember_token cookie"
    cookie_line = cleared[0]
    assert cookie_line.split(";", 1)[0] == "remember_token=", "cookie not emptied"
    assert "Expires=Thu, 01 Jan 1970" in cookie_line or "Max-Age=0" in cookie_line


def test_reset_rejects_remember_cookie_restore(client, app, user_factory):
    """A password reset performed on another device locks out a remembered
    browser: the remember cookie alone cannot restore a session against the
    new watermark (remember restores carry no login_at)."""
    from datetime import datetime, timezone

    from poi_broker import db
    from poi_broker.auth import hash_token
    from poi_broker.models import User

    EMAIL = "remember-reset@example.com"
    user_factory(email=EMAIL)

    client.post(
        "/login",
        data={"email": EMAIL, "password": "Password123!", "remember": "on"},
        follow_redirects=False,
    )
    assert client.get_cookie("remember_token") is not None
    assert client.get("/security", follow_redirects=False).status_code == 200

    raw_token = "reset-remember-restore-token"
    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        user.reset_token = hash_token(raw_token)
        user.reset_token_expires = int(datetime.now(timezone.utc).timestamp()) + 3600
        db.session.commit()

    other_device = app.test_client()
    response = other_device.post(
        f"/reset-password/{raw_token}",
        data={"password": "NewPassword123!", "password_confirm": "NewPassword123!"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    # Drop the now-stale login session so only the remember cookie remains,
    # then prove the restore is refused.
    client.delete_cookie("session")
    resp = client.get("/security", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_security_page_shows_last_password_change_and_signout_notice(client, app, user_factory):
    """Security page surfaces the rotation date (from the DB watermark) and
    warns that changing the password signs out every device."""
    from datetime import datetime, timezone

    from poi_broker.models import User

    EMAIL = "security-page@example.com"
    user_factory(email=EMAIL)

    client.post("/login", data={"email": EMAIL, "password": "Password123!"}, follow_redirects=False)

    # Never rotated -> no section yet.
    html = client.get("/security").get_data(as_text=True)
    assert "Last Password Change" not in html

    client.post(
        "/change-password",
        data={
            "current_password": "Password123!",
            "new_password": "NewPassword123!",
            "new_password_confirm": "NewPassword123!",
        },
        follow_redirects=False,
    )
    client.post("/login", data={"email": EMAIL, "password": "NewPassword123!"}, follow_redirects=False)

    with app.app_context():
        watermark = User.query.filter_by(email=EMAIL).first().password_changed_at
    expected_date = datetime.fromtimestamp(watermark, tz=timezone.utc).strftime("%Y-%m-%d")

    html = client.get("/security").get_data(as_text=True)
    assert "Last Password Change" in html
    assert expected_date in html
    assert "signs you out on all devices" in html


def test_reset_password_rejects_whitespace_only(client, app, user_factory):
    from poi_broker import db
    from poi_broker.auth import hash_token
    from poi_broker.models import User

    user_factory(email="ws-reset@example.com")
    raw_token = "reset-ws-only-token"
    with app.app_context():
        user = User.query.filter_by(email="ws-reset@example.com").first()
        original_hash = user.password
        user.reset_token = hash_token(raw_token)
        user.reset_token_expires = int(time.time()) + 3600
        db.session.commit()

    response = client.post(
        f"/reset-password/{raw_token}",
        data={"password": " " * 8, "password_confirm": " " * 8},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/reset-password/" in (response.headers.get("Location") or "")

    with app.app_context():
        user = User.query.filter_by(email="ws-reset@example.com").first()
        assert user.password == original_hash
        assert user.reset_token is not None


def test_change_password_rejects_whitespace_only(client, user_factory):
    user_factory(email="ws-change@example.com")
    client.post(
        "/login",
        data={"email": "ws-change@example.com", "password": "Password123!"},
        follow_redirects=False,
    )
    response = client.post(
        "/change-password",
        data={
            "current_password": "Password123!",
            "new_password": " " * 8,
            "new_password_confirm": " " * 8,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/security" in (response.headers.get("Location") or "")
    assert client.get("/security", follow_redirects=False).status_code == 200


def test_export_csrf_failure_returns_json(secure_app, secure_client):
    from poi_broker import db
    from poi_broker.models import User

    with secure_app.app_context():
        user_id = _create_verified_user(db, User, "export-csrf@example.com", "Password123!", "Export CSRF")

    _force_login(secure_client, user_id)
    response = secure_client.post(
        "/export",
        json={
            "condition": "AND",
            "rules": [{"field": "featuretable.alert_id", "operator": "is_not_null"}],
        },
    )
    assert response.status_code == 400
    assert response.is_json
    assert response.get_json().get("error") == "csrf validation failed"
