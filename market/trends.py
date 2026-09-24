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
import calendar
from datetime import date

RSI_PERIOD = 14
DRAWDOWN_MONTHS = 6
VOL_WINDOW = 20
TRADING_DAYS = 252


def _window(closes, days):
    """The last `days` sessions, or None if the series does not hold them.

    Exactly `days`, with no tolerance. Non-trading days are already absent
    from a price series, so a venue's holidays are no argument for reducing a
    session count - and averaging 170 closes under a 200-session label gives
    a materially different number that the output cannot be distinguished
    from the real one.
    """
    if len(closes) < days:
        return None
    return closes[-days:]


def _since(closes, months, end=None):
    """The closes within the last `months`, or None if the series is shorter.

    A calendar window, because "its six-month high" is a claim about time
    rather than about sessions. It is only reported when the series actually
    reaches back that far; otherwise it would be the high of whatever we
    happen to hold, under a label saying six months.
    """
    if not closes:
        return None
    last = _as_date(end or closes[-1][0])
    start = _minus_months(last, months)
    if _as_date(closes[0][0]) > start:
        return None
    return [row for row in closes if _as_date(row[0]) >= start]


def _minus_months(day, months):
    """`day` less whole calendar months.

    Not an averaged day count: months differ in length, so six of them from
    18 September is 18 March, while 183 days is the 19th. A peak on the
    boundary would fall outside a window labelled six months.

    A day that does not exist in the earlier month clamps to its last - the
    31st of August less six months is the 28th or 29th of February.
    """
    year, month = day.year, day.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def drawdown(closes, months=DRAWDOWN_MONTHS, last=None, on=None):
    """How far below its peak the last price sits, and when that peak was.

    The plateau case this exists for: a price that stopped rising some time
    ago and has been drifting since. A table of current values cannot show
    that, because nothing in today's number remembers the peak.
    """
    window = _since(closes, months, end=on)
    if not window:
        return None
    stored_day, stored = window[-1]
    # The age is measured to the price being reported. A live Monday quote
    # against a Friday peak is three days on, not the nought that measuring
    # to the last stored close would give.
    last_day = on or stored_day
    # A live price can itself be the high. Comparing today against a peak it
    # has already passed would report a fall that has not happened.
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
    """The mean of the last `days` closes, or None if unsupported."""
    window = _window(closes, days)
    if not window:
        return None
    return sum(close for _, close in window) / len(window)


def relative_to_average(closes, days, last=None):
    """Percent above or below the moving average, or None.

    `last` is compared *against* the average rather than counted in it. A
    live quote is not a close, and folding it into the window would let 199
    settled closes plus an intraday value be reported as a 200-session
    average - or, on a longer series, push a settled close out to make room.
    """
    average = moving_average(closes, days)
    if not average:
        return None
    current = closes[-1][1] if last is None else last
    return (current / average - 1) * 100


def realized_vol(closes, days=VOL_WINDOW):
    """Annualised size of day-to-day moves over the last `days` sessions.

    Settled closes only, like `moving_average` - a live quote is one
    incomplete session and would inflate or dampen a figure meant to
    describe what already happened. `days` returns need `days + 1` closes,
    the same off-by-one `rsi` has for the same reason: a return is the gap
    between two closes, not a property of one.

    Annualised by the conventional 252 trading days, so a window of any
    length reads on the same scale - a fact this codebase does not act on
    any more than a drawdown or an RSI does.
    """
    window = _window(closes, days + 1)
    if not window:
        return None
    values = [close for _, close in window]
    # A zero close is not a real price - it is corrupt or missing data that
    # slipped past storage. Dropping just the one return it breaks and
    # reporting the rest under the full window's label would be a
    # shortened window wearing its name.
    if any(v == 0 for v in values):
        return None
    returns = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return (variance ** 0.5) * (TRADING_DAYS ** 0.5) * 100


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


def describe(closes, live=None, live_on=None):
    """Every metric the series supports, with None for those it does not.

    `live` is the current session's price, which is not a close. It is what
    each metric is measured against, but it never enters a window that counts
    sessions - except RSI, which measures the latest change rather than an
    average over a named number of closes, and is conventionally computed
    against the current price.
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
