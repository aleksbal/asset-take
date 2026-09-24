#!/usr/bin/env python3
"""Archive a dated asset-take valuation for day-over-day and drift analysis.

Reuses portfolio_monitor's pricing; stores the value series its --history
file does not (that only tracks consecutive-move direction for alerts).
"""
import dataclasses, importlib.util, json, sys
from datetime import datetime
from pathlib import Path

import paths
from market import prices as price_history
from market import volume as volume_history

HERE = Path(__file__).parent

spec = importlib.util.spec_from_file_location("pm", HERE / "portfolio_monitor.py")
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)

# When the prices were taken, not when the file is written. A run whose
# fetches crawl through network timeouts can finish after midnight, and dating
# it then files one day's valuation under the next.
taken_at = datetime.now().astimezone()
today = taken_at.date().isoformat()

positions_csv = sys.argv[1] if len(sys.argv) > 1 else paths.POSITIONS
positions = pm.fetch_prices(pm.load_portfolio(str(positions_csv)))
settings = pm.load_config()
base, alerts = settings.base_currency, settings.alerts
# Both the listings' currencies and the cost bases': they need not match any
# more, and a cost basis without a rate is a P&L silently missing.
fx = pm.fetch_fx_rates(pm.currencies(positions), base)
report = pm.calculate_report(positions, base, fx, alerts)

out = paths.HISTORY
path = out / f"{today}.json"
# A run that valued nothing measured nothing. Written, it is a portfolio worth
# 0.00 with a change of +0.00% - and it replaces any snapshot taken earlier
# the same day, which cannot be taken again. A partial one is still kept: it
# says what it could not value, and the dashboard reports that.
valued = any(p.current_price is not None for p in report.positions)
if valued:
    path.write_text(json.dumps({
        "date": today,
        "taken_at": taken_at.isoformat(timespec="seconds"),
        "base_currency": base,
        "total_value": report.total_value,
        "daily_change": report.daily_change,
        "daily_change_pct": report.daily_change_pct,
        "fx_rates": report.fx_rates,
        "alerts": report.alerts,
        "positions": [dataclasses.asdict(p) for p in report.positions],
    }, indent=2, default=str))
# Per-listing price series: seed any we hold none for, then extend with
# its settled closes. Independent of the snapshot above, which records the account.
# Every position is passed, priced or not: a listing the download missed is
# still seeded. What is recorded is every settled close the download held,
# not the live price the snapshot is valued at.
seeded, recorded, rescaled = price_history.update(
    {p.ticker: p.closes for p in report.positions},
    units={p.ticker: p.quote_currency for p in report.positions if p.quote_currency})
vol_seeded, vol_recorded = volume_history.update(
    {p.ticker: p.volumes for p in report.positions})

if valued:
    print(f"{path}  total {report.total_value:,.2f} {base}  "
          f"({report.daily_change_pct:+.2f}%)  [{len(list(out.glob('*.json')))} days]")
else:
    print(f"no position could be valued; {path.name} not written", file=sys.stderr)
if seeded:
    print(f"price history: seeded {seeded} listing(s) from the provider")
if rescaled:
    print(f"price history: re-seeded {rescaled} series after a likely corporate action")
print(f"price history: {recorded} close(s) recorded in {paths.PRICES}")
if vol_seeded:
    print(f"volume history: seeded {vol_seeded} listing(s) from the provider")
print(f"volume history: {vol_recorded} session(s) recorded in {paths.VOLUMES}")
if not valued:
    sys.exit(1)
