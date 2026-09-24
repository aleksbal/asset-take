"""Tests for the parameter registry in market/indicators.py."""
from datetime import date, timedelta

from market import indicators as ix


def series(values, start=date(2026, 1, 1)):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def flat(n, value=100.0, start=date(2026, 1, 1)):
    return series([value] * n, start)


class TestVolumeIsOptional:
    def test_volume_trend_is_absent_without_a_volume_series(self):
        assert "volume_trend" not in ix.values(flat(250))

    def test_volume_trend_appears_once_a_volume_series_is_supplied(self):
        volumes = series([1_000_000.0] * 19 + [2_000_000.0])
        v = ix.values(flat(250), volumes=volumes)
        assert v["volume_trend"] is not None

    def test_a_short_volume_series_is_absent_not_approximated(self):
        volumes = series([1_000_000.0] * 5)
        v = ix.values(flat(250), volumes=volumes)
        assert v["volume_trend"] is None

    def test_price_parameters_are_unaffected_by_volume_being_present(self):
        volumes = series([1_000_000.0] * 20)
        with_vol = ix.values(flat(250), volumes=volumes)
        without_vol = ix.values(flat(250))
        assert with_vol["rsi"] == without_vol["rsi"]
        assert with_vol["drawdown"] == without_vol["drawdown"]

    def test_volume_trend_computes_with_no_price_series_at_all(self):
        volumes = series([1_000_000.0] * 19 + [2_000_000.0])
        v = ix.values([], volumes=volumes)
        assert v["volume_trend"] is not None
        assert "drawdown" not in v      # needs closes, which this row has none of


class TestVolatilityNeedsOnlyCloses:
    def test_present_for_any_priced_instrument_held_or_not(self):
        assert ix.values(flat(21))["volatility"] is not None

    def test_absent_below_its_window(self):
        assert ix.values(flat(20))["volatility"] is None


class TestDeclared:
    def test_the_new_parameters_are_declared(self):
        keys = {d["key"] for d in ix.declared()}
        assert {"volatility", "volume_trend"} <= keys

    def test_each_declared_entry_carries_a_reader_sentence(self):
        for d in ix.declared():
            assert d["means"]
