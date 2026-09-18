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
TOLERANCE = 0.05  # fraction by which a candidate may differ from the broker price
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


def resolve(holding, existing=None):
    """Return a map row for one holding."""
    if existing and existing.get("status") == "manual":
        return existing

    best = None
    for sym in _candidates(holding.isin, holding.name):
        px, cur = _price(sym)
        if px is None or cur != holding.currency:
            continue
        dev = abs(px - holding.broker_price) / holding.broker_price if holding.broker_price else None
        if dev is None:
            continue
        if best is None or dev < best[2]:
            best = (sym, px, dev, cur)

    if best is None:
        return {"isin": holding.isin, "ticker": "", "currency": holding.currency,
                "yahoo_price": "", "broker_price": holding.broker_price,
                "deviation_pct": "", "status": "unresolved", "name": holding.name}

    sym, px, dev, cur = best
    return {"isin": holding.isin, "ticker": sym, "currency": cur,
            "yahoo_price": round(px, 4), "broker_price": holding.broker_price,
            "deviation_pct": round(dev * 100, 2),
            "status": "ok" if dev <= TOLERANCE else "check",
            "name": holding.name}
