# asset-take

Local, daily valuation of a stock/ETF portfolio. Reads a broker's own export,
prices it against public market data, and keeps a history you own.

Everything runs on your machine. Your holdings never leave it.

## Current state

**One broker is supported: ING Germany**, via the `Depotübersicht` CSV export
from their online banking. That adapter is complete and verified against ING's
own valuation.

Other brokers need an adapter — see [Adding a broker](#adding-a-broker). The
format-specific work is contained to one file; nothing else changes.

## Why this isn't just reading a CSV

A broker export identifies holdings by **ISIN**. Market data is keyed by
**ticker**, which is exchange-specific: the same fund has a different symbol,
and a different price, on every venue it trades.

Two things make that mapping fail quietly:

**Currency.** A German broker reports your cost basis in EUR. Looking up
`US67066G1040` returns `NVDA` on Nasdaq, priced in USD. Compare the two and
every P&L figure is wrong, with nothing to indicate it.

**Near-misses.** Searching for an ETF's ISIN can return a *different fund with
a similar name*. This actually happened here: a small-cap ETF was matched to
its large-cap namesake — 93% off, in the largest position.

So resolution is verified rather than trusted. The export carries
`Bewertungskurs`, the broker's own current price, and that is used as ground
truth: a candidate ticker whose live price doesn't match the broker's is
rejected, not used. Anything unresolved is reported, never silently dropped.

## Use

Import holdings — when you trade. Drop the export in `data/imports/` under any
filename; adapters recognise their own format by content:

    ./.venv/bin/python import_holdings.py            # newest file in data/imports/
    ./.venv/bin/python import_holdings.py <path>

Snapshot — daily. Each run stores one dated valuation:

    ./.venv/bin/python snapshot.py

Dashboard — value over time, allocation, positions:

    ./.venv/bin/python dashboard.py && open data/dashboard.html

Text report:

    ./.venv/bin/python portfolio_monitor.py -p data/positions.csv -o report

## Code and data are separate

All instance data lives under `data/`, which is gitignored in full — holdings,
cost bases, snapshots, broker exports, the rendered dashboard. None of it is
committable. Set `ASSET_TAKE_DATA` to keep it outside the working copy
entirely.

Paths come from `paths.py`. New code must take them from there; a hardcoded
`Path("holdings.csv")` would write to the repo root and become committable.

| Code | |
|---|---|
| `adapters/` | one module per broker: `detect(path)`, `parse(path)` |
| `canonical.py` | the `Holding` record every adapter produces |
| `resolve.py` | ISIN → ticker, verified against the broker's own price |
| `import_holdings.py` | export → canonical holdings + pricing input |
| `snapshot.py` | one dated valuation per run |
| `dashboard.py` | renders the HTML dashboard |
| `paths.py` | where data lives |

| Data (gitignored) | |
|---|---|
| `data/imports/` | broker exports you drop in |
| `data/isin_map.csv` | **the file you edit** — ticker corrections, `status=manual` pins one |
| `data/holdings.csv` | canonical holdings, generated |
| `data/positions.csv` | pricing input, generated |
| `data/history/` | dated snapshots; the series behind trend analysis |

## Adding a broker

One file in `adapters/` exposing `detect(path) -> bool` and
`parse(path) -> [Holding]`, registered in `adapters/__init__.py`. Detection
reads file content, never the filename.

Everything format-specific belongs inside the adapter. `adapters/ing.py`
absorbs cp1252 encoding, a preamble before the header, a totals row at the
end, four columns all named `Währung`, and German decimal notation — none of
which is visible anywhere else in the codebase.

## Accuracy

Market data won't match a German broker exactly: ING prices on Direkthandel,
its own OTC venue, which public sources don't carry. Expect ~0.1% on the
total. That is well inside what this is for — recognising trends over weeks
and months, not intraday precision.

## Not yet

`fetch_ing.py` is a FinTS client for **cash accounts** — balances and
transactions, not holdings. It is unused and not part of any workflow. FinTS
requires a product ID registered with the Deutsche Kreditwirtschaft, and ING
appears not to serve securities data over it, so holdings would still come
from the export. Kept because the settlement account reveals trades, which
would let position changes be detected automatically.

## Glossary

**ISIN** — International Securities Identification Number. A 12-character code
identifying a security worldwide, e.g. `IE00B4L5Y983`. One per instrument,
regardless of where it trades. What broker exports use.

**Ticker** — the symbol a security trades under *on one exchange*, e.g.
`IWDA.AS`. The suffix is the venue (`.AS` Amsterdam, `.DE` Xetra, `.L`
London). Market data is keyed on this, which is why the mapping exists.

**WKN** — the older 6-character German security ID, still shown by some banks.

**P&L** — profit and loss; here always *unrealized*: current value minus what
you paid, on things you still hold.

**avg_cost** — average price paid per share. **Cost basis** — total paid for a
position (`avg_cost × quantity`).

**Weight** — a position's share of total value. Weights drift as prices move.

**FX** — foreign exchange. For a holding priced in another currency, part of
its move is the exchange rate rather than the asset; snapshots store the rates
so the two stay separable.

**Depot** — German for a securities account. **Verrechnungskonto** — the cash
settlement account attached to it; trades pass through it.
