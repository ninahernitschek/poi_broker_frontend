import pytest

from poi_broker.models import Ztf

"""
Set isolated temp SQLite paths for both alerts and users DBs via env vars
Set testing mode and disable CSRF for test client runs
Create/drop DB tables around each test app lifecycle
"""


@pytest.fixture(scope='session', autouse=True)
def _offline_earth_rotation_data():
    """Use Astropy's bundled IERS table; tests must not wait on network data."""
    from astropy.utils import iers

    with iers.conf.set_temp('auto_download', False):
        yield


@pytest.fixture(autouse=True)
def _reset_worker_app():
    """Clear the Huey process-wide Flask app between tests (tmp_path DBs)."""
    import poi_broker.tasks as tasks_mod

    tasks_mod._worker_app = None
    yield
    tasks_mod._worker_app = None


@pytest.fixture(autouse=True)
def _reset_timezone_finder_cache():
    """Drop the shared TimezoneFinder singleton so tests never see a stale one."""
    from poi_broker.observing_tool import _get_timezone_finder

    _get_timezone_finder.cache_clear()
    yield
    _get_timezone_finder.cache_clear()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    alerts_db = tmp_path / "alerts_test.db"
    users_db = tmp_path / "users_test.db"

    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FLASK_TESTING", "1")
    monkeypatch.setenv("FLASK_DEBUG", "0")
    monkeypatch.setenv("ALERTS_DB_PATH", str(alerts_db))
    monkeypatch.setenv("USERS_DB_PATH", str(users_db))
    monkeypatch.setenv("HUEY_BACKEND", "memory")
    monkeypatch.setenv("HUEY_IMMEDIATE", "true")

    from poi_broker import create_app, db

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )

    with app.app_context():
        db.create_all()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth_client(app):
    """Test client pre-logged-in as a verified user."""
    from werkzeug.security import generate_password_hash

    from poi_broker import db
    from poi_broker.models import User

    TEST_EMAIL = "smoketest@example.com"
    TEST_PASSWORD = "SmokeTest123!"

    with app.app_context():
        user = User(
            email=TEST_EMAIL,
            password=generate_password_hash(TEST_PASSWORD),
            name="Smoke Test User",
            email_verified=True,
        )
        db.session.add(user)
        db.session.commit()

    client = app.test_client()
    client.post(
        "/login",
        data={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        follow_redirects=False,
    )
    return client


@pytest.fixture()
def user_factory(app):
    from werkzeug.security import generate_password_hash

    from poi_broker import db
    from poi_broker.models import User

    def factory(
        email: str = "user@example.com",
        password: str = "Password123!",
        name: str = "Test User",
        verified: bool = True,
    ):
        with app.app_context():
            user = User(
                email=email,
                password=generate_password_hash(password),
                name=name,
                email_verified=verified,
            )
            db.session.add(user)
            db.session.commit()
            return user

    return factory


@pytest.fixture()
def secure_app(tmp_path, monkeypatch):
    """App fixture with CSRF and auth rate limiting enabled for security tests."""
    alerts_db = tmp_path / "alerts_secure_test.db"
    users_db = tmp_path / "users_secure_test.db"

    monkeypatch.setenv("SECRET_KEY", "secure-test-secret-key")
    monkeypatch.setenv("FLASK_TESTING", "1")
    monkeypatch.setenv("FLASK_DEBUG", "0")
    monkeypatch.setenv("ALERTS_DB_PATH", str(alerts_db))
    monkeypatch.setenv("USERS_DB_PATH", str(users_db))
    monkeypatch.setenv("RATELIMIT_ENABLED", "1")
    monkeypatch.setenv("RATELIMIT_STORAGE_URI", "memory://")
    monkeypatch.setenv("AUTH_RATE_LIMIT_LOGIN", "3 per minute")
    monkeypatch.setenv("AUTH_RATE_LIMIT_SIGNUP", "2 per minute")
    monkeypatch.setenv("AUTH_RATE_LIMIT_FORGOT_PASSWORD", "2 per minute")
    monkeypatch.setenv("READ_RATE_LIMIT_LAX", "30 per minute")
    monkeypatch.setenv("READ_RATE_LIMIT_MEDIUM", "15 per minute")

    from poi_broker import create_app, db

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
    )

    with app.app_context():
        db.create_all()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def secure_client(secure_app):
    return secure_app.test_client()


@pytest.fixture
def mock_my_model():
    my_model = Ztf(
        alert_id="Test POI",
        date_alert_mjd=60255.1143980999,
        locus_id = 'Ztf-2025-0001'
    )
    return my_model

@pytest.fixture
def mock_get_sqlalchemy(mocker, app):
    # Mock the query property at the model level
    mock_query = mocker.Mock()
    # Use app context to properly mock the query property
    with app.app_context():
        mocker.patch.object(Ztf, 'query', mock_query)
    return mock_query
