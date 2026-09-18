"""Exchange rates, current and historical, from the price provider.

Two copies of this existed - one in resolution, one in valuation - and they
disagreed in ways that mattered. The valuation copy fell back to a rate of 1.0
for a pair it could not fetch, which silently values a foreign holding as
though it were domestic. This one returns None and lets the caller decide,
because a fabricated rate is indistinguishable from a real one downstream.

Historical rates are available for the same window as prices, so a conversion
can be done at the rate that applied on a given day rather than today's. What
we lack for a cost basis is the purchase date, not the rate.
"""
from datetime import date

import pandas as pd
import yfinance as yf

HISTORY_PERIOD = "2y"

_SPOT = {}
_SERIES = {}


def rate(currency, base, on=None):
    """One unit of `currency` in `base`, or None if it cannot be had.

    `on` asks for the rate that applied on that date, using the last close at
    or before it. Without it the current rate is used.

    None rather than 1.0: a pair we cannot price is unknown, and a holding
    valued at a made-up rate looks exactly like one valued correctly.
    """
    if currency == base:
        return 1.0
    if not currency or not base:
        return None
    if on is None:
        return _spot(currency, base)
    return _historical(currency, base, on)


def _spot(currency, base):
    key = (currency, base)
    if key not in _SPOT:
        _SPOT[key] = _fetch_spot(currency, base)
    return _SPOT[key]


def _fetch_spot(currency, base):
    for pair, invert in _pairs(currency, base):
        try:
            px = float(yf.Ticker(pair).fast_info["last_price"])
            if px:
                return 1.0 / px if invert else px
        except Exception:
            continue
    return None


def _historical(currency, base, on):
    key = (currency, base)
    if key not in _SERIES:
        _SERIES[key] = _fetch_series(currency, base)
    series = _SERIES[key]
    if series is None or series.empty:
        return None
    want = pd.Timestamp(on.isoformat() if hasattr(on, "isoformat") else on)
    if want < series.index.min():
        # Before the window we hold. Returning the earliest rate would look
        # like an answer; the caller needs to know we do not have one.
        return None
    value = series.asof(want)
    return None if pd.isna(value) else float(value)


def _fetch_series(currency, base):
    for pair, invert in _pairs(currency, base):
        try:
            hist = yf.Ticker(pair).history(period=HISTORY_PERIOD, interval="1d")
        except Exception:
            continue
        if hist is None or hist.empty or "Close" not in hist:
            continue
        closes = hist["Close"].dropna()
        if closes.empty:
            continue
        closes.index = closes.index.tz_localize(None)
        return (1.0 / closes) if invert else closes
    return None


def _pairs(currency, base):
    """The provider names a pair one way round; try both."""
    return ((f"{currency}{base}=X", False), (f"{base}{currency}=X", True))


def convert(amount, currency, base, on=None):
    """`amount` expressed in `base`, or None where the rate is unavailable."""
    if amount is None:
        return None
    r = rate(currency, base, on=on)
    return None if r is None else amount * r
