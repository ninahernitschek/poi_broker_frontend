"""
Consolidated tests for the MJD (date -> Modified Julian Date) filter pipeline.

This covers the `date` query parameter path in app.py (lines 145-150):

    date_text -> SearchService.parse_mjd_input -> InputParser.parse_dates
               -> SearchService.apply_mjd_filter -> MjdFilter (via to_mjd)

The pipeline accepts two input shapes:
  * ISO dates:        "2023-10-05", "2023-10-05 14:30:00", "2023-10-05T14:30:00"
  * 8-digit integers: "20231005"  (parsed as %Y%m%d -> "2023-10-05")

Both shapes support ranges ("a b") and comparison operators (">a", "<a").

These tests exercise the real SQLAlchemy columns on a seeded Ztf model (via the
`app` fixture from conftest.py) so we verify the *actual* SQL produced, not mocks.

Coverage:
  - to_mjd: astropy iso/isot separator handling.
  - InputParser.parse_dates (via SearchService.parse_mjd_input): ISO date-only,
    ISO with time (space and T separators), 8-digit, ranges, operators, and the
    invalid/empty cases that must yield an empty ParseResult (no filter applied).
  - MjdFilter: date-only branch (full-day offset) and the time-component branch
    (1-second offset), plus 8-digit, range, and > / < operator behaviour.
  - SearchService.apply_mjd_filter: end-to-end integration on seeded data,
    including the "no values -> query unchanged" contract.
  - Route smoke: GET /?date=... warning vs. no-warning behaviour (app.py L145-150).

NOTE on known parser limitations (documented, not bugs under test):
  InputParser._ISO_DATE_PATTERN rejects fractional seconds
  ("2023-10-05T14:30:00.123") and a trailing "Z" timezone suffix
  ("2023-10-05 14:30:00Z"); both fall through to an empty ParseResult.
"""
import pytest
from astropy.time import Time

from poi_broker import db
from poi_broker.models import Ztf
from poi_broker.services.filter_service import MjdFilter, to_mjd
from poi_broker.services.input_parser import ParsedValue
from poi_broker.services.search_service import SearchService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_ztf(app):
    """Seed three Ztf rows with known, spaced-out MJD values.

    Layout (MJD -> UTC date):
        Z1: 59945.0  (2023-01-01 00:00)
        Z2: 60110.5  (2023-06-15 12:00)
        Z3: 60308.0  (2023-12-31 00:00)
    """
    with app.app_context():
        db.session.add_all([
            Ztf(
                alert_id="ztf_candidate:1111111111111111111",
                date_alert_mjd=Time(59945.0, format="mjd").mjd,  # 2023-01-01
                locus_id="L1", ztf_object_id="Z1",
                locus_ra=10.0, locus_dec=20.0, ant_mag_corrected=12.0,
                num_alerts=1, num_mag_values=10,
            ),
            Ztf(
                alert_id="ztf_candidate:2222222222222222222",
                date_alert_mjd=Time(60110.5, format="mjd").mjd,  # 2023-06-15 12:00
                locus_id="L2", ztf_object_id="Z2",
                locus_ra=50.0, locus_dec=0.0, ant_mag_corrected=18.0,
                num_alerts=5, num_mag_values=50,
            ),
            Ztf(
                alert_id="ztf_candidate:3333333333333333333",
                date_alert_mjd=Time(60308.0, format="mjd").mjd,  # 2023-12-31
                locus_id="L3", ztf_object_id="Z3",
                locus_ra=180.0, locus_dec=-45.0, ant_mag_corrected=20.5,
                num_alerts=10, num_mag_values=100,
            ),
        ])
        db.session.commit()
        yield


def _pv(value: str, operator: str = "") -> ParsedValue:
    """Build a ParsedValue with the given operator and clean value."""
    return ParsedValue(raw=f"{operator}{value}", operator=operator, value=value)


def _locus_ids(query) -> list[str]:
    """Return locus_id values from a Ztf query, sorted for stable comparison."""
    return sorted(r.locus_id for r in query.all())


