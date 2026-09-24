#!/usr/bin/env python3
"""Takes today's valuation and saves it to history/<date>.json, then extends
the per-listing price and volume series.

Exits 1 without writing a snapshot if no position could be valued.
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

# The snapshot is dated by when the run started.
taken_at = datetime.now().astimezone()
today = taken_at.date().isoformat()

positions_csv = sys.argv[1] if len(sys.argv) > 1 else paths.POSITIONS
positions = pm.fetch_prices(pm.load_portfolio(str(positions_csv)))
settings = pm.load_config()
base, alerts = settings.base_currency, settings.alerts
# Rates for both listing and cost-basis currencies.
fx = pm.fetch_fx_rates(pm.currencies(positions), base)
report = pm.calculate_report(positions, base, fx, alerts)

out = paths.HISTORY
path = out / f"{today}.json"
# No snapshot is written if nothing could be valued; a partial one is.
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
# Per-listing series: every position is seeded; its settled closes and
# volumes from the download are recorded.
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
