"""Resolve ISIN -> Yahoo ticker, using the broker's own price as ground truth.

Yahoo's ISIN search returns a security's primary listing, which is often in a
different currency than a German broker's EUR cost basis. Picking that would
silently corrupt every P&L figure. So candidates are gathered from both the
ISIN and the instrument name, and the one whose live price best matches the
broker's valuation wins.

The resulting map is a plain CSV. Auto-resolution is wrong sometimes; edit it
by hand and set status=manual, and the entry is kept as-is from then on.
"""
import csv
import re
from pathlib import Path

import yfinance as yf

import paths

MAP_PATH = paths.ISIN_MAP
TOLERANCE = 0.05
# Two listings of one security quote within a few hundredths of a percent of
# each other. Wider than that is a real price difference, not venue noise.
PRICE_NOISE = 0.01
# Depth is a threshold, not a score: past it a listing can carry a trend, and
# preferring 506 days over 505 only swaps one usable listing for another.
# Set below a full trading year on purpose - a listing that missed a few days
# to local holidays is no less usable than one that did not.
SUFFICIENT_HISTORY = 200  # fraction by which a candidate may differ from the broker price
TICKER = re.compile(r"^[A-Z0-9]{1,6}(\.[A-Z]{1,3})?$")
ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
COLUMNS = ["isin", "ticker", "currency", "yahoo_price", "broker_price", "deviation_pct", "status", "display_name", "name"]


def load_map():
    if not MAP_PATH.exists():
        return {}
    with MAP_PATH.open(encoding="utf-8") as f:
        return {r["isin"]: r for r in csv.DictReader(f)}


def save_map(rows):
    with MAP_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda x: x["isin"]):
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def _clean_name(name):
    """ING names carry instrument cruft: 'NVIDIA CORP.      DL-,001'."""
    n = re.split(r"\s{2,}", name)[0]
    n = re.sub(r"\b(INH|NA|O\.N\.|DL|LS|ADR|CL\.?[A-Z]?|INC\.?|AG|SE|LTD|PLC|CORP\.?)\b", " ", n)
    n = re.sub(r"[-,./]\S*", " ", n)
    return " ".join(n.split()[:3])


def _candidates(isin, name):
    out = []
    for query in (isin, _clean_name(name)):
        if not query:
            continue
        try:
            for q in yf.Search(query, max_results=8).quotes:
                sym = q.get("symbol")
                if sym and sym not in out and not sym.endswith("=F"):
                    out.append(sym)
        except Exception:
            pass
    return out


def display_name(ticker):
    """Yahoo's readable name. Broker exports carry abbreviated strings like
    'SPACE EXPL.TECHS. CL.A', which are unusable as labels."""
    try:
        n = yf.Ticker(ticker).info.get("longName") or yf.Ticker(ticker).info.get("shortName")
        return n.strip() if n else ""
    except Exception:
        return ""


def fill_display_names(rows):
    """Populate any missing display_name in-place. Cached: only fetches blanks."""
    for r in rows.values():
        if r.get("ticker") and not r.get("display_name"):
            r["display_name"] = display_name(r["ticker"])
    return rows


def display_name(ticker):
    """The provider's own instrument name.

    Broker exports carry abbreviations forced by fixed-width fields
    ('SPACE EXPL.TECHS. CL.A'), which are unusable as labels. Reference data
    keyed on the instrument is the standard source for display names.
    """
    try:
        info = yf.Ticker(ticker).info
        n = info.get("longName") or info.get("shortName")
        return n.strip() if n else ""
    except Exception:
        return ""


def fill_display_names(rows):
    """Populate missing display_name in place; only fetches blanks."""
    for r in rows.values():
        if r.get("ticker") and not r.get("display_name"):
            r["display_name"] = display_name(r["ticker"])
    return rows


def _price(ticker):
    try:
        fi = yf.Ticker(ticker).fast_info
        return float(fi["last_price"]), fi["currency"]
    except Exception:
        return None, None


def _depth(ticker):
    """Trading days of daily history the provider holds for this listing.

    A regional venue can quote the right price and carry almost no history:
    Stuttgart listings priced five holdings correctly while offering a single
    day, which values the portfolio but cannot chart it.
    """
    try:
        return len(yf.Ticker(ticker).history(period="1y", interval="1d"))
    except Exception:
        return 0


def _row(holding, ticker="", currency=None, price="", deviation="", status="unresolved"):
    return {"isin": holding.isin, "ticker": ticker,
            "currency": currency or holding.currency,
            "yahoo_price": price, "broker_price": holding.broker_price,
            "deviation_pct": deviation, "status": status, "name": holding.name}


def resolve(holding, existing=None):
    """Map one holding to a ticker.

    Where the source states a valuation, candidates are verified against it and
    the closest currency-matching one wins. Where it does not - a plain CSV
    export, for instance - there is nothing to verify against, so the first
    currency-matching candidate is taken and marked `unverified`. That status
    is the point: an unverified mapping may be the wrong instrument entirely,
    and nothing downstream can tell.
    """
    if existing and existing.get("status") == "manual":
        return existing

    # An explicitly supplied ticker names the listing the holder actually
    # holds; searching the ISIN could return a different venue entirely.
    given = holding.ticker or (holding.isin if TICKER.match(holding.isin or "")
                               and not ISIN.match(holding.isin or "") else None)
    if given:
        px, cur = _price(given)
        if px is not None and cur == holding.currency:
            if holding.broker_price:
                dev = abs(px - holding.broker_price) / holding.broker_price
                return _row(holding, given, cur, round(px, 4),
                            round(dev * 100, 2), "ok" if dev <= TOLERANCE else "check")
            return _row(holding, given, cur, round(px, 4), "", "unverified")

    matches = []
    for sym in _candidates(holding.isin, holding.name):
        px, cur = _price(sym)
        if px is None or cur != holding.currency:
            continue
        if not holding.broker_price:
            return _row(holding, sym, cur, round(px, 4), "", "unverified")
        dev = abs(px - holding.broker_price) / holding.broker_price
        matches.append((sym, px, dev, cur))

    if not matches:
        return _row(holding)

    # Taking the closest price outright let a listing with one day of history
    # beat one with two years of it, over a difference of four cents. Where
    # two listings are the same price to within noise, depth decides.
    #
    # The band is deliberately narrow. TOLERANCE exists to catch a mapping to
    # the wrong instrument, not to declare everything under it equivalent - a
    # candidate 4% out is a different security, however much history it has.
    closest = min(m[2] for m in matches)
    tied = [m for m in matches if m[2] <= closest + PRICE_NOISE]
    best = max(tied, key=lambda m: (min(_depth(m[0]), SUFFICIENT_HISTORY), -m[2]))

    sym, px, dev, cur = best
    return _row(holding, sym, cur, round(px, 4), round(dev * 100, 2),
                "ok" if dev <= TOLERANCE else "check")
