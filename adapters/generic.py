"""A plain CSV of holdings, for brokers without a dedicated adapter.

Accepts the columns most exports share, under common English and German names.
Unlike a broker-specific adapter it supplies no `broker_price`, so ticker
resolution cannot be verified against the broker's own valuation.

This is a fallback: it is consulted only after every specific adapter has
declined, since it would otherwise claim files they parse better.
"""
import csv
import re
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


SUFFIXES = (".csv", ".tsv", ".txt")

#: A thousands group is always exactly three digits, so a separator followed
#: by any other count can only be a decimal point.
_GROUPED = re.compile(r"^\d{1,3}([.,]\d{3})+$")
_DECIMAL = re.compile(r"^\d+([.,])\d{1,2}$|^\d+([.,])\d{4,}$")


def detect(path):
    if Path(path).suffix.lower() not in SUFFIXES:
        return False
    return _sniff(path)[0] is not None


def _infer_decimal_sep(values):
    """Decide the file's numeric locale from all of its numbers at once.

    The delimiter cannot settle this - CSV quoting allows a comma inside a
    field, so a comma-delimited file may legitimately carry "12,34". A single
    value often cannot settle it either: "1,234" is 1234 in English and 1.234
    in German. Across a whole file there is usually evidence, and applying one
    decision consistently beats guessing per value.
    """
    votes = {".": 0, ",": 0}
    for v in values:
        v = (v or "").strip()
        if not v:
            continue
        if "," in v and "." in v:
            votes["." if v.rfind(".") > v.rfind(",") else ","] += 3
            continue
        m = _DECIMAL.match(v)
        if m:                       # 1 or 2 or 4+ trailing digits: not a group
            votes[m.group(1) or m.group(2)] += 2
            continue
        if _GROUPED.match(v):       # 1.234.567 - repeated groups
            if v.count(".") > 1 or v.count(",") > 1:
                votes["," if "." in v else "."] += 2
    if votes["."] == votes[","]:
        return None                 # no evidence either way; leave it to the heuristic
    return "." if votes["."] > votes[","] else ","


def parse(path):
    delimiter, cols, encoding = _sniff(path)
    if delimiter is None:
        return []

    def cell(row, key):
        return (row.get(cols[key]) or "").strip() if key in cols else ""

    with open(path, encoding=encoding, newline="") as f:
        rows = list(csv.DictReader(f, delimiter=delimiter))

    numeric = [cell(r, k) for r in rows for k in ("quantity", "avg_cost") if k in cols]
    decimal_sep = _infer_decimal_sep(numeric)

    def number(row, key):
        return parse_number(cell(row, key), decimal_sep=decimal_sep)

    holdings = []
    for row in rows:
        qty = number(row, "quantity")
        if not qty:
            continue
        isin, ticker = cell(row, "isin"), cell(row, "ticker")
        if not (isin or ticker):
            continue   # a subtotal or footer row: a quantity naming no instrument
        holdings.append(Holding(
            isin=isin or ticker,
            ticker=ticker or None,   # an explicit ticker beats searching the ISIN
            name=cell(row, "name") or ticker or isin,
            quantity=qty,
            avg_cost=number(row, "avg_cost") or None,
            currency=(cell(row, "currency") or "EUR").upper(),
            broker_price=None,   # a plain CSV states no valuation
            source="generic",
        ))
    return holdings
