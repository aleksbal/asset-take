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
import math
from datetime import date

import pandas as pd
import yfinance as yf

from money import Converted

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


def _usable(value):
    """A rate has to be a positive finite number.

    NaN is truthy, so a plain truth test accepts it and caches it, and every
    position in that currency - and the portfolio total with them - becomes
    NaN. Zero and negatives are not rates either, and inverting them is worse
    than having nothing.
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value > 0


def _fetch_spot(currency, base):
    for pair, invert in _pairs(currency, base):
        try:
            px = yf.Ticker(pair).fast_info["last_price"]
        except Exception:
            continue
        if _usable(px):
            px = float(px)
            return 1.0 / px if invert else px
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
    return float(value) if _usable(value) else None


def _fetch_series(currency, base):
    for pair, invert in _pairs(currency, base):
        try:
            hist = yf.Ticker(pair).history(period=HISTORY_PERIOD, interval="1d")
        except Exception:
            continue
        if hist is None or hist.empty or "Close" not in hist:
            continue
        closes = hist["Close"][hist["Close"].apply(_usable)]
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


def exchange(amount, base, on=None):
    """`amount` (a `Money`) in `base` as a `Converted`, or None.

    The conversion any caller should reach for. `convert` above returns a bare
    float, which is how a rate of 1.0 came to be defaulted in seven places: a
    number that has been converted looks exactly like one that has not. A
    `Converted` carries the rate that produced it, so the difference is on the
    record rather than in the caller's memory.
    """
    if amount is None or amount.amount is None:
        return None
    r = rate(amount.currency, base, on=on)
    return None if r is None else Converted(original=amount, rate=r,
                                            base=base, on=on)
