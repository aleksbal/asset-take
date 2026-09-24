"""Builds report.json from the latest snapshot, the holdings file and the
per-listing series.

One row per instrument, with its price and parameter values. A row has a
`position` block only for instruments that are held.
"""
import json
import math

import paths
from holdings import archive
from market import indicators as ix
from market import prices as price_history
from market import volume as volume_history
from market.money import Converted, Money


def mismatch(snap, by_ticker):
    """Differences between a snapshot's positions and the holdings on file.

    Compares tickers, quantities, cost bases and (where the snapshot records
    one) cost currencies. Returns (gone, added, changed); all empty when they
    agree.
    """
    was = {p["ticker"]: p for p in snap["positions"]}
    gone = sorted(set(was) - set(by_ticker))
    added = sorted(set(by_ticker) - set(was))
    changed = []
    for ticker in sorted(set(was) & set(by_ticker)):
        p, h = was[ticker], by_ticker[ticker]
        if not _agrees(p.get("quantity"), h.quantity):
            changed.append(f"{ticker} quantity "
                           f"{_shown(p.get('quantity'))} -> {_shown(h.quantity)}")
        if not _agrees(p.get("avg_cost"), h.avg_cost):
            changed.append(f"{ticker} cost "
                           f"{_shown(p.get('avg_cost'))} -> {_shown(h.avg_cost)}")
        # Older snapshots have no cost_currency; skip the check for them.
        if p.get("cost_currency") and p["cost_currency"] != h.currency:
            changed.append(f"{ticker} cost currency "
                           f"{p['cost_currency']} -> {h.currency}")
    return gone, added, changed


def _agrees(a, b):
    """Whether two optional numbers are equal; None equals only None."""
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-9)


def _shown(value):
    return "—" if value is None else f"{value:g}"


def _stale(snap, gone, added, changed):
    out = [f"holdings have changed since the last snapshot ({snap['date']}):"]
    if gone:
        out.append(f"  no longer held: {', '.join(gone)}")
    if added:
        out.append(f"  newly held:     {', '.join(added)}")
    out += [f"  changed:        {c}" for c in changed]
    out += ["", "Run snapshot.py to value what you hold now. Rendering the old "
            "snapshot instead", "would meet its quantities with today's cost "
            "basis and report a P&L neither states."]
    return "\n".join(out)


def complete(snap):
    """Whether every position in the snapshot has a price."""
    return all(p.get("current_price") is not None for p in snap["positions"])


def comparable(snaps):
    """The snapshots that can be plotted together: the most recent run with the
    latest snapshot's base currency, keeping only complete ones.
    """
    base = snaps[-1].get("base_currency", "EUR")
    run = []
    for snap in reversed(snaps):
        if snap.get("base_currency", "EUR") != base:
            break
        run.append(snap)
    return [s for s in reversed(run) if complete(s)]

def _series(ticker, price=None, on=None):
    """Parameter values for one listing, or {} if it has no stored series.

    `price` is today's live price, dated `on`. It is used in the calculation
    only if it is newer than the last stored close, and is never stored.
    """
    series = [(day, close) for day, (close, _) in
              price_history.load(ticker).items()]
    volumes = [(day, vol) for day, (vol, _) in
              volume_history.load(ticker).items()]
    if not series and not volumes:
        return {}
    # Use the live price only if it is newer than the last stored close.
    session = str(on or "")
    if not series or price is None or not session or session <= str(max(
            day for day, _ in series)):
        price = session = None
    return {**ix.inputs(series, live=price, live_on=session),
            **ix.values(series, live=price, live_on=session, volumes=volumes)}


def _converted(amount, currency, rates, base):
    """`amount` in `base` at the snapshot's recorded rate as a `Converted`,
    or None without a rate."""
    if amount is None or not currency:
        return None
    rate = rates.get(currency)
    return None if rate is None else Converted(Money(amount, currency), rate, base)


def _cost_of(p, h, rates, base):
    """The position's cost basis in `base`, or None if it has none or its
    rate is missing.

    Uses the snapshot's `cost_currency`, falling back to the listing currency
    for snapshots that predate that field.
    """
    if not (h and h.avg_cost):
        return None
    return _converted(h.quantity * h.avg_cost,
                      p.get("cost_currency") or p.get("currency"), rates, base)

