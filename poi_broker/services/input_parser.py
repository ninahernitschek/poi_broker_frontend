import re
from datetime import datetime
from typing import List, Optional, Tuple
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedValue:
    """A single parsed input value with its comparison operator."""
    raw: str
    operator: str  # '', '>', '<'
    value: str     # Clean value without operator

    @property
    def is_comparison(self) -> bool:
        return self.operator in ('>', '<')


@dataclass(frozen=True)
class ParseResult:
    """Result of parsing a filter input string."""
    values: List[ParsedValue]
    has_time_component: bool = False


class InputParser:
    """Parse and normalize filter input strings into structured values."""

    # Allowed characters: digits, decimal point, optional minus sign, spaces, >, <
    # Note: '+' is intentionally NOT allowed as a sign; only '-' is a supported
    # sign prefix. Allowing '+' in the whitelist but not in _NUMBER_PATTERN caused
    # malformed inputs like "+-10 20.5" to silently drop the '+' and parse as valid.
    _VALID_CHARS_PATTERN = re.compile(r"^[><\d. \-]+$")
    _NUMBER_PATTERN = re.compile(r"[<>]?[-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))")
    
    # Date patterns
    _YYYYMMDD_PATTERN = re.compile(r"[<>]?\d{8}")
    _ISO_DATE_PATTERN = re.compile(r"[<>]?\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?")
    _TIME_COMPONENT_PATTERN = re.compile(r"[ T]\d{2}:\d{2}:\d{2}")

    def parse_numbers(self, text: str, allowed_range: Optional[Tuple[float, float]] = None) -> Optional[ParseResult]:
        """Parse numeric filter input (int/float)."""
        if not self._VALID_CHARS_PATTERN.match(text):
            return None

        matches = self._NUMBER_PATTERN.findall(text)
        if not matches:
            return None

        values = self._normalize_matches(matches)
        if allowed_range and not self._validate_range(values, allowed_range):
            # Out-of-range input is rejected (returns None) so callers can rely on
            # None meaning "invalid input" and a ParseResult meaning "valid".
            return None

        return ParseResult(values=values)

    def parse_dates(self, text: str) -> ParseResult:
        """Parse date filter input (YYYYMMDD or ISO format)."""
        if not text or not text.strip():
            return ParseResult(values=[])

        # Try YYYYMMDD first. Require the whole string to be valid date tokens so
        # trailing garbage (e.g. "20230101abc") is rejected rather than silently ignored.
        matches = self._YYYYMMDD_PATTERN.findall(text)
        if matches and self._tokens_cover_text(matches, text):
            try:
                iso_dates = [
                    datetime.strptime(m.replace('>', '').replace('<', ''), "%Y%m%d").date().isoformat()
                    for m in matches
                ]
                # Re-attach operators
                values = [
                    ParsedValue(raw=m, operator=self._extract_operator(m), value=d)
                    for m, d in zip(matches, iso_dates)
                ]
                return ParseResult(values=values[:2], has_time_component=False)
            except ValueError:
                pass  # Fall through to ISO parsing

        # Try ISO format (with optional time)
        matches = self._ISO_DATE_PATTERN.findall(text)
        if not matches or not self._tokens_cover_text(matches, text):
            return ParseResult(values=[])

        values = []
        for m in matches[:2]:
            value = m.replace('>', '').replace('<', '')
            if not self._iso_value_is_valid(value):
                return ParseResult(values=[])
            values.append(ParsedValue(raw=m, operator=self._extract_operator(m), value=value))
        has_time = any(self._TIME_COMPONENT_PATTERN.search(m) for m in matches)
        return ParseResult(values=values, has_time_component=has_time)

    @staticmethod
    def _iso_value_is_valid(value: str) -> bool:
        """True when an ISO token is a real calendar date (and time, if present)."""
        try:
            if 'T' in value:
                datetime.strptime(value, '%Y-%m-%dT%H:%M:%S')
            elif ' ' in value:
                datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            else:
                datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            return False
        return True

    @staticmethod
    def _tokens_cover_text(matches: List[str], text: str) -> bool:
        """True when the matched tokens (ignoring spaces/operators/commas) account
        for the entire input string, so trailing/leading garbage is rejected."""
        normalized = text.replace(' ', '').replace('>', '').replace('<', '').replace(',', '')
        return "".join(m.replace('>', '').replace('<', '').replace(' ', '') for m in matches) == normalized

    def _normalize_matches(self, matches: List[str]) -> List[ParsedValue]:
        """Convert regex matches to ParsedValue objects, limiting to 2 values."""
        normalized = []
        for m in matches[:2]:
            op = self._extract_operator(m)
            val = m.replace('>', '').replace('<', '')
            normalized.append(ParsedValue(raw=m, operator=op, value=val))
        return normalized

    @staticmethod
    def _extract_operator(text: str) -> str:
        if text.startswith('>'):
            return '>'
        if text.startswith('<'):
            return '<'
        return ''

    def _validate_range(self, values: List[ParsedValue], allowed_range: Tuple[float, float]) -> bool:
        try:
            nums = [float(v.value) for v in values]
            return all(allowed_range[0] <= n <= allowed_range[1] for n in nums)
        except ValueError:
            return False