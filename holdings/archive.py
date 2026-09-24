"""Reads the daily snapshots and the holdings on file.

Snapshots store quantities, prices and FX rates; totals, weights and P&L are
recomputed from them on every read.
"""
import csv
import json

import paths
from holdings import canonical


def snapshots():
    """Every daily snapshot, oldest first."""
    return [json.loads(f.read_text()) for f in sorted(paths.HISTORY.glob("*.json"))]


def held():
    """The holdings from holdings.csv, keyed by the ticker each resolved to in
    isin_map.csv. Returns (by_ticker, display_names).
    """
    by_isin = {h.isin: h for h in canonical.read(paths.HOLDINGS)}
    mapped = {r["isin"]: r for r in csv.DictReader(paths.ISIN_MAP.open())}
    by_ticker = {r["ticker"]: by_isin[i]
                 for i, r in mapped.items() if i in by_isin and r["ticker"]}
    names = {r["ticker"]: (r.get("display_name") or "").strip()
             for r in mapped.values() if r.get("ticker")}
    return by_ticker, names