def rows(snap, by_ticker, names):
    """One row per position in the snapshot, largest value first.

    An unpriced position gets `priced: False` and only its quantity.
    `cost_unconverted` marks a cost basis whose currency has no rate.
    """
    out = []
    total = snap["total_value"]
    base = snap.get("base_currency", "EUR")
    rates = snap["fx_rates"]
    for p in snap["positions"]:
        h = by_ticker.get(p["ticker"])
        row = {
            "ticker": p["ticker"],
            "name": names.get(p["ticker"]) or (h.name if h else p["ticker"]),
            "price": (None if p.get("current_price") is None else
                      {"amount": p["current_price"], "currency": p["currency"]}),
            # Parameters, measured against the live price dated by its session.
            "values": _series(p["ticker"], price=p.get("current_price"),
                              on=p.get("price_date") or snap.get("date")),
        }
        value = _converted(p["quantity"] * (p.get("current_price") or 0),
                           p["currency"], rates, base)
        if p.get("current_price") is None or value is None:
            row["priced"] = False
            row["cost_unconverted"] = False
            row["position"] = {"quantity": p["quantity"]}
            out.append(row)
            continue
        previous = _converted(
            p["quantity"] * (p.get("previous_close") or p["current_price"]),
            p["currency"], rates, base)
        cost = _cost_of(p, h, rates, base)
        value, prev = value.amount, previous.amount if previous else None
        row["priced"] = True
        row["cost_unconverted"] = bool(h and h.avg_cost) and cost is None
        row["position"] = {
            "quantity": p["quantity"],
            "value": value,
            "weight": value / total * 100,
            "day": (value - prev) if prev is not None else None,
            "day_pct": (value / prev - 1) * 100 if prev else 0,
            "pnl": (value - cost.amount) if cost and cost.amount else None,
            "pnl_pct": ((value / cost.amount - 1) * 100) if cost and cost.amount
                       else None,
        }
        out.append(row)
    out.sort(key=lambda r: (r["position"].get("value") is None,
                            -(r["position"].get("value") or 0)))
    return out


def _held(row):
    return row.get("position") or {}


def load():
    """The snapshots, the held positions by ticker, and display names."""
    by_ticker, names = archive.held()
    return archive.snapshots(), by_ticker, names


def build():
    """The report as a dict.

    Exits with a message if the latest snapshot does not match the holdings on
    file (see `mismatch`).
    """
    snaps, by_ticker, names = load()
    if not snaps:
        raise SystemExit("no snapshots in history/ - run snapshot.py first")
    latest = snaps[-1]
    gone, added, changed = mismatch(latest, by_ticker)
    if gone or added or changed:
        raise SystemExit(_stale(latest, gone, added, changed))

    table = rows(latest, by_ticker, names)
    series = comparable(snaps)
    base = latest.get("base_currency", "EUR")

    priced = [r for r in table if _held(r).get("pnl") is not None]
    # None, not 0, when no position has a cost basis.
    pnl = sum(_held(r)["pnl"] for r in priced) if priced else None
    cost = sum(_held(r)["value"] - _held(r)["pnl"] for r in priced)
    weights = [_held(r)["weight"] for r in table[:5] if _held(r).get("weight")]

    return {
        "as_of": latest.get("taken_at", latest["date"]),
        "date": latest["date"],
        "base_currency": base,
        "indicators": ix.declared(),
        "totals": {
            "value": latest["total_value"],
            "day": latest["daily_change"],
            "day_pct": latest["daily_change_pct"],
            "pnl": pnl,
            "pnl_pct": (pnl / cost * 100) if pnl is not None and cost else None,
            "positions": len(table),
            "top5_weight": sum(weights),
            "days_recorded": len(snaps),
        },
        "excluded": {
            "unpriced": [r["ticker"] for r in table if not r["priced"]],
            "cost_unconverted": [r["ticker"] for r in table
                                 if r["cost_unconverted"]],
            "no_cost": [r["ticker"] for r in table
                        if r["priced"] and not r["cost_unconverted"]
                        and _held(r).get("pnl") is None],
            "snapshots_not_comparable": len(snaps) - len(series),
        },
        "series": [{"date": s["date"], "value": s["total_value"]}
                   for s in series],
        "rows": table,
    }


def write(report=None, path=None):
    """Write the report to `path` (default: report.json) and return the path."""
    report = build() if report is None else report
    path = path or paths.REPORT
    path.write_text(json.dumps(report, indent=2, default=str),
                    encoding="utf-8")
    return path


if __name__ == "__main__":      # python -m views.portfolio
    print(write())
