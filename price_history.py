#!/usr/bin/env python3
"""Per-listing daily close series, seeded from the provider and then our own.

A price series belongs to the instrument, not to the account: it says what a
listing closed at, never what was held. That makes it safe to backfill, unlike
portfolio value, which we only know from the day we start recording.

The series is seeded once from the provider and extended by each daily run.
Every row records where it came from, because the provider adjusts its history
retroactively for splits and dividends while our own recorded closes stay as
observed - without provenance the two halves could not be told apart, and a
split would put a false step at the join.
"""
import csv
import os
import pathlib
import tempfile
from datetime import date

import paths

COLUMNS = ["date", "close", "source"]
YAHOO, LOCAL = "yahoo", "local"
BACKFILL_PERIOD = "2y"
# A single-day move this large is not a market move. It is the signal that the
# scale of the series may have changed under us, and the only cheap one we
# have: checking every listing for corporate actions every day would double
# the daily fetch for an event that happens to a holding once in years.
SPLIT_SUSPICION = 0.35


def path_for(ticker):
    """One file per listing. Tickers carry `.` and `^`, neither of which is a
    path separator, so the symbol is usable as a filename as it stands."""
    return paths.PRICES / f"{ticker}.csv"


def load(ticker):
    """The stored series as {date: (close, source)}, empty if we hold none."""
    p = path_for(ticker)
    if not p.exists():
        return {}
    out = {}
    with open(p, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row["date"]] = (float(row["close"]), row.get("source", ""))
            except (TypeError, ValueError):
                continue        # a truncated write should not poison the series
    return out


def _write(ticker, series):
    """Replace the series atomically.

    A truncating write that dies partway leaves a file with some valid rows,
    which is worse than no file: `load()` reads it as a series, `backfill()`
    then declines to restore what was lost, and the next write persists the
    remnant. Years of history would go permanently.
    """
    p = path_for(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            for day in sorted(series):
                close, source = series[day]
                w.writerow({"date": day, "close": round(close, 6),
                            "source": source})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        pathlib.Path(tmp).unlink(missing_ok=True)
        raise


def backfill(ticker, fetch=None):
    """Seed a listing that has no series yet. Returns the rows added.

    A listing we already hold is left alone, so this is safe to call on every
    run. A listing the provider cannot serve leaves no marker and is simply
    retried next time: a recent IPO has little history today and more later,
    and a permanent flag would keep it empty for good.
    """
    if load(ticker):
        return 0
    rows = (fetch or _fetch)(ticker)
    if not rows:
        return 0
    _write(ticker, {d: (c, YAHOO) for d, c in rows.items()})
    return len(rows)


def record(ticker, close, on=None):
    """Append today's observed close, preserving whatever is already stored.

    A day we fetched keeps its fetched value: re-recording it as local would
    lose the fact that the provider had adjusted it.
    """
    day = (on or date.today()).isoformat()
    series = load(ticker)
    if day in series:
        return False
    series[day] = (close, LOCAL)
    _write(ticker, series)
    return True


def _fetch(ticker):
    """Daily closes from the provider as {iso date: close}, empty on failure."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=BACKFILL_PERIOD, interval="1d",
                                         auto_adjust=True)
    except Exception:
        return {}
    if hist is None or hist.empty or "Close" not in hist:
        return {}
    out = {}
    for ts, close in hist["Close"].items():
        if close == close:          # NaN closes are gaps, not prices
            out[ts.date().isoformat()] = float(close)
    return out


def _looks_rescaled(previous, close):
    """Whether a day's move is too large to be a price move."""
    if not previous or not close:
        return False
    ratio = close / previous
    return ratio < 1 - SPLIT_SUSPICION or ratio > 1 / (1 - SPLIT_SUSPICION)


def refresh(ticker, fetch=None):
    """Re-seed a series whose scale no longer matches the provider's.

    A split rewrites the provider's history retroactively; ours stays as
    observed. Once one happens, closes we recorded before it sit on the old
    scale and closes after it on the new, and provenance cannot repair that
    because both sides are ours and no ratio is stored. The provider's
    adjusted history is the one consistent scale available, so it replaces
    the range it covers. Anything of ours beyond that range is kept.
    """
    series = load(ticker)
    if not series:
        return 0
    rows = (fetch or _fetch)(ticker)
    if not rows:
        return 0
    latest = max(rows)
    merged = {day: (close, YAHOO) for day, close in rows.items()}
    for day, entry in series.items():
        if day > latest:
            merged[day] = entry
    _write(ticker, merged)
    return len(rows)


def update(priced, fetch=None, on=None):
    """Seed any listing we hold no series for, then record today's close.

    A close that cannot be a day's move triggers a re-seed: the series has
    most likely been rescaled by a corporate action.

    `priced` maps ticker to today's close. Returns (seeded, recorded,
    rescaled) counts.
    """
    seeded = recorded = rescaled = 0
    for ticker, close in priced.items():
        if not ticker or close is None:
            continue
        seeded += 1 if backfill(ticker, fetch=fetch) else 0
        series = load(ticker)
        if series and _looks_rescaled(series[max(series)][0], close):
            rescaled += 1 if refresh(ticker, fetch=fetch) else 0
        recorded += 1 if record(ticker, close, on=on) else 0
    return seeded, recorded, rescaled
