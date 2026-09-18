"""Number parsing across notations.

Regression: the original stripped every dot as a thousands separator, so a
plain '30.0' was read as 300 — every quantity inflated tenfold, with a total
that still looked plausible.
"""
import pytest

from portfolio_monitor import parse_number


@pytest.mark.parametrize("text,expected", [
    ("30.0", 30.0),          # the regression
    ("30", 30.0),
    ("205.9263", 205.9263),
    ("205,9263", 205.9263),  # German decimal comma
    ("1.234,56", 1234.56),   # German thousands + decimal
    ("1,234.56", 1234.56),   # English thousands + decimal
    ("1.234.567", 1234567.0),  # repeated dots can only be thousands
    ("  42  ", 42.0),
])
def test_parses(text, expected):
    assert parse_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", None, "   "])
def test_empty_is_none(text):
    assert parse_number(text) is None


def test_quantity_is_not_inflated_by_a_decimal_point():
    """The specific failure: 30.0 shares must never become 300."""
    assert parse_number("30.0") < 31


@pytest.mark.parametrize("text,sep,expected", [
    ("1,234", ".", 1234.0),      # English thousands — the 1000x bug
    ("1,234.56", ".", 1234.56),
    ("1.234", ",", 1234.0),      # German thousands
    ("1.234,56", ",", 1234.56),
    ("245,8172", ",", 245.8172),
    ("30.0", ".", 30.0),
])
def test_an_explicit_separator_overrides_the_heuristic(text, sep, expected):
    assert parse_number(text, decimal_sep=sep) == pytest.approx(expected)


def test_the_heuristic_alone_cannot_resolve_a_lone_comma():
    """Why callers that know their locale must say so: '1,234' is genuinely
    ambiguous, and the heuristic reads it as a decimal."""
    assert parse_number("1,234") == pytest.approx(1.234)
    assert parse_number("1,234", decimal_sep=".") == pytest.approx(1234.0)
