"""Descriptive statistics over a listing's daily closes.

Each function returns None when the series is too short for its window;
a window is never shortened to fit.
"""
import calendar
from datetime import date

RSI_PERIOD = 14
DRAWDOWN_MONTHS = 6
VOL_WINDOW = 20
TRADING_DAYS = 252


def _window(closes, days):
    """The last `days` closes, or None if there are fewer."""
    if len(closes) < days:
        return None
    return closes[-days:]


def _since(closes, months, end=None):
    """The closes from `months` calendar months before `end` (default: the
    last close) onwards, or None if the series does not reach back that far."""
    if not closes:
        return None
    last = _as_date(end or closes[-1][0])
    start = _minus_months(last, months)
    if _as_date(closes[0][0]) > start:
        return None
    return [row for row in closes if _as_date(row[0]) >= start]


def _minus_months(day, months):
    """`day` minus whole calendar months, clamped to the end of a shorter
    month (31 August minus six months is the last day of February)."""
    year, month = day.year, day.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def drawdown(closes, months=DRAWDOWN_MONTHS, last=None, on=None):
    """How far `last` sits below the highest close of the last `months`.

    `last` defaults to the last stored close and `on` to its date. Returns
    {peak, peak_on, pct, days_since_peak}, or None if the series is shorter
    than `months`. `last` itself counts as a candidate peak.
    """
    window = _since(closes, months, end=on)
    if not window:
        return None
    stored_day, stored = window[-1]
    last_day = on or stored_day
    last = stored if last is None else last
    peak = max(max(close for _, close in window), last)
    if peak <= 0:
        return None
    peak_day = next((day for day, close in window if close == peak), last_day)
    return {
        "peak": peak,
        "peak_on": peak_day,
        "pct": (last / peak - 1) * 100,
        "days_since_peak": (_as_date(last_day) - _as_date(peak_day)).days,
    }


def moving_average(closes, days):
    """The mean of the last `days` closes, or None if there are fewer."""
    window = _window(closes, days)
    if not window:
        return None
    return sum(close for _, close in window) / len(window)


def relative_to_average(closes, days, last=None):
    """Percent `last` sits above (+) or below (-) the `days`-close average.

    `last` defaults to the last close. It is compared with the average, not
    included in it. None if the average cannot be computed.
    """
    average = moving_average(closes, days)
    if not average:
        return None
    current = closes[-1][1] if last is None else last
    return (current / average - 1) * 100


def realized_vol(closes, days=VOL_WINDOW):
    """Annualised volatility of daily returns over the last `days` sessions,
    in percent.

    Sample standard deviation of the `days` daily returns, times sqrt(252).
    Needs `days + 1` settled closes. None if there are fewer, or if any close
    in the window is zero.
    """
    window = _window(closes, days + 1)
    if not window:
        return None
    values = [close for _, close in window]
    if any(v == 0 for v in values):
        return None
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return (variance ** 0.5) * (TRADING_DAYS ** 0.5) * 100


def rsi(closes, period=RSI_PERIOD):
    """Wilder's relative strength index over `period` sessions.

    Needs `period + 1` closes; None if there are fewer. 100 when there were
    only gains, 50 when there was no change at all.
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


def describe(closes, live=None, live_on=None):
    """Every metric for a series of (date, close), None where unsupported.

    `live` is today's unsettled price, dated `live_on`. Drawdown and the
    moving-average comparisons measure it against the stored closes; RSI
    includes it as the latest change.
    """
    if not closes:
        return {}
    closes = sorted(closes, key=lambda row: str(row[0]))
    last = closes[-1][1] if live is None else live
    return {
        "last": last,
        "drawdown": drawdown(closes, last=live, on=live_on if live is not None
                             else None),
        "vs_ma50": relative_to_average(closes, 50, last=live),
        "vs_ma200": relative_to_average(closes, 200, last=live),
        "rsi": rsi(closes if live is None
                   else closes + [(live_on or _as_date(closes[-1][0]), live)]),
        "sessions": len(closes),
    }


def _as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))
