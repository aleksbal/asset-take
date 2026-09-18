"""Descriptive statistics over a position's price series.

These are facts, not signals. Each one says what already happened - how far
below a recent peak a price sits, which side of its moving average it is on -
and none of them says what to do about it. Evidence that acting on them beats
holding is contested, and by price alone a stock that has stopped growing is
indistinguishable from one that is merely resting.

Nothing here is computed from too little data. A 200-day average of 30 days of
prices is not a rough 200-day average, it is a different number wearing the
name, and a reader cannot tell the difference from the output.
"""
from datetime import date, timedelta

TRADING_DAYS_PER_MONTH = 21
RSI_PERIOD = 14
# A window is only reported when the series covers it. Some slack, because a
# venue's holidays are not the calendar's and a series need not be gapless.
COVERAGE = 0.8


def _window(closes, days):
    """The last `days` sessions, or None if the series does not cover them."""
    if len(closes) < days * COVERAGE:
        return None
    return closes[-days:]


def drawdown(closes, months=6):
    """How far below its peak the last price sits, and when that peak was.

    The plateau case this exists for: a price that stopped rising some time
    ago and has been drifting since. A table of current values cannot show
    that, because nothing in today's number remembers the peak.
    """
    window = _window(closes, months * TRADING_DAYS_PER_MONTH)
    if not window:
        return None
    peak = max(close for _, close in window)
    if peak <= 0:
        return None
    last_day, last = window[-1]
    peak_day = next(day for day, close in window if close == peak)
    return {
        "peak": peak,
        "peak_on": peak_day,
        "pct": (last / peak - 1) * 100,
        "days_since_peak": (_as_date(last_day) - _as_date(peak_day)).days,
    }


def moving_average(closes, days):
    """The mean of the last `days` closes, or None if unsupported."""
    window = _window(closes, days)
    if not window:
        return None
    return sum(close for _, close in window) / len(window)


def relative_to_average(closes, days):
    """Percent above or below the moving average, or None."""
    average = moving_average(closes, days)
    if not average:
        return None
    return (closes[-1][1] / average - 1) * 100


def rsi(closes, period=RSI_PERIOD):
    """Wilder's relative strength index over `period` sessions, or None.

    Needs period+1 closes for period changes. A flat series has no losses to
    divide by, which is 100 by convention rather than by arithmetic.
    """
    if len(closes) < period + 1:
        return None
    values = [close for _, close in closes]
    gains = losses = 0.0
    for previous, current in zip(values[:period], values[1:period + 1]):
        change = current - previous
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for previous, current in zip(values[period:], values[period + 1:]):
        change = current - previous
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100.0 - 100.0 / (1 + avg_gain / avg_loss)


def describe(closes):
    """Every metric the series supports, with None for those it does not."""
    if not closes:
        return {}
    closes = sorted(closes, key=lambda row: str(row[0]))
    return {
        "last": closes[-1][1],
        "drawdown": drawdown(closes),
        "vs_ma50": relative_to_average(closes, 50),
        "vs_ma200": relative_to_average(closes, 200),
        "rsi": rsi(closes),
        "sessions": len(closes),
    }


def _as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))
