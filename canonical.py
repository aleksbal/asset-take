"""Canonical holding representation. Every adapter produces these."""
import csv
from dataclasses import dataclass, asdict, fields
from typing import Optional


@dataclass
class Holding:
    isin: str
    name: str
    quantity: float
    currency: str
    avg_cost: Optional[float] = None   # per share, in `currency`; absent in
                                       # sources that do not state a cost basis
    broker_price: Optional[float] = None   # broker's own valuation, per share
    broker_as_of: Optional[str] = None
    venue: Optional[str] = None
    source: Optional[str] = None           # which adapter produced this


COLUMNS = [f.name for f in fields(Holding)]


def write(holdings, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for h in holdings:
            w.writerow(asdict(h))


def _opt_float(value):
    """Blank means absent, not zero. A holding may state no cost basis, and a
    zero there would silently read as a 100% gain."""
    value = (value or "").strip()
    return float(value) if value else None


def read(path):
    with open(path, encoding="utf-8") as f:
        out = []
        for r in csv.DictReader(f):
            out.append(Holding(
                isin=r["isin"], name=r["name"],
                quantity=float(r["quantity"]),
                currency=r["currency"],
                avg_cost=_opt_float(r.get("avg_cost")),
                broker_price=_opt_float(r.get("broker_price")),
                broker_as_of=r.get("broker_as_of") or None,
                venue=r.get("venue") or None, source=r.get("source") or None,
            ))
        return out
