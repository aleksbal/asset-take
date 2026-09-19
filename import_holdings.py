#!/usr/bin/env python3
"""Ingest a broker export -> holdings.csv (canonical) + positions.csv (for pricing).

    ./.venv/bin/python import_holdings.py                  # newest CSV in imports/
    ./.venv/bin/python import_holdings.py <file-or-folder>

Filenames are irrelevant: adapters identify their own format by content.
"""
import csv
import sys
from pathlib import Path

import adapters
import canonical
import paths
import portfolio_monitor as pm
import resolve as rz

ROOT = Path(__file__).parent


def _de(x):
    """Format a number the way portfolio_monitor parses it.

    It applies German notation unconditionally (`str.replace('.','')`), so a
    plain `30.0` is read as 300. Whole numbers are written without a decimal
    part; fractions use a comma. Do not "fix" this to plain floats.
    """
    if x is None or x == "":
        return ""
    if float(x) == int(float(x)):
        return str(int(float(x)))
    return repr(float(x)).replace(".", ",")


POSITION_COLUMNS = ["ticker", "quantity", "currency", "avg_cost",
                    "cost_currency", "quote_currency"]


def position_row(holding, resolved):
    """One positions.csv line for a resolved holding.

    `currency` is the listing's, `cost_currency` the broker's. They are
    separate columns because they are separate facts and need not agree.

    A mismatch used to drop the cost basis entirely, on the reasoning that it
    could not be compared against the wrong unit. It can: it converts. The
    drop is what turned the listing's currency into a selection criterion,
    which is how a listing with one day of history came to be chosen over one
    with 251 and had to be corrected by hand.
    """
    return [resolved["ticker"], _de(holding.quantity), resolved["currency"],
            _de(holding.avg_cost), holding.currency,
            resolved.get("quote_currency", "")]


def pick_file(arg):
    p = Path(arg).expanduser() if arg else paths.IMPORTS
    if p.is_dir():
        # Must match what adapters accept, or a supported export is reported absent.
        found = sorted((f for f in p.iterdir()
                        if f.suffix.lower() in adapters.generic.SUFFIXES),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        if not found:
            exts = ", ".join(adapters.generic.SUFFIXES)
            sys.exit(f"no {exts} files in {p}")
        return found[0]
    if not p.exists():
        sys.exit(f"not found: {p}")
    return p


def main():
    path = pick_file(sys.argv[1] if len(sys.argv) > 1 else None)
    adapter = adapters.detect(path)
    if adapter is None:
        sys.exit(f"no adapter recognises {path.name}. Supported: "
                 f"{', '.join(a.__name__.split('.')[-1] for a in adapters.ADAPTERS)}")

    holdings = adapter.parse(path)
    print(f"{path.name}: {adapter.__name__.split('.')[-1]} adapter, {len(holdings)} holdings")

    total = sum(h.quantity * h.broker_price for h in holdings if h.broker_price)
    print(f"broker valuation: {total:,.2f}")

    canonical.write(holdings, paths.HOLDINGS)

    cached = rz.load_map()
    prefer = pm.load_config().prefer_quote_currency
    rows, resolved = {}, []
    for h in holdings:
        row = rz.resolve(h, cached.get(h.isin), prefer_currency=prefer)
        rows[h.isin] = row
        if row["ticker"] and row["status"] in ("ok", "manual", "unverified"):
            resolved.append((h, row["ticker"]))
        print(f"  {h.isin}  {h.name[:26]:<26} -> {row['ticker'] or '—':<10} "
              f"{row['status']:<10} {str(row['deviation_pct']) + '%' if row['deviation_pct'] != '' else ''}")
    rz.fill_display_names(rows)
    rz.save_map(rows)

    with paths.POSITIONS.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(POSITION_COLUMNS)
        for h, _ in resolved:
            w.writerow(position_row(h, rows[h.isin]))

    bad = [r for r in rows.values() if r["status"] not in ("ok", "manual", "unverified")]
    unverified = [r for r in rows.values() if r["status"] == "unverified"]
    print(f"\nholdings.csv: {len(holdings)}   positions.csv: {len(resolved)} priceable")
    if unverified:
        print(f"{len(unverified)} unverified — this source states no valuation, so the "
              f"ticker could not be checked against it. Confirm they are the right "
              f"instruments:")
        for r in unverified:
            print(f"  {r['isin']:<14} {r['name'][:30]:<30} -> {r['ticker']}")
    if bad:
        print(f"{len(bad)} need attention — fix the ticker in isin_map.csv and set status=manual:")
        for r in bad:
            print(f"  {r['isin']}  {r['name'][:30]:<30} {r['status']}"
                  f"{'  ' + str(r['deviation_pct']) + '% off broker price' if r['deviation_pct'] != '' else ''}")


if __name__ == "__main__":
    main()
