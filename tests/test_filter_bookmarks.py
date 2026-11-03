"""Tests for main-table filter bookmark API."""

import json

from werkzeug.security import generate_password_hash


def test_filter_bookmarks_unauthenticated(client):
    r = client.get("/api/filter-bookmarks")
    assert r.status_code == 401
    assert r.is_json
    assert r.get_json().get("error") == "authentication required"

    r = client.post("/api/filter-bookmarks", json={"name": "x", "params": {}})
    assert r.status_code == 401

    r = client.delete("/api/filter-bookmarks/1")
    assert r.status_code == 401


def test_filter_bookmarks_crud(auth_client):
    r = auth_client.post(
        "/api/filter-bookmarks",
        json={
            "name": "My locus",
            "params": {"locus_id": "ANT123", "sort__locus_ra": "asc"},
        },
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert "id" in body
    assert body["name"] == "My locus"
    assert body["params"]["locus_id"] == "ANT123"
    assert "locus_id=ANT123" in body["path"]
    assert "sort__locus_ra=asc" in body["path"]
    bookmark_id = body["id"]

    r = auth_client.get("/api/filter-bookmarks")
    assert r.status_code == 200
    items = r.get_json()["filterBookmarks"]
    assert isinstance(items, list)
    assert len(items) >= 1
    match = next((x for x in items if x["id"] == bookmark_id), None)
    assert match is not None
    assert match["name"] == "My locus"

    r = auth_client.delete(f"/api/filter-bookmarks/{bookmark_id}")
    assert r.status_code == 200
    assert r.is_json
    assert r.get_json().get("status") == "ok"

    r = auth_client.get("/api/filter-bookmarks")
    assert r.status_code == 200
    ids = [x["id"] for x in r.get_json()["filterBookmarks"]]
    assert bookmark_id not in ids


def test_filter_bookmarks_validation(auth_client):
    r = auth_client.post("/api/filter-bookmarks", json={"name": "", "params": {}})
    assert r.status_code == 400
    assert "error" in r.get_json()

    r = auth_client.post("/api/filter-bookmarks", json={"params": {"locus_id": "x"}})
    assert r.status_code == 400

    r = auth_client.post(
        "/api/filter-bookmarks",
        json={"name": "bad", "params": {"not_a_real_param": "1"}},
    )
    assert r.status_code == 400
    assert "unknown" in r.get_json().get("error", "").lower()

    r = auth_client.post(
        "/api/filter-bookmarks",
        json={"name": "legacy sort", "params": {"sort__date_alert_mjd": "desc"}},
    )
    assert r.status_code == 400
    assert "unknown" in r.get_json().get("error", "").lower()


def test_filter_bookmarks_allows_sort_locus_id(auth_client):
    r = auth_client.post(
        "/api/filter-bookmarks",
        json={"name": "Locus sort", "params": {"sort__locus_id": "asc"}},
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body["params"]["sort__locus_id"] == "asc"
    assert "sort__locus_id=asc" in body["path"]


def test_filter_bookmarks_aliases_legacy_date_alert_mjd_sort(auth_client, app):
    from poi_broker import db
    from poi_broker.models import FilterBookmark, User

    with app.app_context():
        user = User.query.filter_by(email="smoketest@example.com").first()
        assert user is not None
        row = FilterBookmark(
            user_id=user.id,
            name="Legacy MJD sort",
            query_json=json.dumps({"sort__date_alert_mjd": "desc", "locus_id": "ANT99"}),
        )
        db.session.add(row)
        db.session.commit()
        bookmark_id = row.id

    r = auth_client.get("/api/filter-bookmarks")
    assert r.status_code == 200
    match = next((x for x in r.get_json()["filterBookmarks"] if x["id"] == bookmark_id), None)
    assert match is not None
    assert "sort__date_alert_mjd" not in match["params"]
    assert match["params"]["sort__date"] == "desc"
    assert "sort__date=desc" in match["path"]
    assert match["params"]["locus_id"] == "ANT99"


def test_filter_bookmarks_omitted_params_empty(auth_client):
    r = auth_client.post("/api/filter-bookmarks", json={"name": "Empty filters"})
    assert r.status_code == 201
    assert r.get_json()["path"] == "/"


def test_filter_bookmarks_duplicate_name_returns_409(auth_client):
    """O5: UniqueConstraint(user_id, name) maps to 409."""
    payload = {"name": "Same name", "params": {"locus_id": "ANT1"}}
    r1 = auth_client.post("/api/filter-bookmarks", json=payload)
    assert r1.status_code == 201

    r2 = auth_client.post("/api/filter-bookmarks", json=payload)
    assert r2.status_code == 409
    assert "already exists" in r2.get_json().get("error", "").lower()


def test_filter_bookmarks_cross_user_delete(app, auth_client):
    from poi_broker import db
    from poi_broker.models import FilterBookmark, User

    with app.app_context():
        other = User(
            email="other_bm@example.com",
            password=generate_password_hash("OtherUser123!"),
            name="Other",
            email_verified=True,
        )
        db.session.add(other)
        db.session.commit()

        row = FilterBookmark(
            user_id=other.id,
            name="Theirs",
            query_json=json.dumps({"locus_id": "LOCUS_X"}),
        )
        db.session.add(row)
        db.session.commit()
        foreign_id = row.id

    r = auth_client.delete(f"/api/filter-bookmarks/{foreign_id}")
    assert r.status_code == 404

    with app.app_context():
        still = db.session.get(FilterBookmark, foreign_id)
        assert still is not None


def test_filter_bookmarks_reject_non_string_name(auth_client):
    r = auth_client.post("/api/filter-bookmarks", json={"name": 12345, "params": {}})
    assert r.status_code == 400
    assert r.is_json
    assert "name" in r.get_json()["error"].lower()


def test_bookmark_allowed_keys_derived_from_catalog_query():
    """auditSBA F6: ALLOWED_KEYS must track the authoritative catalog key
    sets instead of a hand-maintained copy, and must never contain
    pagination keys."""
    from poi_broker.routes.filter_bookmarks import ALLOWED_KEYS
    from poi_broker.services.catalog_query import FILTER_KEYS, PAGINATION_KEYS, SORT_KEYS

    assert ALLOWED_KEYS == frozenset(FILTER_KEYS) | {f'sort__{key}' for key in SORT_KEYS}
    assert not ALLOWED_KEYS & PAGINATION_KEYS


def test_bookmark_save_js_receives_pagination_keys(auth_client):
    """The bookmark-save JS strips the pagination keys injected into the
    page. Saving from a keyset-paginated URL used to 400 with
    'unknown parameter: before_mjd' because the client only stripped
    page/survey/options (auditSBA F6)."""
    from poi_broker.services.catalog_query import PAGINATION_KEYS

    html = auth_client.get("/").get_data(as_text=True)
    for key in sorted(PAGINATION_KEYS):
        assert f'"{key}"' in html
