from typing import Any, Optional, Tuple
from sqlalchemy.orm import Query

from poi_broker.services.input_parser import InputParser, ParseResult
from poi_broker.services.filter_service import FloatFilter, IntFilter, MjdFilter


class SearchService:
    """
    High-level search orchestration service.
    Combines input parsing with filter application.
    """

    def __init__(self):
        self.parser = InputParser()
        self.float_filter = FloatFilter()
        self.int_filter = IntFilter()
        self.mjd_filter = MjdFilter()

    # --- Parsing-only methods (exposed for validation) ---
    def parse_float_input(self, text: str, allowed_range: Optional[Tuple[float, float]] = None) -> Optional[ParseResult]:
        return self.parser.parse_numbers(text, allowed_range=allowed_range)

    def parse_int_input(self, text: str, allowed_range: Optional[Tuple[float, float]] = None) -> ParseResult | None:
        return self.parser.parse_numbers(text, allowed_range)

    def parse_date_input(self, text: str) -> ParseResult:
        # Never returns None, returns empty ParseResult on failure
        return self.parser.parse_dates(text)

    def parse_mjd_input(self, text: str) -> ParseResult:
        return self.parser.parse_dates(text) # MJD uses same parser as dates
    
    # --- Filter methods accepting ParseResult ---
    def apply_float_filter(
        self, query: Query, db_field: Any, parsed: ParseResult, decimals: int = 0
    ) -> Query:
        if not parsed or not parsed.values:
            return query
        return self.float_filter(query, db_field, parsed.values, decimals=decimals)

    def apply_int_filter(self, query: Query, db_field: Any, parsed: ParseResult) -> Query:
        if not parsed or not parsed.values:
            return query
        return self.int_filter(query, db_field, parsed.values)

    def apply_mjd_filter(self, query: Query, db_field: Any, parsed: ParseResult) -> Query:
        if not parsed or not parsed.values:
            return query
        return self.mjd_filter(query, db_field, parsed.values, parsed.has_time_component)
