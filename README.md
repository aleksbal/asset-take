# asset-take

Local, daily valuation of a stock/ETF portfolio. Reads a broker's own export,
prices it against public market data, and keeps a history you own.

Everything runs on your machine. Your holdings never leave it.

## Current state

**ING Germany** — the `Depotübersicht` CSV export from their online banking.
Complete, and verified against ING's own valuation.

**Any plain CSV** — a fallback reader accepting `isin` or `ticker` plus
`quantity`, and optionally `name`, `avg_cost` and `currency`, under common
English and German column names. Comma, semicolon or tab.

A plain CSV states no valuation, so ticker resolution cannot be checked
against the source. Those mappings are marked `unverified` and listed on
import. An unverified mapping may be the wrong instrument, and nothing
downstream can tell — which is the reason a broker-specific adapter is worth
writing.

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

Report — the run's output. Every figure, and what each parameter means:

    ./.venv/bin/python -m views.portfolio        # writes data/report.json

Dashboard — one program that reads that report. It values what is held, writes
the report, then draws what was written:

    ./.venv/bin/python dashboard.py && open data/dashboard.html
    ./.venv/bin/python dashboard.py --from-report   # draw it, fetch nothing

`--from-report` is what makes a report still usable after the holdings have
moved on. A run refuses to draw a snapshot against holdings that no longer
match it — but a report already written is a finished fact about its own day.

Both of the above, on a schedule — weekdays at 23:00, after the US close:

    bin/install-schedule.sh              # --at 18:30 for another time
    bin/install-schedule.sh --remove

The history series cannot be backfilled, so a day nobody ran the snapshot is
gone. launchd rather than cron, because cron simply skips a run the Mac slept
through. A caught-up run is *not* taken, though: `snapshot.py` dates its
output by the clock, so a Friday job waking on Saturday would file Friday's
closes under Saturday, and that cannot be corrected afterwards. `bin/daily.sh`
runs only inside its own slot and logs why it declined. Output goes to
`data/logs/daily.log`.

Text report:

    ./.venv/bin/python portfolio_monitor.py -p data/positions.csv -o report

## Tests

    ./.venv/bin/python -m pytest

The ING fixture in `tests/fixtures/` is synthetic — the format's quirks with
invented holdings. No real portfolio data is in this repository.

## Code and data are separate

All instance data lives under `data/`, which is gitignored in full — holdings,
cost bases, snapshots, broker exports, the rendered dashboard. None of it is
committable. Set `ASSET_TAKE_DATA` to keep it outside the working copy
entirely.

Paths come from `paths.py`. New code must take them from there; a hardcoded
`Path("holdings.csv")` would write to the repo root and become committable.

Code sits in four layers, and each may only use the ones below it:

    render/     the page, and nothing else that knows what HTML is
    views/      a question -> rows
    holdings/   facts about what is owned
    market/     facts about instruments

`tests/test_layers.py` fails on an import pointing the wrong way, and on
markup below `render/`. `market/` never learns that a portfolio exists, which
is what will let a market-wide view reuse it unchanged.

| Code | |
|---|---|
| `market/quotes.py` `money.py` `fx.py` | a price with its unit; an amount and a converted one; rates |
| `market/prices.py` | one close series per listing |
| `market/trends.py` | the maths: drawdown, moving averages, RSI |
| `market/indicators.py` | **one entry per parameter** — see below |
| `holdings/adapters/` | one module per broker: `detect(path)`, `parse(path)` |
| `holdings/canonical.py` | the `Holding` record every adapter produces |
| `holdings/resolve.py` | ISIN → ticker, verified against the broker's own price |
| `holdings/archive.py` | reads the snapshots and the holdings beside them |
| `views/portfolio.py` | what is held, valued → `report.json`; `python -m views.portfolio` |
| `render/html.py` | `report.json` → the page |
| `import_holdings.py` | export → canonical holdings + pricing input |
| `snapshot.py` | one dated valuation per run |
| `dashboard.py` | builds the report, then draws it |
| `paths.py` | where data lives |

| Data (gitignored) | |
|---|---|
| `data/imports/` | broker exports you drop in |
| `data/isin_map.csv` | **the file you edit** — ticker corrections, `status=manual` pins one |
| `data/holdings.csv` | canonical holdings, generated |
| `data/positions.csv` | pricing input, generated |
| `data/history/` | dated snapshots; the series behind trend analysis |
| `data/prices/` | one close series per listing |
| `data/report.json` | the run's output — every figure, with what each means |

## Adding a parameter

One entry in `market/indicators.py`:

    MY_METRIC = Indicator(
        key="my_metric", label="My metric", unit="percent",
        means="what a reader needs to know about this number",
        needs=("closes",),
        compute=lambda closes, live, on, volumes=None: trends.my_metric(closes, live))

`compute` always receives four positional arguments - closes, the live quote,
its session, and a volume series where one exists (`None` otherwise) - even
where a parameter ignores the last one, as most do.

Add it to `ALL` and it appears in `report.json`, as a column on the page, and
in anything that filters rows. Nothing else is touched — the renderer builds
its columns from the report's own declaration and formats by `unit`, never by
name.

`needs` states the inputs required: `"closes"`, `"volumes"`, or both. Most
parameters need only closes, so they run on any instrument, held or not. A
parameter needing volumes is absent from a listing with no volume history,
the same way one needing a cost basis would be absent from a position with
none - rather than computed from a substituted zero.

The registry carries what a parameter *means* and never how to print it.
Decimal places, an em dash for an absent value and which side is green are
display decisions, and they live in `render/`.

## Adding a broker

One file in `holdings/adapters/` exposing `detect(path) -> bool` and
`parse(path) -> [Holding]`, registered in `holdings/adapters/__init__.py` under
`SPECIFIC`. Detection reads file content, never the filename. `FALLBACK` is
consulted only after every specific adapter declines, so a generic reader
never claims a file a dedicated one parses better.

Supply `broker_price` if the export states a valuation. It is what makes
resolution verifiable, and it is the main advantage a specific adapter has
over the generic reader.

Everything format-specific belongs inside the adapter. `holdings/adapters/ing.py`
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

**avg_cost** — average price paid per share, in the currency the broker
charged in, which is not always what the listing quotes in. **Cost basis** —
total paid for a position (`avg_cost × quantity`).

**Weight** — a position's share of total value. Weights drift as prices move.

**FX** — foreign exchange. For a holding priced in another currency, part of
its move is the exchange rate rather than the asset; snapshots store the rates
so the two stay separable.

**Depot** — German for a securities account. **Verrechnungskonto** — the cash
settlement account attached to it; trades pass through it.