# ---------------------------------------------------------------------------
# to_mjd (astropy iso/isot separator handling)
# ---------------------------------------------------------------------------

class TestToMjd:
    def test_space_separator_with_time(self):
        # Space-separated ISO with time must parse and keep the time component.
        assert to_mjd("2026-05-27 09:30:40") == 61187.3962962963

    def test_t_separator_with_time(self):
        # T-separated ISO with time must parse identically to the space form.
        assert to_mjd("2026-05-27T09:30:40") == to_mjd("2026-05-27 09:30:40")

    def test_date_only(self):
        assert to_mjd("2023-01-01") == 59945.0


# ---------------------------------------------------------------------------
# InputParser.parse_dates (via SearchService.parse_mjd_input)
# ---------------------------------------------------------------------------

class TestMjdParser:
    def test_iso_date_only(self):
        parsed = SearchService().parse_mjd_input("2023-10-05")
        assert len(parsed.values) == 1
        assert parsed.has_time_component is False
        assert parsed.values[0].operator == ""
        assert parsed.values[0].value == "2023-10-05"

    def test_iso_with_space_time(self):
        parsed = SearchService().parse_mjd_input("2023-10-05 14:30:00")
        assert len(parsed.values) == 1
        assert parsed.has_time_component is True
        assert parsed.values[0].value == "2023-10-05 14:30:00"

    def test_iso_with_t_time(self):
        parsed = SearchService().parse_mjd_input("2023-10-05T14:30:00")
        assert len(parsed.values) == 1
        assert parsed.has_time_component is True
        assert parsed.values[0].value == "2023-10-05T14:30:00"

    def test_eight_digit_date(self):
        parsed = SearchService().parse_mjd_input("20231005")
        assert len(parsed.values) == 1
        assert parsed.has_time_component is False
        # 8-digit yyyymmdd is normalized to ISO date form.
        assert parsed.values[0].value == "2023-10-05"

    def test_eight_digit_range(self):
        parsed = SearchService().parse_mjd_input("20231005 20231007")
        assert len(parsed.values) == 2
        assert parsed.has_time_component is False
        assert [v.value for v in parsed.values] == ["2023-10-05", "2023-10-07"]

    def test_eight_digit_with_operator(self):
        parsed = SearchService().parse_mjd_input(">20231005")
        assert len(parsed.values) == 1
        assert parsed.values[0].operator == ">"
        assert parsed.values[0].value == "2023-10-05"

    def test_iso_range(self):
        parsed = SearchService().parse_mjd_input("2023-10-05 2023-10-07")
        assert len(parsed.values) == 2
        assert parsed.has_time_component is False
        assert [v.value for v in parsed.values] == ["2023-10-05", "2023-10-07"]

    def test_iso_with_operator(self):
        parsed = SearchService().parse_mjd_input(">2023-10-05")
        assert len(parsed.values) == 1
        assert parsed.values[0].operator == ">"
        assert parsed.values[0].value == "2023-10-05"

    def test_invalid_input_yields_empty(self):
        # Non-date text must produce an empty ParseResult so no filter is applied.
        parsed = SearchService().parse_mjd_input("invalid-date")
        assert parsed.values == []

    def test_empty_input_yields_empty(self):
        parsed = SearchService().parse_mjd_input("")
        assert parsed.values == []

    def test_fractional_seconds_rejected(self):
        # _ISO_DATE_PATTERN requires exactly HH:MM:SS (no fractional seconds),
        # and the trailing ".123" makes _tokens_cover_text fail -> empty result.
        parsed = SearchService().parse_mjd_input("2023-10-05T14:30:00.123")
        assert parsed.values == []

    def test_z_suffix_rejected(self):
        # A trailing "Z" timezone suffix is not accepted (everything is UTC);
        # _tokens_cover_text fails on the leftover "Z" -> empty result.
        parsed = SearchService().parse_mjd_input("2023-10-05 14:30:00Z")
        assert parsed.values == []


# ---------------------------------------------------------------------------
# MjdFilter (date-only branch + time-component branch)
# ---------------------------------------------------------------------------

