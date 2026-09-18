"""Resolve ISIN -> Yahoo ticker, using the broker's own price as ground truth.

Yahoo's ISIN search returns a security's primary listing, which is often in a
different currency than a German broker's EUR cost basis. Picking that would
silently corrupt every P&L figure. So candidates are gathered from both the
ISIN and the instrument name, and the one whose live price best matches the
broker's valuation wins.

A listing quoted in another currency is not a different instrument, so prices
are compared converted. Requiring the currencies to match outright discarded
the only listing the provider offered for several holdings, which is what made
hand-pinning necessary in the first place.

The resulting map is a plain CSV. Auto-resolution is wrong sometimes; edit it
by hand and set status=manual, and the entry is kept as-is from then on.
"""
import csv
import re
from pathlib import Path

import yfinance as yf

import paths
import quotes

MAP_PATH = paths.ISIN_MAP
TOLERANCE = 0.05  # fraction by which a candidate may differ from the broker price
# Two listings of one security quote within a few hundredths of a percent of
# each other. Wider than that is a real price difference, not venue noise.
PRICE_NOISE = 0.01
# Depth is a threshold, not a score: past it a listing can carry a trend, and
# preferring 506 days over 505 only swaps one usable listing for another.
# Set below a full trading year on purpose - a listing that missed a few days
# to local holidays is no less usable than one that did not.
SUFFICIENT_HISTORY = 200
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
    """Live price and currency, normalised to the currency's major unit."""
    try:
        fi = yf.Ticker(ticker).fast_info
        return _as_major(float(fi["last_price"]), fi["currency"])
    except Exception:
        return None, None


_FX_CACHE = {}

# Re-exported: the quote unit is shared with the valuation layer, which reads
# its own prices from the provider and must scale them identically.
_as_major = quotes.as_major


def _fx(currency, base):
    """Rate converting one unit of `currency` into `base`, or None.

    None where the rate cannot be had, so the candidate is skipped rather than
    priced at a rate of one - a fabricated rate would put a plausible number
    on the wrong instrument, which is exactly what verification exists to stop.
    """
    if currency == base:
        return 1.0
    key = (currency, base)
    if key not in _FX_CACHE:
        _FX_CACHE[key] = _fx_rate(currency, base)
    return _FX_CACHE[key]


def _fx_rate(currency, base):
    for pair, invert in ((f"{currency}{base}=X", False), (f"{base}{currency}=X", True)):
        try:
            px = float(yf.Ticker(pair).fast_info["last_price"])
            if px:
                return 1.0 / px if invert else px
        except Exception:
            continue
    return None


def _repriced(holding, existing):
    """A pinned row with its price columns refreshed, ticker untouched.

    Pinning otherwise freezes whatever the rejected match had left behind, so
    the row keeps reporting the deviation of the mapping it was created to
    override - a pin reading as 92% off when it is exact.
    """
    row = dict(existing)
    px, cur = _price(row.get("ticker"))
    base = _in_base(px, cur, holding.currency)
    if base is None:
        return row
    row["currency"] = cur
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
        return _repriced(holding, existing)

    # A mapping that still holds is kept. Candidates differ by hundredths of a
    # percent and live prices move, so re-resolving otherwise flips between
    # venues for no gain - and every flip starts the position's price history
    # again under a new symbol. Depth is required too, so the thin listings
    # this preference would otherwise entrench are still replaced.
    prev = (existing or {}).get("ticker")
    if prev and holding.broker_price:
        px, cur = _price(prev)
        base = _in_base(px, cur, holding.currency)
        if base is not None:
            dev = abs(base - holding.broker_price) / holding.broker_price
            if dev <= TOLERANCE and _depth(prev) >= SUFFICIENT_HISTORY:
                return _row(holding, prev, cur, round(px, 4),
                            round(dev * 100, 2), "ok")

    # An explicitly supplied ticker names the listing the holder actually
    # holds; searching the ISIN could return a different venue entirely.
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

    # Nothing to verify against, so currency is the only signal there is: a
    # listing already in the holding's currency is the safer guess.
    if not holding.broker_price:
        sym, px, cur, _ = max(matches, key=lambda m: m[2] == holding.currency)
        return _row(holding, sym, cur, round(px, 4), "", "unverified")

    matches = [(sym, px, abs(base - holding.broker_price) / holding.broker_price,
                cur) for sym, px, cur, base in matches]

    # Taking the closest price outright let a listing with one day of history
    # beat one with two years of it, over a difference of four cents. Where
    # two listings are the same price to within noise, depth decides.
    #
    # The band is deliberately narrow. TOLERANCE exists to catch a mapping to
    # the wrong instrument, not to declare everything under it equivalent - a
    # candidate 4% out is a different security, however much history it has.
    # A candidate outside tolerance is a different instrument, and no
    # preference among the rest should rescue it. Where none verifies, the
    # closest is still reported - flagged, so the mapping can be looked at.
    verified = [m for m in matches if m[2] <= TOLERANCE]
    if not verified:
        best = min(matches, key=lambda m: m[2])
    else:
        closest = min(m[2] for m in verified)
        tied = [m for m in verified if m[2] <= closest + PRICE_NOISE]
        # Same currency first: converting is sound but adds a rate we do not
        # hold historically, so it is a last resort rather than a preference.
        best = max(tied, key=lambda m: (m[3] == holding.currency,
                                        min(_depth(m[0]), SUFFICIENT_HISTORY),
                                        -m[2]))

    sym, px, dev, cur = best
    return _row(holding, sym, cur, round(px, 4), round(dev * 100, 2),
                "ok" if dev <= TOLERANCE else "check")
