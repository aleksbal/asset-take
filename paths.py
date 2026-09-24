"""Where instance data lives.

Code and data are strictly separated: everything under DATA is private
(holdings, cost bases, valuations) and is never committed. Override with
ASSET_TAKE_DATA to keep it outside the working copy entirely.
"""
import os
from pathlib import Path

ROOT = Path(__file__).parent
DATA = Path(os.environ.get("ASSET_TAKE_DATA", ROOT / "data"))

IMPORTS = DATA / "imports"       # broker exports you drop in
HOLDINGS = DATA / "holdings.csv"  # canonical, generated
POSITIONS = DATA / "positions.csv"  # pricing input, generated
ISIN_MAP = DATA / "isin_map.csv"  # your corrections — the one you edit
HISTORY = DATA / "history"        # daily snapshots
PRICES = DATA / "prices"          # per-listing price series
VOLUMES = DATA / "volumes"        # per-listing daily volume series
DASHBOARD = DATA / "dashboard.html"
CONFIG = DATA / "config.json"   # base currency, alert thresholds, preferences

for d in (DATA, IMPORTS, HISTORY, PRICES, VOLUMES):
    d.mkdir(parents=True, exist_ok=True)

REPORT = DATA / "report.json"   # the report the dashboard renders
