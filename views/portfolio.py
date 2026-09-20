"""What is held, valued, with every parameter the series supports.

The output of a run is data. `build()` returns it and `write()` puts it in
report.json; the page is one program that reads that file, and a screen, a
chart or a model are others. Nothing here knows what HTML is.

A row is one instrument. It always carries a price and whatever parameters
could be computed. It carries a `position` block only where something is
held - absent, not zeroed, for anything that is not.
"""
import json
import math

import paths
from holdings import archive
from market import indicators as ix
from market import prices as price_history
from market.money import Converted, Money

def mismatch(snap, by_ticker):
    """How a snapshot's holdings differ from the ones on file, if at all.

    A snapshot is valued on its own quantities, but the cost basis beside it
    is read from holdings.csv. The two must therefore describe the same
    portfolio. They part company whenever an import lands without a snapshot
    after it, and the result does not look wrong: every figure renders, and
    a position valued at 300 shares against the cost of 640 reports a 44%
    loss that neither file states.

    Ticker sets alone do not settle it, and neither do quantities. Everything
    a row is built from must agree, because any one of them moving underneath
    the snapshot produces the same kind of figure: a broker correction or a
    sell-and-rebuy at the same size leaves the quantity equal while the cost
    basis changes, and the P&L is then this snapshot's value against another
    day's cost.

    Returns (gone, added, changed) - all empty when the two agree.
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
        # Only where the snapshot states one. A snapshot written before
        # positions carried a cost currency is silent on it, which is not the
        # same as disagreeing.
        if p.get("cost_currency") and p["cost_currency"] != h.currency:
            changed.append(f"{ticker} cost currency "
                           f"{p['cost_currency']} -> {h.currency}")
    return gone, added, changed


def _agrees(a, b):
    """Whether two optional numbers say the same thing.

    Absent equals absent; absent never equals a number. A cost basis that
    appeared or vanished is a change, not a rounding difference.
    """
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
    """Whether every position in this snapshot was valued.

    Derived rather than stored, so it holds for snapshots written before
    anyone thought to ask: a position whose currency had no rate is left
    unpriced by `calculate_report`, and the snapshot records that faithfully.
    A stored flag would have needed a migration and a rule for what its
    absence meant.
    """
    return all(p.get("current_price") is not None for p in snap["positions"])


def comparable(snaps):
    """The snapshots that can share one line.

    Two things disqualify a point. A different base currency: a total is
    stored in the base that produced it, so plotting them together draws the
    switch as a gain and then labels the old points with the new currency,
    which is the more serious half. And an incomplete valuation: a snapshot
    taken while a rate was down holds the sum of the positions that could be
    valued, so a complete-partial-complete run draws a crash and a recovery
    that never happened.

    The longest suffix rather than every currency match: a base that changed
    and changed back would otherwise splice two runs across the gap between
    them.
    """
    base = snaps[-1].get("base_currency", "EUR")
    run = []
    for snap in reversed(snaps):
        if snap.get("base_currency", "EUR") != base:
            break
        run.append(snap)
    return [s for s in reversed(run) if complete(s)]

def _series(ticker, price=None, on=None):
    """Parameter values for one listing, or {} where there is no series.

    The stored series deliberately excludes the current session, because an
    in-progress close cannot be corrected once written. The report shows
    that live price though, so it is passed in for the calculation only - a
    position that moved sharply today would otherwise display today's price
    beside yesterday's drawdown. Nothing is written back.
    """
    series = [(day, close) for day, (close, _) in
              price_history.load(ticker).items()]
    if not series:
        return {}
    # Only where the quote is newer than everything stored. Regenerating the
    # report without a fresh snapshot would otherwise append a stale price
    # under a new date, inventing a session across whatever gap had passed.
    session = str(on or "")
    if price is None or not session or session <= str(max(
            day for day, _ in series)):
        price = session = None
    return {**ix.inputs(series, live=price, live_on=session),
            **ix.values(series, live=price, live_on=session)}


def _converted(amount, currency, rates, base):
    """`amount` in `base` at a rate the snapshot recorded, or None without one.

    Never a default of 1, which values a foreign amount as a domestic one and
    is indistinguishable from a correct figure on the page.
    """
    if amount is None or not currency:
        return None
    rate = rates.get(currency)
    return None if rate is None else Converted(Money(amount, currency), rate, base)


def _cost_of(p, h, rates, base):
    """A position's cost basis in `base`, or None where it has none.

    Where the snapshot records a cost currency, that is the denomination.
    Where it does not, the snapshot predates the column, and the position's
    own listing currency is the recoverable one: the writer of that era
    emitted a cost basis only when the two agreed, and blanked it otherwise.

    Today's `h.currency` is not a safe substitute. A broker correction that
    changes the holding currency without changing the quantity or the number
    is accepted by `mismatch()` for exactly such a legacy snapshot, and would
    then reinterpret an old cost under a denomination it never had - reporting
    the rate between them as P&L.
    """
    if not (h and h.avg_cost):
        return None
    return _converted(h.quantity * h.avg_cost,
                      p.get("cost_currency") or p.get("currency"), rates, base)

def rows(snap, by_ticker, names):
    """One row per instrument in the snapshot.

    A position may carry no price: the provider can fail for one ticker while
    succeeding for the rest. Such a row states that rather than being valued,
    since a zero would understate the total while looking complete.

    `position` is present only where something is held. A row without it is
    still a row - which is what lets a market-wide view emit the same shape
    for instruments nobody owns.
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
            # The series is in the listing's own currency, not the base, so
            # the unconverted price is the one that belongs beside it.
            # price_date names the session the quote settled in, so where it
            # is set the close is already stored and is not a new
            # observation. A snapshot taken on a weekend is dated later than
            # the close it holds, and would otherwise re-enter it as live.
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
        # avg_cost is denominated in the currency the broker charged in, which
        # need not be the listing's, so it is converted on its own rate rather
        # than the position's. Falling back to the position's rate converted a
        # USD cost basis at the EUR rate and reported the difference as P&L.
        cost = _cost_of(p, h, rates, base)
        value, prev = value.amount, previous.amount if previous else None
        row["priced"] = True
        # States a cost basis, but its currency has no rate. Different from
        # stating none, and the difference is what the reader needs: one is a
        # fact about the holding, the other about our data.
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
    """Everything a report is built from, in one place.

    The snapshots are what was measured; the holdings beside them carry the
    cost basis, which a snapshot does not. Gathered together because the two
    must describe the same portfolio and `build()` checks that they do.
    """
    by_ticker, names = archive.held()
    return archive.snapshots(), by_ticker, names


def build():
    """The whole report, as data.

    Raises rather than returning a blended one: a snapshot valued on its own
    quantities against a cost basis read from today's holdings produces
    figures neither file states, and every one of them renders.
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
    # No position states a cost basis: the gain is unknown, which is not the
    # same as a gain of zero. sum([]) would quietly claim the latter.
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
        # The facts behind the caveats, never the sentences. Which tickers
        # could not be priced is data; how to word it is display.
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
    """Put the report on disk. This is where a run finishes."""
    report = build() if report is None else report
    path = path or paths.REPORT
    path.write_text(json.dumps(report, indent=2, default=str),
                    encoding="utf-8")
    return path


if __name__ == "__main__":
    print(write())
