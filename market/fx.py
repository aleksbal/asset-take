"""Exchange rates, current and historical, from the price provider."""
import math
from datetime import date

import pandas as pd
import yfinance as yf

from market.money import Converted

HISTORY_PERIOD = "2y"

_SPOT = {}
_SERIES = {}


def rate(currency, base, on=None):
    """One unit of `currency` in `base`, or None if it cannot be fetched.

    With `on`, the rate at the last close on or before that date; otherwise the
    current rate.
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
    """Whether `value` is a positive finite number."""
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
        # Before the fetched window: no rate.
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
    """Both provider symbols for a pair, with whether each must be inverted."""
    return ((f"{currency}{base}=X", False), (f"{base}{currency}=X", True))


def convert(amount, currency, base, on=None):
    """`amount` in `base` as a float, or None if the rate is unavailable."""
    if amount is None:
        return None
    r = rate(currency, base, on=on)
    return None if r is None else amount * r


def exchange(amount, base, on=None):
    """`amount` (a `Money`) in `base` as a `Converted`, or None if the rate is
    unavailable."""
    if amount is None or amount.amount is None:
        return None
    r = rate(amount.currency, base, on=on)
    return None if r is None else Converted(original=amount, rate=r,
                                            base=base, on=on)
