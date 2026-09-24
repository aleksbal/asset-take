"""Maps each holding's ISIN to a Yahoo ticker and stores the result in
isin_map.csv.

Candidates come from the ISIN and the instrument name. Where the broker
states a price, a candidate is accepted only if its price, converted to the
holding's currency, is within TOLERANCE of it. Rows with status=manual are
kept as they are.
"""
import csv
import re
from pathlib import Path

import yfinance as yf

from market import fx
import paths
from market.quotes import Quote

MAP_PATH = paths.ISIN_MAP
TOLERANCE = 0.05  # fraction by which a candidate may differ from the broker price
# Candidates within this fraction of the closest price count as equally close.
PRICE_NOISE = 0.01
# Daily bars beyond which more history no longer ranks a candidate higher.
SUFFICIENT_HISTORY = 200
# Prefer a listing quoting in the holding's currency, ranked below depth.
PREFER_QUOTE_CURRENCY = False
TICKER = re.compile(r"^[A-Z0-9]{1,6}(\.[A-Z]{1,3})?$")
ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
COLUMNS = ["isin", "ticker", "currency", "quote_currency", "yahoo_price", "broker_price", "deviation_pct", "status", "display_name", "name"]


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
    """A search query from a broker name: its first three words, without
    legal-form and share-class tokens.
    """
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
    """The provider's long (or short) name for `ticker`; "" on failure."""
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
    """Live price and currency of `ticker`, in the currency's major unit, or
    (None, None) if the quote or its unit cannot be read.
    """
    try:
        fi = yf.Ticker(ticker).fast_info
        quote = Quote.from_provider(fi["last_price"], fi["currency"])
    except Exception:
        return None, None
    return (quote.price, quote.currency) if quote else (None, None)




def _fx(currency, base):
    """Rate converting one unit of `currency` into `base`, or None."""
    return fx.rate(currency, base)


def _repriced(holding, existing):
    """A manual row with its price and deviation columns refreshed; the
    ticker is unchanged."""
    row = dict(existing)
    px, cur = _price(row.get("ticker"))
    base = _in_base(px, cur, holding.currency)
    if base is None:
        return row
    row["currency"] = cur
    row["quote_currency"] = _quote_unit(row.get("ticker"))
    row["yahoo_price"] = round(px, 4)
    row["broker_price"] = holding.broker_price
    row["deviation_pct"] = (round(abs(base - holding.broker_price)
                                  / holding.broker_price * 100, 2)
                            if holding.broker_price else "")
    return row


def _in_base(price, currency, base):
    """`price` expressed in `base`, or None where it cannot be."""
    if price is None:
        return None
    rate = _fx(currency, base)
    return None if rate is None else price * rate


def _depth(ticker):
    """Number of daily bars the provider holds for `ticker` over the last year;
    0 on failure.
    """
    try:
        return len(yf.Ticker(ticker).history(period="1y", interval="1d"))
    except Exception:
        return 0


def _quote_unit(ticker):
    """The unit `ticker` quotes in (e.g. `GBp`), or "" if unknown."""
    try:
        return yf.Ticker(ticker).fast_info["currency"] if ticker else ""
    except Exception:
        return ""


def _row(holding, ticker="", currency=None, price="", deviation="", status="unresolved"):
    return {"isin": holding.isin, "ticker": ticker,
            "currency": currency or holding.currency,
            "quote_currency": _quote_unit(ticker),
            "yahoo_price": price, "broker_price": holding.broker_price,
            "deviation_pct": deviation, "status": status, "name": holding.name}


def resolve(holding, existing=None, prefer_currency=None):
    """The map row for `holding`, keeping the display name from `existing` if
    the new row has none.
    """
    row = _resolve(holding, existing, prefer_currency)
    name = ((existing or {}).get("display_name") or "").strip()
    if name and not (row.get("display_name") or "").strip():
        row["display_name"] = name
    return row


def _resolve(holding, existing=None, prefer_currency=None):
    """The map row for `holding`.

    In order: a manual row is repriced and kept; an existing ticker that still
    verifies and has SUFFICIENT_HISTORY is kept; a ticker given by the source is
    used; otherwise candidates are searched. Among verified candidates within
    PRICE_NOISE of the closest price, the one with the most history wins, then
    (if `prefer_currency`) one quoting in the holding's currency. Without a
    broker price the deepest candidate is taken and marked `unverified`. Status
    is `ok`, `check` (outside TOLERANCE), `unverified` or `unresolved`.
    """
    prefer_currency = (PREFER_QUOTE_CURRENCY if prefer_currency is None
                       else prefer_currency)

    if existing and existing.get("status") == "manual":
        return _repriced(holding, existing)

    # Keep the existing ticker if it still verifies and has enough history.
    prev = (existing or {}).get("ticker")
    if prev and holding.broker_price:
        px, cur = _price(prev)
        base = _in_base(px, cur, holding.currency)
        if base is not None:
            dev = abs(base - holding.broker_price) / holding.broker_price
            if dev <= TOLERANCE and _depth(prev) >= SUFFICIENT_HISTORY:
                return _row(holding, prev, cur, round(px, 4),
                            round(dev * 100, 2), "ok")

    # A ticker named by the source is used instead of searching.
    given = holding.ticker or (holding.isin if TICKER.match(holding.isin or "")
                               and not ISIN.match(holding.isin or "") else None)
    if given:
        px, cur = _price(given)
        base = _in_base(px, cur, holding.currency)
        if base is not None:
            if holding.broker_price:
                dev = abs(base - holding.broker_price) / holding.broker_price
                return _row(holding, given, cur, round(px, 4),
                            round(dev * 100, 2), "ok" if dev <= TOLERANCE else "check")
            return _row(holding, given, cur, round(px, 4), "", "unverified")

    matches = []
    for sym in _candidates(holding.isin, holding.name):
        px, cur = _price(sym)
        base = _in_base(px, cur, holding.currency)
        if base is None:
            continue
        matches.append((sym, px, cur, base))

    if not matches:
        return _row(holding)

    # No broker price: take the deepest candidate, then currency match.
    if not holding.broker_price:
        sym, px, cur, _ = max(matches, key=lambda m: (
            min(_depth(m[0]), SUFFICIENT_HISTORY), m[2] == holding.currency))
        return _row(holding, sym, cur, round(px, 4), "", "unverified")

    matches = [(sym, px, abs(base - holding.broker_price) / holding.broker_price,
                cur) for sym, px, cur, base in matches]

    # If none verifies, report the closest (status `check`).
    verified = [m for m in matches if m[2] <= TOLERANCE]
    if not verified:
        best = min(matches, key=lambda m: m[2])
    else:
        closest = min(m[2] for m in verified)
        tied = [m for m in verified if m[2] <= closest + PRICE_NOISE]
        # Rank by depth, then currency preference, then price closeness.
        best = max(tied, key=lambda m: (min(_depth(m[0]), SUFFICIENT_HISTORY),
                                        prefer_currency and m[3] == holding.currency,
                                        -m[2]))

    sym, px, dev, cur = best
    return _row(holding, sym, cur, round(px, 4), round(dev * 100, 2),
                "ok" if dev <= TOLERANCE else "check")
