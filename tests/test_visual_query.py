"""Tests for visual query API endpoints."""

def test_authenticated_visual_query(auth_client):
    """Test authenticated visual query preview, export, and page render."""
    # The UI requires at least one valid rule before preview/save.
    minimal_rules = {
        "condition": "AND",
        "rules": [
            {
                "field": "featuretable.alert_id",
                "operator": "is_not_null",
            }
        ],
    }

    # Preview query
    r = auth_client.post("/api/preview-query", json={"rules": minimal_rules})
    assert r.status_code == 200
    assert "sql" in r.get_json()

    # Export query (match count)
    r = auth_client.post("/api/export-query", json={"rules": minimal_rules})
    assert r.status_code == 200
    assert "count" in r.get_json()

    # Visual query page renders
    r = auth_client.get("/visual_query")
    assert r.status_code == 200


def test_authenticated_visual_query_rejects_malformed_between(auth_client):
    """Test that malformed 'between' operator is rejected."""
    malformed_rules = {
        "condition": "AND",
        "rules": [
            {
                "field": "featuretable.locus_ra",
                "operator": "between",
                "value": [118.0],  # Missing second value
            }
        ],
    }

    r = auth_client.post("/api/preview-query", json={"rules": malformed_rules})
    assert r.status_code == 400
    assert r.is_json
    assert "error" in r.get_json()


def test_authenticated_visual_query_rejects_malformed_in(auth_client):
    """Test that malformed 'in' operator is rejected."""
    malformed_rules = {
        "condition": "AND",
        "rules": [
            {
                "field": "featuretable.alert_id",
                "operator": "in",
                "value": "ztf_candidate:123",  # Should be array
            }
        ],
    }

    r = auth_client.post("/api/preview-query", json={"rules": malformed_rules})
    assert r.status_code == 400
    assert r.is_json
    assert "error" in r.get_json()


def test_authenticated_visual_query_rejects_malformed_not_in(auth_client):
    """Test that malformed 'not_in' operator is rejected."""
    malformed_rules = {
        "condition": "AND",
        "rules": [
            {
                "field": "featuretable.alert_id",
                "operator": "not_in",
                "value": "ztf_candidate:123",  # Should be array
            }
        ],
    }

    r = auth_client.post("/api/preview-query", json={"rules": malformed_rules})
    assert r.status_code == 400
    assert r.is_json
    assert "error" in r.get_json()


def test_authenticated_watchlist_crud(auth_client):
    """Test authenticated watchlist CRUD operations."""
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
    r = auth_client.post("/api/watchlist", json={"name": "Smoke Watchlist", "rules": minimal_rules})
    assert r.status_code == 201, r.get_data(as_text=True)
    wl_id = r.get_json()["id"]

    # List watchlists
    r = auth_client.get("/api/watchlist")
    assert r.status_code == 200
    names = [w["name"] for w in r.get_json()["watchlists"]]
    assert "Smoke Watchlist" in names

    # Delete watchlist
    r = auth_client.delete(f"/api/watchlist/{wl_id}")
    assert r.status_code == 200
    assert r.is_json
    assert r.get_json().get("status") == "ok"


def test_watchlist_rejects_non_string_name(auth_client):
    r = auth_client.post(
        "/api/watchlist",
        json={"name": 12345, "rules": {
            "condition": "AND",
            "rules": [{"field": "featuretable.alert_id", "operator": "is_not_null"}],
        }},
    )
    assert r.status_code == 400
    assert r.is_json
    assert "name" in r.get_json()["error"].lower()


def test_preview_query_rejects_non_column_field(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={
            "condition": "AND",
            "rules": [{"field": "featuretable.classification", "operator": "is_null"}],
        },
    )
    assert r.status_code == 400
    assert r.is_json
    assert "error" in r.get_json()


def test_preview_query_rejects_unknown_table(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={
            "condition": "AND",
            "rules": [{"field": "unknown.alert_id", "operator": "is_not_null"}],
        },
    )
    assert r.status_code == 400
    assert r.is_json


def test_preview_query_rejects_unsupported_operator(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={
            "condition": "AND",
            "rules": [{"field": "featuretable.alert_id", "operator": "totally_fake", "value": "x"}],
        },
    )
    assert r.status_code == 400
    assert r.is_json


def test_preview_query_rejects_non_dict_rule(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={"condition": "AND", "rules": ["not-a-rule"]},
    )
    assert r.status_code == 400
    assert r.is_json


def test_preview_query_rejects_non_string_field(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={
            "condition": "AND",
            "rules": [{"field": ["featuretable", "alert_id"], "operator": "is_not_null"}],
        },
    )
    assert r.status_code == 400
    assert r.is_json


def test_preview_query_rejects_non_string_condition(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={
            "condition": 1,
            "rules": [{"field": "featuretable.alert_id", "operator": "is_not_null"}],
        },
    )
    assert r.status_code == 400
    assert r.is_json


def test_preview_query_rejects_dict_equal_value(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={
            "condition": "AND",
            "rules": [
                {
                    "field": "featuretable.alert_id",
                    "operator": "equal",
                    "value": {"a": 1},
                }
            ],
        },
    )
    assert r.status_code == 400
    assert r.is_json


def test_export_query_rejects_dict_in_elements(auth_client):
    r = auth_client.post(
        "/api/export-query",
        json={
            "condition": "AND",
            "rules": [
                {
                    "field": "featuretable.alert_id",
                    "operator": "in",
                    "value": [{}],
                }
            ],
        },
    )
    assert r.status_code == 400
    assert r.is_json


def test_preview_query_accepts_in_with_array(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={
            "condition": "AND",
            "rules": [
                {
                    "field": "featuretable.alert_id",
                    "operator": "in",
                    "value": ["a", "b"],
                }
            ],
        },
    )
    assert r.status_code == 200
    assert r.is_json
    assert "sql" in r.get_json()


def test_preview_query_accepts_equal_with_single_element_array(auth_client):
    r = auth_client.post(
        "/api/preview-query",
        json={
            "condition": "AND",
            "rules": [
                {
                    "field": "featuretable.ant_passband",
                    "operator": "equal",
                    "value": ["g"],
                }
            ],
        },
    )
    assert r.status_code == 200
    assert r.is_json
    assert "sql" in r.get_json()


def test_save_watchlist_rejects_invalid_rules_with_400(auth_client):
    r = auth_client.post(
        "/api/watchlist",
        json={
            "name": "Bad Rules",
            "rules": {
                "condition": "AND",
                "rules": [
                    {
                        "field": "featuretable.alert_id",
                        "operator": "equal",
                        "value": ["a", "b"],
                    }
                ],
            },
        },
    )
    assert r.status_code == 400
    assert r.is_json
