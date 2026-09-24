#!/usr/bin/env python3
"""Per-listing daily volume series, stored as one CSV per ticker.

Rows are keyed by trading session and marked with their source: "yahoo" for
rows from the provider's history, "local" for rows recorded by a daily run.
Seeded from the provider once, then extended by each run.
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
    """The CSV file for `ticker`."""
    return paths.VOLUMES / f"{ticker}.csv"


def load(ticker):
    """The stored series as {date: (volume, source)}; {} if there is none."""
    p = path_for(ticker)
    if not p.exists():
        return {}
    out = {}
    with open(p, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row["date"]] = (float(row["volume"]), row.get("source", ""))
            except (TypeError, ValueError):
                continue        # skip unreadable rows
    return out


def _write(ticker, series):
    """Replace the stored series atomically."""
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
    """Seed the series from the provider's history. Returns the rows added.

    Does nothing if the series already holds provider rows. Locally recorded
    rows are kept.
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
    """Store `volume` for session `on` (default: today).

    Returns whether a row was added; an existing row is never overwritten.
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
    """Settled daily volumes from the provider as {iso date: volume}; {} on
    failure."""
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
        if vol != vol:              # NaN: no data
            continue
        session = ts.date()
        if session >= today:        # not settled yet
            continue
        out[session.isoformat()] = float(vol)
    return out


def _fetched(ticker, fetch):
    if fetch is not None:
        return fetch(ticker)
    return _fetch(ticker)


def update(volumes, fetch=None):
    """Seed each ticker, then record its volumes.

    `volumes` maps ticker to {session: volume}. A ticker with no volumes is
    still seeded. Returns (seeded, recorded) counts.
    """
    seeded = recorded = 0
    for ticker, days in volumes.items():
        if not ticker:
            continue
        seeded += 1 if backfill(ticker, fetch=fetch) else 0
        for day in sorted(days or {}):
            recorded += 1 if record(ticker, days[day], on=day) else 0
    return seeded, recorded
