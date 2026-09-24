"""Tests for market/fx.py: current and historical exchange rates."""
from datetime import date

import pandas as pd
import pytest

from market import fx


@pytest.fixture(autouse=True)
def clear_caches():
    fx._SPOT.clear()
    fx._SERIES.clear()


class Pair:
    """A fake provider that knows one direction of one pair."""

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
        monkeypatch.setattr(fx.yf, "Ticker", Pair("EURUSD=X", 1.25))
        assert fx.rate("USD", "EUR") == pytest.approx(0.8)

    def test_an_unavailable_pair_is_none_not_one(self, monkeypatch):
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
        assert fx.rate("USD", "EUR", on=date(2026, 9, 15)) == pytest.approx(0.90)

    def test_a_date_before_the_window_is_none(self, series):
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


class TestUnusableRates:
    """NaN, zero and negative rates are rejected."""

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.5,
                                     None, "x"])
    def test_an_unusable_spot_price_is_rejected(self, monkeypatch, bad):
        monkeypatch.setattr(fx.yf, "Ticker", Pair("USDEUR=X", bad))
        assert fx.rate("USD", "EUR") is None

    def test_the_reverse_pair_is_tried_when_the_first_is_unusable(self,
                                                                 monkeypatch):
        class EitherWay:
            def __call__(self, symbol):
                self.symbol = symbol
                return self

            @property
            def fast_info(self):
                return {"last_price": float("nan") if self.symbol == "USDEUR=X"
                        else 1.25}

        monkeypatch.setattr(fx.yf, "Ticker", EitherWay())
        assert fx.rate("USD", "EUR") == pytest.approx(0.8)

    def test_an_unusable_historical_close_is_rejected(self, monkeypatch):
        monkeypatch.setattr(fx.yf, "Ticker", Pair(
            "USDEUR=X", 0.87, closes={"2026-09-16": float("nan")}))
        assert fx.rate("USD", "EUR", on=date(2026, 9, 16)) is None

    def test_a_usable_close_beside_an_unusable_one_is_kept(self, monkeypatch):
        monkeypatch.setattr(fx.yf, "Ticker", Pair(
            "USDEUR=X", 0.87,
            closes={"2026-09-14": 0.90, "2026-09-16": float("nan")}))
        assert fx.rate("USD", "EUR", on=date(2026, 9, 16)) == pytest.approx(0.90)
