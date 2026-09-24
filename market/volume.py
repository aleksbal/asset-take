#!/usr/bin/env python3
"""Per-listing daily volume series, seeded from the provider and extended by
our own runs.

Mirrors `market.prices`: one file per listing, keyed by the session the
figure belongs to, extended once a day, and marked with where each row came
from - a seed can fail transiently while the daily figure still arrives, and
without that mark a one-row local series reads as already seeded, so the
full history is never fetched again.

It is smaller than `market.prices` in one respect: a price needs a `Quote`
because its unit can be wrong in a way that is invisible without one - a
share count carries no currency, so there is nothing to normalise and
nothing to get wrong that way. And a split that doubles the share count is a
real change in how much is trading, not a scale error to repair, so unlike
`prices.refresh()` there is no re-seed here: a fetched volume is always
taken at face value.
"""
import csv
import os
import pathlib
import tempfile
from datetime import date

import yfinance as yf

import paths

COLUMNS = ["date", "volume", "source"]
YAHOO, LOCAL = "yahoo", "local"
BACKFILL_PERIOD = "2y"


def path_for(ticker):
    """One file per listing, same convention as `market.prices`."""
    return paths.VOLUMES / f"{ticker}.csv"


def load(ticker):
    """The stored series as {date: (volume, source)}, empty if we hold none."""
    p = path_for(ticker)
    if not p.exists():
        return {}
    out = {}
    with open(p, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row["date"]] = (float(row["volume"]), row.get("source", ""))
            except (TypeError, ValueError):
                continue        # a truncated write should not poison the series
    return out


def _write(ticker, series):
    """Replace the series atomically - see `market.prices._write` for why."""
    p = path_for(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            for day in sorted(series):
                volume, source = series[day]
                w.writerow({"date": day, "volume": round(volume),
                           "source": source})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def backfill(ticker, fetch=None):
    """Seed a listing that has no provider history yet. Returns rows added.

    A seed can fail transiently while the daily figure still arrives via
    `record()`, leaving a one-row local series. Testing for any series at
    all would read that as seeded and never retry - so, like
    `prices.backfill()`, this tests for a provider-sourced row specifically,
    and a row recorded locally in the meantime is kept, not overwritten.
    """
    series = load(ticker)
    if any(source == YAHOO for _, source in series.values()):
        return 0
    rows = _fetched(ticker, fetch)
    if not rows:
        return 0
    merged = {day: (vol, YAHOO) for day, vol in rows.items()}
    for day, entry in series.items():
        merged.setdefault(day, entry)
    _write(ticker, merged)
    return len(rows)


def record(ticker, volume, on=None):
    """Append an observed volume, preserving whatever is already stored.

    `on` is the session the figure belongs to, not the day we ran - same
    reasoning as `prices.record()`: a weekend run must not file it under a
    day the market never traded.
    """
    if volume is None:
        return False
    day = on.isoformat() if hasattr(on, "isoformat") else (on or date.today().isoformat())
    series = load(ticker)
    if day in series:
        return False
    series[day] = (volume, LOCAL)
    _write(ticker, series)
    return True


def _fetch(ticker):
    """Daily volume from the provider as {iso date: volume}, empty on failure."""
    try:
        hist = yf.Ticker(ticker).history(period=BACKFILL_PERIOD, interval="1d",
                                         auto_adjust=True)
    except Exception:
        return {}
    if hist is None or hist.empty or "Volume" not in hist:
        return {}
    today = date.today()
    out = {}
    for ts, vol in hist["Volume"].items():
        if vol != vol:              # NaN rows are gaps, not zero-volume days
            continue
        session = ts.date()
        if session >= today:        # unsettled: not a final count yet
            continue
        out[session.isoformat()] = float(vol)
    return out


def _fetched(ticker, fetch):
    if fetch is not None:
        return fetch(ticker)
    return _fetch(ticker)


def update(volumes, dates=None, fetch=None, on=None):
    """Seed any listing we hold no provider history for, then record today's
    volume.

    `volumes` maps ticker to its most recent traded volume, `dates` to the
    session that volume belongs to - the same two-map contract as
    `prices.update()`. Returns (seeded, recorded) counts.

    A ticker whose volume is None is still seeded, for the reason given in
    `prices.update()`: the seed does not depend on today's download.
    """
    seeded = recorded = 0
    for ticker, vol in volumes.items():
        if not ticker:
            continue
        seeded += 1 if backfill(ticker, fetch=fetch) else 0
        if vol is None:
            continue
        if dates is None:
            day = on
        elif dates.get(ticker):
            day = dates[ticker]
        else:
            continue
        recorded += 1 if record(ticker, vol, on=day) else 0
    return seeded, recorded
