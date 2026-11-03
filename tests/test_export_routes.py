"""
HTTP/route tests for the bulk export feature (poi_broker/routes/export.py).

Covers the user-facing endpoints:
    GET  /export
    POST /export
    GET  /export/status/<task_id>
    GET  /export/download/<task_id>

The task logic itself (create_export_file) is tested in test_huey_tasks.py; here
we mock it so these tests focus on HTTP behaviour (auth, validation, ownership,
status/download flows).
"""

import pytest

VALID_RULES = {
    "condition": "AND",
    "rules": [
        {"field": "featuretable.alert_id", "operator": "is_not_null"}
    ],
}


@pytest.fixture()
def mock_export_task(monkeypatch):
    """Replace the enqueued background task with a no-op."""
    import poi_broker.routes.export as export_routes
    monkeypatch.setattr(export_routes, "create_export_file", lambda **kw: None)


def _create_task(app, user_id, status="SUCCESS", file_path=None):
    from poi_broker import db
    from poi_broker.models import ExportTask
    task = ExportTask(user_id=user_id, status=status, file_path=file_path)
    db.session.add(task)
    db.session.commit()
    task_id = task.id
    db.session.expunge(task)
    return task_id


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

def test_export_routes_require_login(client):
    """All export endpoints redirect unauthenticated users to login."""
    for method, path in [
        ("get", "/export"),
        ("post", "/export"),
        ("get", "/export/status/1"),
        ("get", "/export/download/1"),
    ]:
        response = getattr(client, method)(path)
        assert response.status_code in (301, 302), f"{method.upper()} {path}"


# --------------------------------------------------------------------------- #
# GET /export
# --------------------------------------------------------------------------- #

def test_export_page_renders_empty(auth_client):
    response = auth_client.get("/export")
    assert response.status_code == 200
    assert b"No previous exports found" in response.data


def test_export_page_shows_recent_task(auth_client, app):
    with app.app_context():
        from poi_broker.models import User
        user = User.query.filter_by(email="smoketest@example.com").first()
        _create_task(app, user.id, status="SUCCESS", file_path="/tmp/x.csv")

    response = auth_client.get("/export")
    assert response.status_code == 200
    assert b"Export Completed" in response.data


# --------------------------------------------------------------------------- #
# POST /export
# --------------------------------------------------------------------------- #

def test_export_submit_creates_task(auth_client, app, mock_export_task):
    response = auth_client.post("/export", json={"rules": VALID_RULES})
    assert response.status_code == 202, response.get_data(as_text=True)
    data = response.get_json()
    assert data["success"] is True
    assert isinstance(data["task_id"], int)

    with app.app_context():
        from datetime import datetime, timezone

        from poi_broker import db
        from poi_broker.models import ExportTask
        from poi_broker.services.filter_service import datetime_to_mjd
        task = db.session.get(ExportTask, data["task_id"])
        assert task is not None
        assert task.status == "PENDING"
        # Worker takes its cutoff from this row, so submit must persist it.
        assert task.snapshot_mjd is not None
        assert abs(task.snapshot_mjd - datetime_to_mjd(datetime.now(timezone.utc))) < 0.01


def test_export_submit_accepts_raw_query_builder_shape(auth_client, app, mock_export_task):
    """Frontend sends the raw query-builder object (not wrapped in {'rules': ...})."""
    response = auth_client.post("/export", json=VALID_RULES)
    assert response.status_code == 202, response.get_data(as_text=True)
    assert response.get_json()["success"] is True


def test_export_submit_missing_rules(auth_client):
    response = auth_client.post("/export", json={})
    assert response.status_code == 400
    assert response.get_json()["error"] == "No query parameters provided"


def test_export_submit_invalid_json(auth_client):
    response = auth_client.post("/export", json="not-a-dict")
    assert response.status_code == 400


