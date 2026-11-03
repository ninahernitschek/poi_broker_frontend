import pytest
from poi_broker.services.input_parser import InputParser, ParseResult


class TestInputParser:
    @pytest.fixture
    def parser(self):
        return InputParser()

    # --- parse_numbers ---
    def test_parse_numbers_single(self, parser):
        result = parser.parse_numbers("123.45")
        assert isinstance(result, ParseResult)
        assert len(result.values) == 1
        assert result.values[0].value == "123.45"
        assert result.values[0].operator == ""

    def test_parse_numbers_with_operators(self, parser):
        result = parser.parse_numbers(">10 <20")
        assert len(result.values) == 2
        assert result.values[0].operator == ">"
        assert result.values[0].value == "10"
        assert result.values[1].operator == "<"
        assert result.values[1].value == "20"

    def test_parse_numbers_invalid_chars(self, parser):
        assert parser.parse_numbers("abc") is None
        assert parser.parse_numbers("1,2") is None  # comma not allowed

    def test_parse_numbers_range_validation(self, parser):
        result = parser.parse_numbers("5 15", allowed_range=(0, 10))
        assert result is None  # 15 out of range

    def test_parse_numbers_range_validation_single(self, parser):
        # Single out-of-range value is also rejected
        assert parser.parse_numbers("99", allowed_range=(0, 10)) is None

    def test_parse_numbers_negative(self, parser):
        result = parser.parse_numbers("-5.5")
        assert len(result.values) == 1
        assert result.values[0].value == "-5.5"

    def test_parse_numbers_empty(self, parser):
        assert parser.parse_numbers("") is None
        assert parser.parse_numbers("   ") is None

    # --- legacy extract_* assertions relocated from test_helpers.py ---

    def test_parse_numbers_returns_none_when_no_numeric_text(self, parser):
        assert parser.parse_numbers('no numbers here') is None

    def test_parse_numbers_preserves_single_value_with_comparator(self, parser):
        assert [v.raw for v in parser.parse_numbers('>42').values] == ['>42']
        assert [v.raw for v in parser.parse_numbers('<-1.5').values] == ['<-1.5']

    def test_parse_numbers_returns_two_values_without_comparators(self, parser):
        assert [v.value for v in parser.parse_numbers('>1.2 <3.4').values] == ['1.2', '3.4']
        assert [v.value for v in parser.parse_numbers('5 10').values] == ['5', '10']

    # --- parse_dates ---
    def test_parse_yyyymmdd(self, parser):
        result = parser.parse_dates("20230101")
        assert len(result.values) == 1
        assert result.values[0].value == "2023-01-01"
        assert result.has_time_component is False

    def test_parse_yyyymmdd_with_operator(self, parser):
        result = parser.parse_dates(">20230101")
        assert len(result.values) == 1
        assert result.values[0].operator == ">"
        assert result.values[0].value == "2023-01-01"

    def test_parse_yyyymmdd_trailing_garbage(self, parser):
        # Trailing non-date text must be rejected, not silently ignored
        assert parser.parse_dates("20230101abc").values == []

    def test_parse_iso_with_time(self, parser):
        result = parser.parse_dates("2023-01-01T12:30:45")
        assert len(result.values) == 1
        assert result.values[0].value == "2023-01-01T12:30:45"
        assert result.has_time_component is True

    def test_parse_iso_date_only(self, parser):
        result = parser.parse_dates("2023-01-01")
        assert result.has_time_component is False

    def test_parse_iso_trailing_garbage(self, parser):
        assert parser.parse_dates("2023-01-01xyz").values == []

    def test_parse_date_invalid(self, parser):
        assert parser.parse_dates("not-a-date").values == []

    def test_parse_date_empty(self, parser):
        assert parser.parse_dates("").values == []
        assert parser.parse_dates("   ").values == []

    def test_parse_date_range(self, parser):
        result = parser.parse_dates("2023-01-01 2023-12-31")
        assert len(result.values) == 2
        assert result.values[0].value == "2023-01-01"
        assert result.values[1].value == "2023-12-31"

    def test_parse_dates_parses_yyyymmdd_and_iso_formats(self, parser):
        assert [v.value for v in parser.parse_dates('20250115').values] == ['2025-01-15']
        assert [v.value for v in parser.parse_dates('2025-01-15T12:30:00').values] == ['2025-01-15T12:30:00']
        assert [v.raw for v in parser.parse_dates('<2025-01-15 00:00:00').values] == ['<2025-01-15 00:00:00']

    def test_parse_dates_rejects_invalid_month(self, parser):
        # Invalid month (13) is rejected rather than returned verbatim.
        assert parser.parse_dates('20251301').values == []
        assert parser.parse_dates('2026-13-40').values == []
        assert parser.parse_dates('2026-02-31').values == []
        assert parser.parse_dates('2023-01-01T99:99:99').values == []