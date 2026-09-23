"""The parameters a row can carry, and how each one is computed.

One entry per parameter. Adding a parameter is writing the maths in
`trends.py` and adding an entry here: it then appears in the report, in the
page, and in anything that filters rows, without those knowing its name.

The registry carries what a parameter *means* - its label, its unit, a
sentence a reader needs - and never how to print it. Decimal places, an em
dash for a missing value and which side is green are display decisions and
belong to whoever renders.

`needs` names the inputs a parameter requires. Every parameter here needs
only closes, volume, or both - never a cost basis or anything else that
implies a holding - so everything here runs on any instrument, held or not,
which is what lets a market-wide screen reuse this file unchanged. A listing
with no volume recorded simply has no `volume_trend`, the same way a
position with no cost basis has no P&L: absent, not computed from a
substituted zero.
"""
from dataclasses import dataclass
from typing import Callable

from market import trends


@dataclass(frozen=True)
class Indicator:
    key: str
    label: str
    unit: str          # percent | fall | index | days - how to read it
    means: str         # one sentence, carried into the report for its reader
    needs: tuple       # the inputs required, e.g. ("closes",)
    compute: Callable  # (closes, live, live_on, volumes) -> a number, or None


def _drawdown(closes, live, live_on, part):
    d = trends.drawdown(closes, last=live, on=live_on if live is not None else None)
    return None if d is None else d[part]


# Two entries rather than one returning a pair. A parameter is one number:
# that is what makes it filterable, comparable and printable without the
# reader knowing which parameter it is holding.
DRAWDOWN = Indicator(
    key="drawdown", label="From 6m high", unit="fall",
    means="below its highest close of the last six months",
    needs=("closes",),
    compute=lambda c, live, on, volumes=None: _drawdown(c, live, on, "pct"))

PEAK_AGE = Indicator(
    key="peak_age", label="High set", unit="days",
    means="days since that six-month high was set",
    needs=("closes",),
    compute=lambda c, live, on, volumes=None: _drawdown(c, live, on, "days_since_peak"))

VS_MA50 = Indicator(
    key="vs_ma50", label="vs 50d", unit="percent",
    means="above or below the average close of the last 50 sessions",
    needs=("closes",),
    compute=lambda c, live, on, volumes=None: trends.relative_to_average(c, 50, last=live))

VS_MA200 = Indicator(
    key="vs_ma200", label="vs 200d", unit="percent",
    means="above or below the average close of the last 200 sessions",
    needs=("closes",),
    compute=lambda c, live, on, volumes=None: trends.relative_to_average(c, 200, last=live))

RSI = Indicator(
    key="rsi", label="RSI", unit="index",
    means=("relative strength index over 14 sessions; 30 and 70 are "
           "conventional markers, not thresholds to act on"),
    needs=("closes",),
    compute=lambda c, live, on, volumes=None: trends.rsi(
        c if live is None else c + [(on or trends._as_date(c[-1][0]), live)]))

VOLATILITY = Indicator(
    key="volatility", label="Volatility", unit="vol",
    means=("annualised size of day-to-day moves over the last 20 sessions; "
           "a magnitude, not a direction"),
    needs=("closes",),
    compute=lambda c, live, on, volumes=None: trends.realized_vol(c))

VOLUME_TREND = Indicator(
    key="volume_trend", label="Volume", unit="volume",
    means=("most recent session's volume against its own 20-session "
           "average; above or below, neither is inherently good or bad"),
    needs=("volumes",),
    compute=lambda c, live, on, volumes=None: (
        trends.relative_to_average(volumes, 20) if volumes else None))

ALL = [DRAWDOWN, PEAK_AGE, VS_MA50, VS_MA200, RSI, VOLATILITY, VOLUME_TREND]


def declared(indicators=ALL):
    """What the parameters are, for a reader of the report.

    Carried in the file so a program reading it - another view, a screen, a
    model - does not have to be told separately what a column means.
    """
    return [{"key": i.key, "label": i.label, "unit": i.unit, "means": i.means}
            for i in indicators]


def values(closes, live=None, live_on=None, volumes=None, have=None, indicators=ALL):
    """Every parameter that `have` supports, computed from this series.

    An empty series yields nothing at all rather than a row of zeroes: no
    parameter is computed from data that cannot support it, and absent and
    neutral are different claims.

    `volumes` is optional and separate from `closes` for the same reason a
    cost basis would be: not every listing has it recorded, so a parameter
    that needs it must say so and be absent from rows without one, rather
    than computed from a substituted zero. Passing it is what adds
    `"volumes"` to `have`; a caller need not track that itself.
    """
    if not closes:
        return {}
    closes = sorted(closes, key=lambda row: str(row[0]))
    if have is None:
        have = ("closes",) + (("volumes",) if volumes else ())
    if volumes:
        volumes = sorted(volumes, key=lambda row: str(row[0]))
    return {i.key: i.compute(closes, live, live_on, volumes)
            for i in indicators if set(i.needs) <= set(have)}


def inputs(closes, live=None, live_on=None):
    """What the parameters were computed from.

    Carried beside the values because a figure computed from 40 sessions and
    one computed from 400 are different claims, and the number alone cannot
    be told apart. Not declared as parameters: these describe the
    calculation rather than the instrument, so they belong in the report
    without becoming a column.
    """
    if not closes:
        return {}
    closes = sorted(closes, key=lambda row: str(row[0]))
    return {"last": closes[-1][1] if live is None else live,
            "sessions": len(closes)}
