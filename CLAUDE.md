# portfolio

Daily valuation of a stock/ETF portfolio, plus ING cash accounts over FinTS.

## Running things

Always use `./.venv/bin/python` (3.12). System python has neither yfinance nor
fints. Never `pip install` outside the venv.

    ./.venv/bin/python snapshot.py                                   # archive today
    ./.venv/bin/python portfolio_monitor.py -p positions.csv -o report
    FINTS_PRODUCT_ID=<id> ./.venv/bin/python fetch_ing.py --days 30  # cash accounts

## How the pieces relate

`portfolio_monitor.py` started as the bundled `stock-portfolio-monitor` skill
and is now a fork we edit freely. Fix bugs there rather than working around
them downstream. Changes so far: `parse_number()` replaced number parsing that
assumed German notation unconditionally and read `30.0` as `300`.

`snapshot.py` imports it and reuses its pricing functions; it must not fetch
prices itself.

Ingestion is `import_holdings.py`: adapters in `adapters/` identify a broker
export by content (never filename), emit `Holding` objects from `canonical.py`,
and `resolve.py` maps ISIN to a Yahoo ticker. Adding a broker means one new
adapter with `detect()` and `parse()`; nothing else changes.

`broker_price` is optional. A source that states no valuation (the generic CSV
reader) yields `unverified` mappings, which are usable but unchecked. Do not
make it mandatory again - that assumption was baked in until a second adapter
exposed it, and silently resolved every such holding to nothing.

Verification is the point of `broker_price`: the export carries the broker's
own valuation, so a wrong ticker is caught automatically. This already caught
a small-cap ETF being matched to its large-cap namesake. Never accept a
mapping that deviates materially from the broker price.

Prefer a listing whose currency matches the broker's cost basis, so P&L is
computed in one unit. Where no such listing exists (a stock quoted only in USD
against a EUR cost basis), `avg_cost` is written empty rather than wrong.

The two data sources are deliberately separate. Holdings live in
`positions.csv` and are maintained by hand. Cash accounts come from FinTS.
They do not merge, because ING is reported not to serve Depot data over FinTS
(`HKWPD` unsupported). If `fetch_ing.py` ever prints `GET_HOLDINGS: yes`, that
assumption is wrong and positions could be fetched instead — worth checking on
the first successful login.

## positions.csv

Columns: `ticker,quantity,currency,avg_cost`. The ticker must be the
exchange-suffixed Yahoo symbol (`IWDA.AS` Amsterdam, `VWCE.DE` Xetra), not an
ISIN — the same fund on two exchanges has two tickers and two prices. Mapping
ISIN to ticker is a manual step; there is no lookup.

`portfolio_monitor.py` also accepts `isin` as a ticker fallback and parses
German decimals (`1.234,56`), so an ING Depot export needs little reshaping.

All instance data lives under `data/` (override with `ASSET_TAKE_DATA`) and is
gitignored in full — holdings, valuations, broker exports and snapshots never
enter version control. Paths come from `paths.py`; never hardcode one.

`positions.csv` is **generated** by `import_holdings.py`. Hand edits are
overwritten; corrections belong in `isin_map.csv`, where `status=manual`
pins an entry against re-resolution.

## history/

One JSON per day: values, weights, FX rates. Gitignored. This series is the
only thing that makes day-over-day and drift analysis possible, and it cannot
be backfilled — a missed day is gone. Read several files, not just today's.

Do not confuse it with `portfolio_monitor.py --history`, which is unrelated:
that file tracks only consecutive-move direction for alerts.

## Credentials

`fetch_ing.py` prompts for the Zugangsnummer and PIN at runtime and writes
neither to disk. `.fints_state.bin` holds only the FinTS system id. Do not add
credential arguments, env vars or config entries for the PIN.

FinTS needs a product ID registered with the Deutsche Kreditwirtschaft; the
client refuses to construct without one. Do not substitute a public or
borrowed ID — they get deactivated.
