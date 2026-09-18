"""Canonical holding representation. Every adapter produces these."""
import csv
from dataclasses import dataclass, asdict, fields
from typing import Optional


@dataclass
class Holding:
    isin: str
    name: str
    quantity: float
    avg_cost: float          # per share, in `currency`
    currency: str
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


def read(path):
    with open(path, encoding="utf-8") as f:
        out = []
        for r in csv.DictReader(f):
            out.append(Holding(
                isin=r["isin"], name=r["name"],
                quantity=float(r["quantity"]), avg_cost=float(r["avg_cost"]),
                currency=r["currency"],
                broker_price=float(r["broker_price"]) if r.get("broker_price") else None,
                broker_as_of=r.get("broker_as_of") or None,
                venue=r.get("venue") or None, source=r.get("source") or None,
            ))
        return out