class TestMjdFilter:
    def test_date_only_matches_full_day(self, app, seeded_ztf):
        """A bare date should match the row whose MJD falls on that day."""
        with app.app_context():
            mf = MjdFilter()
            q = db.session.query(Ztf)
            # 2023-06-15 -> MJD 60110; with has_time_component=False the
            # filter expands to the full day and should hit L2 (MJD 60110.5).
            result = mf(
                q, Ztf.date_alert_mjd,
                [_pv("2023-06-15")],
                has_time_component=False,
            )
            assert _locus_ids(result) == ["L2"]

    def test_date_only_excludes_other_days(self, app, seeded_ztf):
        with app.app_context():
            mf = MjdFilter()
            q = db.session.query(Ztf)
            # 2023-07-01 -> no row has MJD on that day.
            result = mf(
                q, Ztf.date_alert_mjd,
                [_pv("2023-07-01")],
                has_time_component=False,
            )
            assert _locus_ids(result) == []

    def test_time_component_matches_exact_time(self, app, seeded_ztf):
        """Time component uses a 1-second offset, so an exact time matches."""
        with app.app_context():
            mf = MjdFilter()
            q = db.session.query(Ztf)
            # 2023-06-15 12:00:00 -> MJD 60110.5 == L2 exactly; small offset keeps it.
            result = mf(
                q, Ztf.date_alert_mjd,
                [_pv("2023-06-15 12:00:00")],
                has_time_component=True,
            )
            assert _locus_ids(result) == ["L2"]

    def test_time_component_excludes_other_day(self, app, seeded_ztf):
        """The 1-second offset must NOT bleed into the next day (unlike date-only)."""
        with app.app_context():
            mf = MjdFilter()
            q = db.session.query(Ztf)
            # 2023-06-16 00:00:00 -> MJD 60111.0; with the small offset this
            # cannot reach L2 (MJD 60110.5).
            result = mf(
                q, Ztf.date_alert_mjd,
                [_pv("2023-06-16 00:00:00")],
                has_time_component=True,
            )
            assert _locus_ids(result) == []

    def test_eight_digit_date_only_branch(self, app, seeded_ztf):
        """8-digit input normalizes to an ISO date -> date-only branch."""
        with app.app_context():
            mf = MjdFilter()
            q = db.session.query(Ztf)
            # "20230615" -> "2023-06-15" -> date-only branch -> hits L2.
            result = mf(
                q, Ztf.date_alert_mjd,
                [_pv("2023-06-15")],
                has_time_component=False,
            )
            assert _locus_ids(result) == ["L2"]

    def test_range_covers_all_seeded(self, app, seeded_ztf):
        with app.app_context():
            mf = MjdFilter()
            q = db.session.query(Ztf)
            # 2023-01-01 .. 2023-12-31 (date-only) spans all three rows.
            result = mf(
                q, Ztf.date_alert_mjd,
                [_pv("2023-01-01"), _pv("2023-12-31")],
                has_time_component=False,
            )
            assert _locus_ids(result) == ["L1", "L2", "L3"]

    def test_greater_than_operator(self, app, seeded_ztf):
        with app.app_context():
            mf = MjdFilter()
            q = db.session.query(Ztf)
            # > 2023-06-15 (date-only) -> >= MJD 60110.0 -> L2 and L3.
            result = mf(
                q, Ztf.date_alert_mjd,
                [_pv("2023-06-15", operator=">")],
                has_time_component=False,
            )
            assert _locus_ids(result) == ["L2", "L3"]

    def test_less_than_operator(self, app, seeded_ztf):
        with app.app_context():
            mf = MjdFilter()
            q = db.session.query(Ztf)
            # < 2023-06-15 (date-only) -> <= MJD 60111.0 -> L1 and L2.
            result = mf(
                q, Ztf.date_alert_mjd,
                [_pv("2023-06-15", operator="<")],
                has_time_component=False,
            )
            assert _locus_ids(result) == ["L1", "L2"]


# ---------------------------------------------------------------------------
# SearchService.apply_mjd_filter (end-to-end integration)
# ---------------------------------------------------------------------------

