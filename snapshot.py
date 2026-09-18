#!/usr/bin/env python3
"""Archive a dated asset-take valuation for day-over-day and drift analysis.

Reuses portfolio_monitor's pricing; stores the value series its --history
file does not (that only tracks consecutive-move direction for alerts).
"""
import dataclasses, importlib.util, json, sys
from datetime import date, datetime
from pathlib import Path

import paths
import price_history

HERE = Path(__file__).parent

spec = importlib.util.spec_from_file_location("pm", HERE / "portfolio_monitor.py")
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)

positions_csv = sys.argv[1] if len(sys.argv) > 1 else paths.POSITIONS
positions = pm.fetch_prices(pm.load_portfolio(str(positions_csv)))
base, alerts = pm.load_config(None)
fx = pm.fetch_fx_rates({p.currency for p in positions}, base)
report = pm.calculate_report(positions, base, fx, alerts)

out = paths.HISTORY
path = out / f"{date.today().isoformat()}.json"
path.write_text(json.dumps({
    "date": date.today().isoformat(),
    "taken_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "base_currency": base,
    "total_value": report.total_value,
    "daily_change": report.daily_change,
    "daily_change_pct": report.daily_change_pct,
    "fx_rates": report.fx_rates,
    "alerts": report.alerts,
    "positions": [dataclasses.asdict(p) for p in report.positions],
}, indent=2, default=str))
# Per-listing price series: seed any we hold none for, then extend with
# today's close. Independent of the snapshot above, which records the account.
seeded, recorded, rescaled = price_history.update(
    {p.ticker: p.current_price for p in report.positions},
    dates={p.ticker: p.price_date for p in report.positions if p.price_date},
    units={p.ticker: p.quote_currency for p in report.positions if p.quote_currency})

print(f"{path}  total {report.total_value:,.2f} {base}  "
      f"({report.daily_change_pct:+.2f}%)  [{len(list(out.glob('*.json')))} days]")
if seeded:
    print(f"price history: seeded {seeded} listing(s) from the provider")
if rescaled:
    print(f"price history: re-seeded {rescaled} series after a likely corporate action")
print(f"price history: {recorded} close(s) recorded in {paths.PRICES}")
