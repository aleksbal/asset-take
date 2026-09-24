"""The parameters a report row can carry, and how each is computed.

Each entry declares its key, label, unit, a one-sentence meaning, the inputs
it needs ("closes", "volumes") and a compute function. A parameter is only
computed for a row that has every input it needs.
"""
from dataclasses import dataclass
from typing import Callable

from market import trends


@dataclass(frozen=True)
class Indicator:
    key: str
    label: str
    unit: str          # percent | fall | index | days | vol | volume
    means: str         # one sentence describing the value
    needs: tuple       # required inputs, e.g. ("closes",)
    compute: Callable  # (closes, live, live_on, volumes) -> a number, or None


def _drawdown(closes, live, live_on, part):
    d = trends.drawdown(closes, last=live, on=live_on if live is not None else None)
    return None if d is None else d[part]


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
    """Key, label, unit and meaning of each parameter, for the report."""
    return [{"key": i.key, "label": i.label, "unit": i.unit, "means": i.means}
            for i in indicators]


def values(closes, live=None, live_on=None, volumes=None, have=None, indicators=ALL):
    """{key: value} for every parameter whose inputs are available.

    `have` lists the available inputs; by default it is derived from which of
    `closes` and `volumes` are non-empty. Returns {} when both are empty.
    """
    if not closes and not volumes:
        return {}
    if have is None:
        have = (("closes",) if closes else ()) + (("volumes",) if volumes else ())
    if closes:
        closes = sorted(closes, key=lambda row: str(row[0]))
    if volumes:
        volumes = sorted(volumes, key=lambda row: str(row[0]))
    return {i.key: i.compute(closes, live, live_on, volumes)
            for i in indicators if set(i.needs) <= set(have)}


def inputs(closes, live=None, live_on=None):
    """The price the parameters were measured against and the number of
    closes they were computed from. {} for an empty series."""
    if not closes:
        return {}
    closes = sorted(closes, key=lambda row: str(row[0]))
    return {"last": closes[-1][1] if live is None else live,
            "sessions": len(closes)}
