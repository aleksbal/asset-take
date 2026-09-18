"""A plain CSV of holdings, for brokers without a dedicated adapter.

Accepts the columns most exports share, under common English and German names.
Unlike a broker-specific adapter it supplies no `broker_price`, so ticker
resolution cannot be verified against the broker's own valuation.

This is a fallback: it is consulted only after every specific adapter has
declined, since it would otherwise claim files they parse better.
"""
import csv
from pathlib import Path

from canonical import Holding
from portfolio_monitor import parse_number

# Column aliases, lowercased; first match wins.
ALIASES = {
    "isin": ("isin", "wkn"),
    "ticker": ("ticker", "symbol"),
    "name": ("name", "wertpapiername", "security", "bezeichnung", "instrument"),
    "quantity": ("quantity", "qty", "shares", "stück", "stuck", "anzahl",
                 "nominale", "stück/nominale", "units"),
    "avg_cost": ("avg_cost", "average_cost", "cost", "einstandskurs", "kaufkurs",
                 "buy_price", "price_paid", "purchase_price"),
    "currency": ("currency", "währung", "wahrung", "ccy"),
}


def _norm(name):
    return (name or "").strip().lower().lstrip("﻿")


def _map_columns(fieldnames):
    present = {_norm(f): f for f in (fieldnames or [])}
    found = {}
    for ours, names in ALIASES.items():
        for candidate in names:
            if candidate in present:
                found[ours] = present[candidate]
                break
    return found


def _sniff(path):
    """Return (delimiter, columns, encoding), or (None, None, None)."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            head = Path(path).read_text(encoding=encoding).splitlines()[:1]
        except (UnicodeDecodeError, OSError):
            continue
        if not head:
            return None, None, None
        for delimiter in (",", ";", "\t"):
            cols = _map_columns(next(csv.reader(head, delimiter=delimiter), []))
            if "quantity" in cols and ("isin" in cols or "ticker" in cols):
                return delimiter, cols, encoding
    return None, None, None


def detect(path):
    if Path(path).suffix.lower() not in (".csv", ".tsv", ".txt"):
        return False
    return _sniff(path)[0] is not None


def parse(path):
    delimiter, cols, encoding = _sniff(path)
    if delimiter is None:
        return []

    def cell(row, key):
        return (row.get(cols[key]) or "").strip() if key in cols else ""

    holdings = []
    with open(path, encoding=encoding, newline="") as f:
        for row in csv.DictReader(f, delimiter=delimiter):
            qty = parse_number(cell(row, "quantity"))
            if not qty:
                continue
            isin, ticker = cell(row, "isin"), cell(row, "ticker")
            holdings.append(Holding(
                isin=isin or ticker,
                name=cell(row, "name") or ticker or isin,
                quantity=qty,
                avg_cost=parse_number(cell(row, "avg_cost")) or None,
                currency=(cell(row, "currency") or "EUR").upper(),
                broker_price=None,   # a plain CSV states no valuation
                source="generic",
            ))
    return holdings