class TestMjdService:
    def test_iso_date_end_to_end(self, app, seeded_ztf):
        with app.app_context():
            svc = SearchService()
            parsed = svc.parse_mjd_input("2023-06-15")
            q = db.session.query(Ztf)
            result = svc.apply_mjd_filter(q, Ztf.date_alert_mjd, parsed)
            assert _locus_ids(result) == ["L2"]

    def test_eight_digit_end_to_end(self, app, seeded_ztf):
        with app.app_context():
            svc = SearchService()
            parsed = svc.parse_mjd_input("20230615")
            q = db.session.query(Ztf)
            result = svc.apply_mjd_filter(q, Ztf.date_alert_mjd, parsed)
            assert _locus_ids(result) == ["L2"]

    def test_range_end_to_end(self, app, seeded_ztf):
        with app.app_context():
            svc = SearchService()
            parsed = svc.parse_mjd_input("2023-01-01 2023-12-31")
            q = db.session.query(Ztf)
            result = svc.apply_mjd_filter(q, Ztf.date_alert_mjd, parsed)
            assert _locus_ids(result) == ["L1", "L2", "L3"]

    def test_empty_parse_result_leaves_query_unchanged(self, app, seeded_ztf):
        """Invalid input yields no values -> apply_mjd_filter returns query as-is."""
        with app.app_context():
            svc = SearchService()
            parsed = svc.parse_mjd_input("invalid-date")
            assert parsed.values == []
            q = db.session.query(Ztf)
            result = svc.apply_mjd_filter(q, Ztf.date_alert_mjd, parsed)
            # No filter applied -> all three seeded rows returned.
            assert _locus_ids(result) == ["L1", "L2", "L3"]


# ---------------------------------------------------------------------------
# Route smoke (app.py L145-150)
# ---------------------------------------------------------------------------

class TestMjdRoute:
    def test_iso_date_no_warning(self, app):
        client = app.test_client()
        response = client.get("/?date=2023-06-15")
        assert b'<div class="alert alert-warning" role="alert">' not in response.data

    def test_eight_digit_no_warning(self, app):
        client = app.test_client()
        response = client.get("/?date=20230615")
        assert b'<div class="alert alert-warning" role="alert">' not in response.data

    def test_invalid_date_shows_warning(self, app):
        client = app.test_client()
        response = client.get("/?date=invalid-date")
        assert b'<div class="alert alert-warning" role="alert">' in response.data
        assert b'Date filter cannot be applied' in response.data

    def test_iso_date_with_invalid_month_shows_warning(self, app):
        client = app.test_client()
        response = client.get("/?date=2026-13-40")
        assert response.status_code == 200
        assert b'<div class="alert alert-warning" role="alert">' in response.data
        assert b'Date filter cannot be applied' in response.data

        count = client.get("/api/catalog-count?date=2026-13-40")
        assert count.status_code == 200
        assert "count" in count.get_json()

    def test_empty_date_no_warning(self, app):
        client = app.test_client()
        response = client.get("/?date=")
        assert b'<div class="alert alert-warning" role="alert">' not in response.data

    def test_time_component_iso_date_shows_warning(self, app):
        """ISO date with Z suffix is rejected — timezone not accepted."""
        client = app.test_client()
        response = client.get("/?date=2025-01-15T12:00:00Z")
        assert b'<div class="alert alert-warning" role="alert">' in response.data
        assert b'Date filter cannot be applied' in response.data

    def test_offset_iso_date_shows_warning(self, app):
        """ISO date with +00:00 offset is rejected — timezone not accepted."""
        client = app.test_client()
        response = client.get("/?date=2025-01-15T12:00:00+00:00")
        assert b'<div class="alert alert-warning" role="alert">' in response.data
        assert b'Date filter cannot be applied' in response.data

    def test_future_date_no_warning(self, app):
        """Future ISO date is accepted (0 results, but no warning)."""
        client = app.test_client()
        response = client.get("/?date=2049-01-01")
        assert b'<div class="alert alert-warning" role="alert">' not in response.data