def test_export_submit_duplicate_active_task(auth_client, app, mock_export_task):
    """A second export while one is PENDING/RUNNING returns 409."""
    with app.app_context():
        from poi_broker.models import User
        user = User.query.filter_by(email="smoketest@example.com").first()
        task_id = _create_task(app, user.id, status="RUNNING")

    response = auth_client.post("/export", json={"rules": VALID_RULES})
    assert response.status_code == 409
    assert "active export task" in response.get_json()["error"]

    with app.app_context():
        from poi_broker import db
        from poi_broker.models import ExportTask
        assert db.session.get(ExportTask, task_id).status == "RUNNING"


def test_export_submit_stale_active_task_is_failed_and_enqueued(
    auth_client, app, mock_export_task
):
    """Stale PENDING (past EXPORT_STALE window) is failed; POST then enqueues."""
    from datetime import datetime, timedelta, timezone

    with app.app_context():
        from poi_broker import db
        from poi_broker.models import ExportTask, User
        user = User.query.filter_by(email="smoketest@example.com").first()
        stale_id = _create_task(app, user.id, status="PENDING")
        stale = db.session.get(ExportTask, stale_id)
        stale.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.session.commit()

    response = auth_client.post("/export", json={"rules": VALID_RULES})
    assert response.status_code == 202, response.get_data(as_text=True)
    new_id = response.get_json()["task_id"]
    assert new_id != stale_id

    with app.app_context():
        from poi_broker import db
        from poi_broker.models import ExportTask
        assert db.session.get(ExportTask, stale_id).status == "FAILED"
        assert db.session.get(ExportTask, new_id).status == "PENDING"


def test_export_submit_does_not_fail_other_users_stale_task(
    auth_client, app, mock_export_task, user_factory
):
    """POST /export age-aware fail is scoped to the caller, not a global reset."""
    from datetime import datetime, timedelta, timezone

    with app.app_context():
        from poi_broker import db
        from poi_broker.models import ExportTask, User

        user_factory(email="other-export@example.com", name="Other Exporter")
        caller = User.query.filter_by(email="smoketest@example.com").first()
        other_id = User.query.filter_by(email="other-export@example.com").first().id
        old = datetime.now(timezone.utc) - timedelta(hours=1)

        caller_stale_id = _create_task(app, caller.id, status="PENDING")
        other_stale_id = _create_task(app, other_id, status="PENDING")
        for task_id in (caller_stale_id, other_stale_id):
            db.session.get(ExportTask, task_id).updated_at = old
        db.session.commit()

    response = auth_client.post("/export", json={"rules": VALID_RULES})
    assert response.status_code == 202, response.get_data(as_text=True)
    new_id = response.get_json()["task_id"]

    with app.app_context():
        from poi_broker import db
        from poi_broker.models import ExportTask
        assert db.session.get(ExportTask, caller_stale_id).status == "FAILED"
        assert db.session.get(ExportTask, other_stale_id).status == "PENDING"
        assert db.session.get(ExportTask, new_id).status == "PENDING"
        assert new_id != other_stale_id


def test_export_page_still_shows_stale_active_task(auth_client, app):
    """GET /export does not auto-fail a stale row; it still renders as queued."""
    from datetime import datetime, timedelta, timezone

    with app.app_context():
        from poi_broker import db
        from poi_broker.models import ExportTask, User
        user = User.query.filter_by(email="smoketest@example.com").first()
        stale_id = _create_task(app, user.id, status="PENDING")
        stale = db.session.get(ExportTask, stale_id)
        stale.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.session.commit()

    response = auth_client.get("/export")
    assert response.status_code == 200
    assert b"Export Queued" in response.data
    assert b"Export Failed" not in response.data

    with app.app_context():
        from poi_broker import db
        from poi_broker.models import ExportTask
        assert db.session.get(ExportTask, stale_id).status == "PENDING"


