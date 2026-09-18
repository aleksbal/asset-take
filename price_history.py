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
from datetime import date

import paths

COLUMNS = ["date", "close", "source"]
YAHOO, LOCAL = "yahoo", "local"
BACKFILL_PERIOD = "2y"


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
    p = path_for(ticker)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for day in sorted(series):
            close, source = series[day]
            w.writerow({"date": day, "close": round(close, 6), "source": source})


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


def update(priced, fetch=None, on=None):
    """Seed any listing we hold no series for, then record today's close.

    `priced` maps ticker to today's close. Returns (seeded, recorded) counts.
    """
    seeded = recorded = 0
    for ticker, close in priced.items():
        if not ticker or close is None:
            continue
        seeded += 1 if backfill(ticker, fetch=fetch) else 0
        recorded += 1 if record(ticker, close, on=on) else 0
    return seeded, recorded
