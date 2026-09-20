"""ING (Germany) 'Depotübersicht' CSV export.

Owns every quirk of that format so nothing leaks into the canonical layer:
cp1252 encoding, a five-line preamble, a Depot-Gesamtwert trailer row,
four columns all named 'Währung', and German decimal notation.
"""
import csv
import re
from pathlib import Path

from holdings.canonical import Holding

ENCODING = "cp1252"
HEADER_SIGNATURE = ("ISIN", "Wertpapiername")

# Header columns repeat ('Währung' x4), so parse positionally.
ISIN, NAME, QTY, _UNIT, AVG_COST, CURRENCY = 0, 1, 2, 3, 4, 5
_VALUE, _CUR2, PRICE, _CUR3, TIME, VENUE = 6, 7, 8, 9, 10, 11


def _lines(path):
    return Path(path).read_text(encoding=ENCODING, errors="replace").splitlines()


def detect(path):
    if Path(path).suffix.lower() != ".csv":
        return False
    for line in _lines(path)[:15]:
        cells = line.split(";")
        if len(cells) > 6 and cells[0] == HEADER_SIGNATURE[0] and cells[1] == HEADER_SIGNATURE[1]:
            return True
    return False


def _num(s):
    """German decimal: '6.177,79' -> 6177.79, '205,9263' -> 205.9263."""
    s = (s or "").strip().replace("\xa0", "").replace(".", "").replace(",", ".")
    return float(s) if s else None


def _as_of(path, time_cell):
    """Combine the report date from line 1 with a row's 'HH:MM Uhr'."""
    head = _lines(path)[0] if _lines(path) else ""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", head)
    date = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None
    t = re.search(r"(\d{1,2}:\d{2})", time_cell or "")
    if date and t:
        return f"{date}T{t.group(1)}"
    return date


def parse(path):
    rows = list(csv.reader(_lines(path), delimiter=";"))
    start = next(i for i, r in enumerate(rows)
                 if len(r) > 1 and r[0] == HEADER_SIGNATURE[0] and r[1] == HEADER_SIGNATURE[1])

    holdings = []
    for r in rows[start + 1:]:
        if len(r) <= PRICE:
            continue
        isin = r[ISIN].strip().strip('"')
        # ISINs are 2 letters + 10 alphanumerics; the trailer row has none.
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", isin):
            continue
        holdings.append(Holding(
            isin=isin,
            name=r[NAME].strip(),
            quantity=_num(r[QTY]),
            avg_cost=_num(r[AVG_COST]),
            currency=(r[CURRENCY] or "EUR").strip(),
            broker_price=_num(r[PRICE]),
            broker_as_of=_as_of(path, r[TIME] if len(r) > TIME else ""),
            venue=r[VENUE].strip() if len(r) > VENUE else None,
            source="ing",
        ))
    return holdings