def test_export_submit_enqueue_failure_marks_task_failed(auth_client, app, monkeypatch):
    """Regression (#4): enqueue errors must fail the row, not leave it PENDING."""
    import poi_broker.routes.export as export_routes

    def _boom(**_kw):
        raise RuntimeError("queue full")

    monkeypatch.setattr(export_routes, "create_export_file", _boom)

    response = auth_client.post("/export", json={"rules": VALID_RULES})
    assert response.status_code == 500
    assert response.get_json()["error"] == "Failed to start export task"

    with app.app_context():
        from poi_broker.models import ExportTask, User
        user = User.query.filter_by(email="smoketest@example.com").first()
        task = (
            ExportTask.query.filter_by(user_id=user.id)
            .order_by(ExportTask.id.desc())
            .first()
        )
        assert task is not None
        assert task.status == "FAILED"
        assert task.error_message == "Failed to enqueue export task"


def test_export_submit_race_insert_returns_409(auth_client, app):
    """Concurrent check-then-insert: the DB partial unique index wins, 409.

    Regression for audit #1. Two simultaneous POST /export for the same user
    could both pass the pre-check and both INSERT. The partial unique index on
    active (PENDING/RUNNING) tasks makes the second INSERT raise IntegrityError,
    which the route maps to 409 instead of leaving a duplicate active task or
    returning 500.

    We verify both halves of the fix here at the DB layer (no fragile query
    monkeypatching):
      1. The partial unique index exists in the test database.
      2. A second active task for the same user cannot be inserted.
    """
    import sqlalchemy as sa

    from poi_broker import db
    from poi_broker.models import ExportTask, User

    with app.app_context():
        user = User.query.filter_by(email="smoketest@example.com").first()
        _create_task(app, user.id, status="RUNNING")

        # 1) The index must be present (created from the model's __table_args__).
        index_rows = db.session.execute(
            sa.text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='uix_export_task_one_active_per_user'"
            ),
            bind_arguments={"bind": db.engines["users"]},
        ).fetchall()
        assert index_rows, (
            "partial unique index uix_export_task_one_active_per_user missing "
            "from the test database"
        )

        # 2) The same user cannot have two active tasks.
        with pytest.raises(sa.exc.IntegrityError):
            dup = ExportTask(user_id=user.id, status="PENDING")
            db.session.add(dup)
            db.session.commit()
        db.session.rollback()

        # The user's original active task is still intact.
        active = ExportTask.query.filter(
            ExportTask.user_id == user.id,
            ExportTask.status.in_(["PENDING", "RUNNING"]),
        ).all()
        assert len(active) == 1
        assert active[0].status == "RUNNING"


def test_export_status_does_not_leak_file_path(auth_client, app):
    """Regression: status JSON must not include the server filesystem path."""
    from poi_broker.models import User
    with app.app_context():
        user = User.query.filter_by(email="smoketest@example.com").first()
        task_id = _create_task(app, user.id, status="SUCCESS", file_path="/tmp/secret.csv")

    response = auth_client.get(f"/export/status/{task_id}")
    assert response.status_code == 200
    data = response.get_json()
    assert "file_path" not in data
    assert "download_url" in data
    assert "/export/download/" in data["download_url"]


# --------------------------------------------------------------------------- #
# GET /export/status/<task_id>
# --------------------------------------------------------------------------- #

