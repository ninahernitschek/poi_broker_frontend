from datetime import datetime, timezone
from typing import Any, Callable, List, Protocol
from sqlalchemy.orm import Query
from astropy.time import Time
from . input_parser import ParsedValue


def to_mjd(date_str: str) -> float:
    """Convert an ISO date/datetime string to Modified Julian Date (MJD).

    astropy's ``iso`` format requires a space separator (``2026-05-27 09:30:40``)
    while ``isot`` requires a ``T`` separator (``2026-05-27T09:30:40``). Both are
    valid user input, so normalize the separator and pick the matching format to
    avoid the confusing "Input values did not match the format class" errors.
    """
    if "T" in date_str:
        return Time(date_str, format="isot", scale="utc").mjd
    return Time(date_str, format="iso", scale="utc").mjd


def datetime_to_mjd(dt: datetime) -> float:
    """Convert a datetime to Modified Julian Date (MJD).

    Naive datetimes are assumed to be UTC. Astropy interprets ``datetime``
    input as UTC when ``scale='utc'``.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return float(Time(dt, scale="utc").mjd)


def mjd_to_datetime(mjd: float) -> datetime:
    """Convert MJD back to a timezone-aware UTC datetime (inverse of
    :func:`datetime_to_mjd`)."""
    return Time(mjd, format="mjd", scale="utc").datetime.replace(
        tzinfo=timezone.utc
    )

class Convertible(Protocol):
    """Protocol for values that can be compared in SQLAlchemy filters."""
    def __ge__(self, other: Any) -> Any: ...
    def __le__(self, other: Any) -> Any: ...
    def __eq__(self, other: Any) -> Any: ...


class FilterService:
    """Apply filters to SQLAlchemy queries. Stateless and fully testable."""

    def apply_filter(
        self,
        query: Query,
        db_field: Any,
        parsed_values: List["ParsedValue"],
        convert_callback: Callable[[str], Convertible],
        upper_offset: float = 0.0,
        lower_offset: float = 0.0
    ) -> Query:
        """Apply filter based on parsed input values."""
        if len(parsed_values) == 1:
            return self._apply_single_value_filter(
                query, db_field, parsed_values[0], convert_callback,
                upper_offset, lower_offset
            )
        elif len(parsed_values) == 2:
            return self._apply_range_filter(
                query, db_field, parsed_values, convert_callback,
                upper_offset, lower_offset
            )
        return query

    def _apply_single_value_filter(
        self,
        query: Query,
        db_field: Any,
        pv: "ParsedValue",
        convert_callback: Callable[[str], Convertible],
        upper_offset: float,
        lower_offset: float
    ) -> Query:
        converted = convert_callback(pv.value)

        if pv.operator == '>':
            return query.filter(db_field >= converted - lower_offset)
        elif pv.operator == '<':
            return query.filter(db_field <= converted + upper_offset)
        else:
            # Exact match with precision handling
            lower_bound = converted - lower_offset
            upper_bound = converted + upper_offset
            
            if abs(lower_bound - upper_bound) < 1e-12:
                return query.filter(db_field == lower_bound)
            else:
                return query.filter(db_field >= lower_bound).filter(db_field <= upper_bound)

    def _apply_range_filter(
        self,
        query: Query,
        db_field: Any,
        parsed_values: List["ParsedValue"],
        convert_callback: Callable[[str], Convertible],
        upper_offset: float,
        lower_offset: float
    ) -> Query:
        # Convert and sort - operators are ignored for range (order determined by value)
        converted = sorted(convert_callback(pv.value) for pv in parsed_values)
        lower_bound = converted[0] - lower_offset
        upper_bound = converted[1] + upper_offset
        return query.filter(db_field >= lower_bound).filter(db_field <= upper_bound)


# Convenience methods for common filter types
class FloatFilter:
    def __init__(self):
        self.service = FilterService()

    def __call__(
        self,
        query: Query,
        db_field: Any,
        parsed_values: List["ParsedValue"],
        decimals: int = 0
    ) -> Query:
        float_func = lambda x: float(x)
        offset = 10 ** (-decimals) if decimals > 0 else 0.0
        return self.service.apply_filter(
            query, db_field, parsed_values, float_func,
            upper_offset=offset / 2, lower_offset=offset / 2
        )


class IntFilter:
    def __init__(self):
        self.service = FilterService()

    def __call__(self, query: Query, db_field: Any, parsed_values: List["ParsedValue"]) -> Query:
        int_func = lambda x: int(x)
        return self.service.apply_filter(query, db_field, parsed_values, int_func)


class MjdFilter:
    def __init__(self):
        self.service = FilterService()

    def __call__(
        self,
        query: Query,
        db_field: Any,
        parsed_values: List["ParsedValue"],
        has_time_component: bool
    ) -> Query:
        mjd_func = to_mjd

        if has_time_component:
            # Small offset for rounding errors in MJD conversion
            offset_mjd = 1.0 / (3600 * 24)  # 1 second in days
        else:
            # Large offset to cover entire day when time component missing
            offset_mjd = 1.0 + 1.0 / (3600 * 24)  # 1 day + 1 second
            
        return self.service.apply_filter(
            query, db_field, parsed_values, mjd_func,
            upper_offset=offset_mjd
        )
