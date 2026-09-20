"""Read what was recorded: the daily snapshots and the holdings beside them.

A snapshot stores what was measured - quantities, prices, the rates in force
- and never a figure worked out from them. Totals, weights and P&L are
recomputed on every read, so a correction to the conversion logic repairs
the whole series rather than only the days after it.
"""
import csv
import json

import paths
from holdings import canonical


def snapshots():
    """Every daily snapshot, oldest first."""
    return [json.loads(f.read_text()) for f in sorted(paths.HISTORY.glob("*.json"))]


def held():
    """The holdings on file, keyed by the ticker they resolved to.

    Two files, because they answer different questions: holdings.csv is what
    the broker says you own, and isin_map.csv is which listing each one was
    matched to. Returns (by_ticker, display_names).
    """
    by_isin = {h.isin: h for h in canonical.read(paths.HOLDINGS)}
    mapped = {r["isin"]: r for r in csv.DictReader(paths.ISIN_MAP.open())}
    by_ticker = {r["ticker"]: by_isin[i]
                 for i, r in mapped.items() if i in by_isin and r["ticker"]}
    names = {r["ticker"]: (r.get("display_name") or "").strip()
             for r in mapped.values() if r.get("ticker")}
    return by_ticker, names
