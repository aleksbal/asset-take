#!/usr/bin/env python3
"""Per-listing daily close series, stored as one CSV per ticker.

Rows are keyed by trading session and marked with their source: "yahoo" for
rows from the provider's split-adjusted history, "local" for closes recorded
by a daily run. Seeded from the provider once, then extended by each run.
"""
import csv
import os
import pathlib
import tempfile
from datetime import date

import yfinance as yf

import paths
from market.quotes import Quote

COLUMNS = ["date", "close", "source"]
YAHOO, LOCAL = "yahoo", "local"
BACKFILL_PERIOD = "2y"
# A one-day move larger than this is treated as a possible split and triggers
# a re-seed. Low enough to catch a 3-for-2 (ratio 0.667) and its reverse (1.5).
SPLIT_SUSPICION = 0.20


def path_for(ticker):
    """The CSV file for `ticker`."""
    return paths.PRICES / f"{ticker}.csv"


def load(ticker):
    """The stored series as {date: (close, source)}; {} if there is none."""
    p = path_for(ticker)
    if not p.exists():
        return {}
    out = {}
    with open(p, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row["date"]] = (float(row["close"]), row.get("source", ""))
            except (TypeError, ValueError):
                continue        # skip unreadable rows
    return out


def _write(ticker, series):
    """Replace the stored series atomically, so a failed write leaves the old
    file intact."""
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


def backfill(ticker, fetch=None, unit=None):
    """Seed the series from the provider's history. Returns the rows added.

    Does nothing if the series already holds provider rows. Locally recorded
    rows are kept. `unit` is the quote unit, if known.
    """
    series = load(ticker)
    if any(source == YAHOO for _, source in series.values()):
        return 0
    rows = _fetched(ticker, fetch, unit)
    if not rows:
        return 0
    merged = {day: (close, YAHOO) for day, close in rows.items()}
    for day, entry in series.items():
        merged.setdefault(day, entry)
    _write(ticker, merged)
    return len(rows)


def record(ticker, close, on=None):
    """Store `close` for session `on` (a date or ISO string; default: today).
    Returns whether a row was added; an existing row is never overwritten.
    """
    day = on.isoformat() if hasattr(on, "isoformat") else (on or date.today().isoformat())
    series = load(ticker)
    if day in series:
        return False
    series[day] = (close, LOCAL)
    _write(ticker, series)
    return True


def _fetch(ticker, unit=None):
    """Settled daily closes from the provider as {iso date: close}, in the major
    currency unit; {} on failure. Uses `unit` if given, else asks the provider.
    """
    try:
        handle = yf.Ticker(ticker)
        hist = handle.history(period=BACKFILL_PERIOD, interval="1d",
                              auto_adjust=True)
    except Exception:
        return {}
    if hist is None or hist.empty or "Close" not in hist:
        return {}
    currency = unit
    if not currency:
        try:
            currency = handle.fast_info["currency"]
        except Exception:
            currency = None

    out = {}
    for ts, close in hist["Close"].items():
        if close != close:          # NaN: no data
            continue
        # Skip bars with an unknown unit or an unsettled session.
        quote = Quote.from_provider(close, currency, session=ts.date())
        if quote is None or not quote.settled:
            continue
        out[quote.session.isoformat()] = quote.price
    return out


def _fetched(ticker, fetch, unit):
    """Closes from `fetch` if given, else from the provider."""
    if fetch is not None:
        return fetch(ticker)
    return _fetch(ticker, unit=unit)


def _looks_rescaled(previous, close):
    """Whether the move from `previous` to `close` exceeds SPLIT_SUSPICION."""
    if not previous or not close:
        return False
    ratio = close / previous
    return ratio < 1 - SPLIT_SUSPICION or ratio > 1 / (1 - SPLIT_SUSPICION)


def refresh(ticker, fetch=None, unit=None):
    """Replace the series with the provider's history, for when its scale has
    changed (e.g. after a split). Returns the rows fetched.

    Stored rows after the fetched range are kept. Rows before it are rescaled
    by the ratio on the first overlapping day, or dropped if there is none.
    """
    series = load(ticker)
    if not series:
        return 0
    rows = _fetched(ticker, fetch, unit)
    if not rows:
        return 0

    earliest, latest = min(rows), max(rows)
    merged = {day: (close, YAHOO) for day, close in rows.items()}

    # Ratio between provider and stored close on the first overlapping day.
    scale = None
    for day in sorted(rows):
        if day in series and series[day][0]:
            scale = rows[day] / series[day][0]
            break

    for day, (close, source) in series.items():
        if day > latest:
            merged[day] = (close, source)
        elif day < earliest and scale is not None:
            merged[day] = (close * scale, source)
        elif day < earliest:
            # No ratio to rescale with: drop.
            continue

    _write(ticker, merged)
    return len(rows)


def update(closes, units=None, fetch=None):
    """Seed each ticker, then record its closes.

    `closes` maps ticker to {session: close}; `units` maps ticker to its quote
    unit where known. Sessions already stored are skipped. A ticker with no
    closes is still seeded. A close that moves more than SPLIT_SUSPICION from the
    previous stored close triggers `refresh()` first.
    Returns (seeded, recorded, rescaled) counts.
    """
    seeded = recorded = rescaled = 0
    for ticker, days in closes.items():
        if not ticker:
            continue
        unit = (units or {}).get(ticker)
        seeded += 1 if backfill(ticker, fetch=fetch, unit=unit) else 0
        for day in sorted(days or {}):
            close = days[day]
            series = load(ticker)
            if day in series:
                continue
            before = [d for d in series if d < day]
            if before and _looks_rescaled(series[max(before)][0], close):
                rescaled += 1 if refresh(ticker, fetch=fetch, unit=unit) else 0
            recorded += 1 if record(ticker, close, on=day) else 0
    return seeded, recorded, rescaled
