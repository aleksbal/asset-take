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

import yfinance as yf

import paths
from market.quotes import Quote

COLUMNS = ["date", "close", "source"]
YAHOO, LOCAL = "yahoo", "local"
BACKFILL_PERIOD = "2y"
# A single-day move this large is not a market move. It is the signal that the
# scale of the series may have changed under us, and the only cheap one we
# have: checking every listing for corporate actions every day would double
# the daily fetch for an event that happens to a holding once in years.
#
# Set low enough for a 3-for-2, whose ratio is 0.667, and its reverse at 1.5.
# Erring low costs one redundant fetch on a violent day and nothing else - a
# re-seed replaces our rows with the provider's, which are authoritative
# whether or not anything was rescaled. Erring high leaves a series silently
# broken, so the asymmetry decides the threshold.
SPLIT_SUSPICION = 0.20


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


def backfill(ticker, fetch=None, unit=None):
    """Seed a listing that has no series yet. Returns the rows added.

    A listing we already hold provider history for is left alone, so this is
    safe to call on every run. A listing the provider cannot serve leaves no marker and is simply
    retried next time: a recent IPO has little history today and more later,
    and a permanent flag would keep it empty for good.
    """
    series = load(ticker)
    if any(source == YAHOO for _, source in series.values()):
        return 0
    rows = _fetched(ticker, fetch, unit)
    if not rows:
        return 0
    # A seed can fail while the daily close succeeds, leaving a one-row local
    # series. Testing for any series at all would then read that as seeded and
    # never retry, so the test is for provider rows - and what we recorded
    # ourselves is kept rather than thrown away by the eventual seed.
    merged = {day: (close, YAHOO) for day, close in rows.items()}
    for day, entry in series.items():
        merged.setdefault(day, entry)
    _write(ticker, merged)
    return len(rows)


def record(ticker, close, on=None):
    """Append an observed close, preserving whatever is already stored.

    `on` is the session the close belongs to, not the day we ran: a run on a
    weekend downloads Friday's close, and filing it under Saturday invents a
    trading day. It accepts a date or an ISO string.

    A day we fetched keeps its fetched value: re-recording it as local would
    lose the fact that the provider had adjusted it.
    """
    day = on.isoformat() if hasattr(on, "isoformat") else (on or date.today().isoformat())
    series = load(ticker)
    if day in series:
        return False
    series[day] = (close, LOCAL)
    _write(ticker, series)
    return True


def _fetch(ticker, unit=None):
    """Daily closes from the provider as {iso date: close}, empty on failure.

    Normalised to the major unit, like every other price we store. The daily
    close arrives already converted, so leaving these raw would mix pence with
    pounds in one series - and the resulting hundredfold step reads as a
    corporate action, re-seeding the series back to the raw values on every
    run.
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
        if close != close:          # NaN closes are gaps, not prices
            continue
        # Quote refuses an unknown unit and marks an unclosed session, so
        # both of the ways a bar can be unusable are decided in one place.
        # An unscaled series would sit beside converted closes and read as a
        # corporate action; an in-progress bar would fix an intraday value as
        # that day's close, which record() then never corrects.
        quote = Quote.from_provider(close, currency, session=ts.date())
        if quote is None or not quote.settled:
            continue
        out[quote.session.isoformat()] = quote.price
    return out


def _fetched(ticker, fetch, unit):
    """The provider's history, with a known unit passed through where we have
    one. positions.csv records it, so the fetch need not ask again."""
    if fetch is not None:
        return fetch(ticker)
    return _fetch(ticker, unit=unit)


def _looks_rescaled(previous, close):
    """Whether a day's move is too large to be a price move."""
    if not previous or not close:
        return False
    ratio = close / previous
    return ratio < 1 - SPLIT_SUSPICION or ratio > 1 / (1 - SPLIT_SUSPICION)


def refresh(ticker, fetch=None, unit=None):
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
    rows = _fetched(ticker, fetch, unit)
    if not rows:
        return 0

    earliest, latest = min(rows), max(rows)
    merged = {day: (close, YAHOO) for day, close in rows.items()}

    # Rows the provider's window does not reach are kept, not dropped: once a
    # series outlives BACKFILL_PERIOD, filtering to the fetched range alone
    # would delete every older row on each refresh. They predate the rescaling
    # though, so where a day overlaps we can read the ratio off it and put
    # them on the provider's scale.
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
            # No overlapping day, so no ratio to read. A refresh happens
            # because the scale is suspect; keeping these unscaled would
            # preserve the very discontinuity it exists to remove, and there
            # is nothing to correct them with. They are dropped.
            continue

    _write(ticker, merged)
    return len(rows)


def update(priced, dates=None, units=None, fetch=None, on=None):
    """Seed any listing we hold no series for, then record today's close.

    A close that cannot be a day's move triggers a re-seed: the series has
    most likely been rescaled by a corporate action.

    `priced` maps ticker to its latest close, `dates` to the session that
    close settled in, and `units` to the venue's quote unit where it is
    already known. Returns (seeded, recorded, rescaled) counts.
    """
    seeded = recorded = rescaled = 0
    for ticker, close in priced.items():
        if not ticker or close is None:
            continue
        unit = (units or {}).get(ticker)
        seeded += 1 if backfill(ticker, fetch=fetch, unit=unit) else 0
        series = load(ticker)
        if series and _looks_rescaled(series[max(series)][0], close):
            rescaled += 1 if refresh(ticker, fetch=fetch, unit=unit) else 0
        # The session the close settled in, not the day of the run. Where
        # sessions are supplied and this ticker has none, the latest bar has
        # not settled: seeding still happened, but nothing is recorded, since
        # an intraday value written as a close could never be corrected.
        if dates is None:
            day = on
        elif dates.get(ticker):
            day = dates[ticker]
        else:
            continue
        recorded += 1 if record(ticker, close, on=day) else 0
    return seeded, recorded, rescaled
