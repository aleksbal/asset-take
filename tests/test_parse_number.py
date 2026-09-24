"""Tests for parse_number across notations."""
import pytest

from portfolio_monitor import parse_number


@pytest.mark.parametrize("text,expected", [
    ("30.0", 30.0),          # plain decimal point
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
    assert parse_number("1,234") == pytest.approx(1.234)
    assert parse_number("1,234", decimal_sep=".") == pytest.approx(1234.0)