def test_export_status_ok(auth_client, app):
    with app.app_context():
        from poi_broker.models import User
        user = User.query.filter_by(email="smoketest@example.com").first()
        task_id = _create_task(app, user.id, status="SUCCESS", file_path="/tmp/x.csv")

    response = auth_client.get(f"/export/status/{task_id}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["task_id"] == task_id
    assert data["status"] == "SUCCESS"


def test_export_sentinel_snapshot_mjd_renders_without_date(auth_client, app):
    """Regression: export_task rows backfilled by the one-shot
    tools/apply_export_snapshot_mjd.sql script (applied in production, since
    removed) carry the "no cutoff" sentinel 1e9. MJD 1e9 is beyond datetime's
    year range, so converting it used to 500 the export page and status
    endpoint; both now render and simply omit the date."""
    from poi_broker import db
    from poi_broker.models import ExportTask, User

    with app.app_context():
        user = User.query.filter_by(email="smoketest@example.com").first()
        task = ExportTask(user_id=user.id, status="SUCCESS", snapshot_mjd=1e9)
        db.session.add(task)
        db.session.commit()
        task_id = task.id

    page = auth_client.get("/export")
    assert page.status_code == 200
    assert b"Data as of:" not in page.data

    status = auth_client.get(f"/export/status/{task_id}")
    assert status.status_code == 200
    data = status.get_json()
    assert data["snapshot_mjd"] == 1e9
    assert data["data_as_of"] is None


def test_export_status_not_found(auth_client):
    response = auth_client.get("/export/status/999999")
    assert response.status_code == 404


def test_export_status_cross_user_forbidden(auth_client, app):
    """Another user cannot read a task's status."""
    with app.app_context():
        from poi_broker.models import User
        other = User(email="other@example.com", password="x", name="Other", email_verified=True)
        from poi_broker import db
        db.session.add(other)
        db.session.commit()
        task_id = _create_task(app, other.id, status="SUCCESS")

    response = auth_client.get(f"/export/status/{task_id}")
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# GET /export/download/<task_id>
# --------------------------------------------------------------------------- #

def test_export_download_not_ready_redirects(auth_client, app):
    with app.app_context():
        from poi_broker.models import User
        user = User.query.filter_by(email="smoketest@example.com").first()
        task_id = _create_task(app, user.id, status="PENDING")

    response = auth_client.get(f"/export/download/{task_id}")
    assert response.status_code in (301, 302)


def test_export_download_flash_is_consumed_on_export_page(auth_client, app):
    """Failed download flashes must render on /export, not leak to later pages."""
    with app.app_context():
        from poi_broker.models import User
        user = User.query.filter_by(email="smoketest@example.com").first()
        task_id = _create_task(app, user.id, status="PENDING")

    response = auth_client.get(f"/export/download/{task_id}")
    assert response.status_code in (301, 302)
    assert response.headers["Location"].rstrip("/").endswith("/export")

    with auth_client.session_transaction() as session:
        flashes = session.get("_flashes", [])
    assert any("not ready for download" in msg for _cat, msg in flashes)

    export_page = auth_client.get("/export")
    assert export_page.status_code == 200
    assert b"not ready for download" in export_page.data
    assert b"alert-warning" in export_page.data

    with auth_client.session_transaction() as session:
        assert not session.get("_flashes")

    later = auth_client.get("/login")
    assert b"not ready for download" not in later.data


def test_export_download_missing_file_redirects(auth_client, app):
    with app.app_context():
        from poi_broker.models import User
        user = User.query.filter_by(email="smoketest@example.com").first()
        task_id = _create_task(app, user.id, status="SUCCESS", file_path="/nonexistent/x.csv")

    response = auth_client.get(f"/export/download/{task_id}")
    assert response.status_code in (301, 302)


def test_export_download_serves_file(auth_client, app, tmp_path):
    csv_file = tmp_path / "export.csv"
    csv_file.write_text("alert_id\nztf_1\n", encoding="utf-8")

    with app.app_context():
        from poi_broker.models import User
        user = User.query.filter_by(email="smoketest@example.com").first()
        task_id = _create_task(app, user.id, status="SUCCESS", file_path=str(csv_file))

    response = auth_client.get(f"/export/download/{task_id}")
    assert response.status_code == 200
    assert "text/csv" in response.content_type
    assert b"ztf_1" in response.data


def test_export_download_cross_user_forbidden(auth_client, app, tmp_path):
    csv_file = tmp_path / "export.csv"
    csv_file.write_text("alert_id\nztf_1\n", encoding="utf-8")

    with app.app_context():
        from poi_broker import db
        from poi_broker.models import User
        other = User(email="other2@example.com", password="x", name="Other", email_verified=True)
        db.session.add(other)
        db.session.commit()
        task_id = _create_task(app, other.id, status="SUCCESS", file_path=str(csv_file))

    response = auth_client.get(f"/export/download/{task_id}")
    assert response.status_code in (301, 302)
