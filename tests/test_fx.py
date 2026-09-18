"""Exchange rates from the provider, current and historical.

Two copies of this existed and disagreed: the valuation one fell back to a
rate of 1.0 for a pair it could not fetch, which values a foreign holding as
though it were domestic.
"""
from datetime import date

import pandas as pd
import pytest

import fx


@pytest.fixture(autouse=True)
def clear_caches():
    fx._SPOT.clear()
    fx._SERIES.clear()


class Pair:
    """A provider that knows one direction of one pair."""

    def __init__(self, known, price, closes=None):
        self.known, self.price, self.closes = known, price, closes

    def __call__(self, symbol):
        self.symbol = symbol
        return self

    @property
    def fast_info(self):
        if self.symbol != self.known:
            raise KeyError(self.symbol)
        return {"last_price": self.price}

    def history(self, **kw):
        if self.symbol != self.known or self.closes is None:
            return pd.DataFrame()
        idx = pd.to_datetime(list(self.closes)).tz_localize("UTC")
        return pd.DataFrame({"Close": list(self.closes.values())}, index=idx)


class TestSpot:
    def test_the_same_currency_needs_no_rate(self):
        assert fx.rate("EUR", "EUR") == 1.0

    def test_a_direct_pair_is_used(self, monkeypatch):
        monkeypatch.setattr(fx.yf, "Ticker", Pair("USDEUR=X", 0.87))
        assert fx.rate("USD", "EUR") == pytest.approx(0.87)

    def test_the_reverse_pair_is_inverted(self, monkeypatch):
        """The provider names a pair one way round; both are tried."""
        monkeypatch.setattr(fx.yf, "Ticker", Pair("EURUSD=X", 1.25))
        assert fx.rate("USD", "EUR") == pytest.approx(0.8)

    def test_an_unavailable_pair_is_none_not_one(self, monkeypatch):
        """1.0 is a rate. None is an answer the caller can act on."""
        monkeypatch.setattr(fx.yf, "Ticker", Pair("NOPE=X", 1.0))
        assert fx.rate("XXX", "EUR") is None

    def test_a_missing_currency_is_none(self):
        assert fx.rate(None, "EUR") is None


class TestHistorical:
    @pytest.fixture
    def series(self, monkeypatch):
        monkeypatch.setattr(fx.yf, "Ticker", Pair(
            "USDEUR=X", 0.87,
            closes={"2026-09-14": 0.90, "2026-09-16": 0.92}))

    def test_the_rate_on_a_day_we_hold(self, series):
        assert fx.rate("USD", "EUR", on=date(2026, 9, 16)) == pytest.approx(0.92)

    def test_a_gap_takes_the_last_close_before_it(self, series):
        """Weekends and holidays have no close; the rate that applied is the
        one it was last fixed at."""
        assert fx.rate("USD", "EUR", on=date(2026, 9, 15)) == pytest.approx(0.90)

    def test_a_date_before_the_window_is_none(self, series):
        """The earliest rate we hold is not the rate that applied in 2001.
        Returning it would look like an answer."""
        assert fx.rate("USD", "EUR", on=date(2001, 1, 1)) is None

    def test_a_date_after_the_window_takes_the_latest(self, series):
        assert fx.rate("USD", "EUR", on=date(2026, 9, 30)) == pytest.approx(0.92)


class TestConvert:
    def test_an_amount_is_converted(self, monkeypatch):
        monkeypatch.setattr(fx.yf, "Ticker", Pair("USDEUR=X", 0.87))
        assert fx.convert(100.0, "USD", "EUR") == pytest.approx(87.0)

    def test_an_absent_amount_stays_absent(self):
        assert fx.convert(None, "USD", "EUR") is None

    def test_an_unavailable_rate_gives_no_amount(self, monkeypatch):
        monkeypatch.setattr(fx.yf, "Ticker", Pair("NOPE=X", 1.0))
        assert fx.convert(100.0, "XXX", "EUR") is None
