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

Number parsing is locale-ambiguous by nature: `1,234` is 1234 in English and
1.234 in German. The file delimiter does NOT settle it - CSV quoting permits a
comma inside a comma-delimited field - so do not infer locale from it. The
generic adapter infers one locale from all of the file's numbers together and
applies it uniformly; where there is no evidence it declines to guess rather
than inventing a default.

A snapshot position may carry no price - the provider can fail for one ticker
while succeeding for the rest. Never value such a position at zero: it
understates the total while looking complete. Exclude it and say so.

`avg_cost` and `broker_price` are both optional on `Holding`, and blank means
absent, never zero - a zero cost basis reads as a 100% gain. Anything consuming
them must handle `None`; `canonical.read()` raised on the empty field until a
source without cost data existed, which killed the dashboard outright rather
than omitting P&L.

The same applies to aggregates over those optional fields. `sum([])` is `0`,
so a portfolio where no position states a cost basis reported `+0 EUR` of
unrealised P&L - an unknown result dressed as a certainty. Aggregate only over
the contributing rows and render the summary as unavailable when there are
none.

`avg_cost` is denominated in the holding's own currency. The dashboard
converts a position's value to the base currency, so it must convert the cost
basis with it - subtracting an unconverted USD cost from a EUR value reports
the exchange rate itself as a gain or loss. Both sides use the same rate, so
what is shown is the local P&L expressed in the base currency; we hold no
historical FX for the purchase date and do not pretend to.

Ticker resolution compares prices converted, not raw. A listing quoted in
another currency is the same instrument; requiring the currencies to match
discarded the only candidate the provider offered for four holdings and is
what made hand-pinning necessary. Conversion widens the search without
weakening the check - the small-cap namesake that caused the original
mismapping is still 93% out and still rejected. A candidate already in the
holding's currency still wins where one exists, because converting introduces
a rate we hold no history for.

A provider price enters through `Quote.from_provider`, which normalises to the
major unit and records whether the session has closed. It returns None for an
unknown unit rather than a quote in an assumed one.

Three paths read prices and each builds a Quote:

- `resolve._price()` - `fast_info`, for verifying a candidate
- `portfolio_monitor.fetch_prices()` - `yf.download`, for the daily valuation
- `price_history._fetch()` - `Ticker.history`, for seeding a series

Six review rounds found the same defect in six places before this type
existed: a price reaching storage or valuation with its unit left behind,
where 4,208 pence is a valid number that happens to be a hundred times the
truth. Each fix was correct and each looked complete. A fourth reader must
construct a Quote too - not because a convention says so, but because there
is then nothing left to forget.

Exchange rates come from `fx.rate`, current or on a given date, and it
returns None where a pair cannot be priced. Never 1.0: that values a foreign
holding as though it were domestic, wrong by whatever the rate happens to be,
and indistinguishable from a correct figure. A position whose rate is missing
is left unvalued, which the dashboard reports.

The unit itself is recorded at resolution, in `quote_currency` on the map and
on `positions.csv`, and is deliberately not upper-cased - `GBp` and `GBP` are
different units and folding the case destroys the distinction the column
exists to carry. Valuation reads it from the file rather than asking the
provider, because a failed lookup is indistinguishable from a major-unit
quote. Where the unit cannot be established the position is left unpriced: an
excluded position is visible in the dashboard, a hundredfold overstatement is
not.

A close belongs to the session it settled in, not to the day of the run. A
weekend or pre-close run otherwise files it under a day the market never
traded, and the same-day guard then stops a later run correcting it. A bar
dated today may still be in progress and the provider flags no such thing, so
it is not recorded at all: the series lags a session rather than holding an
intraday value that can never be corrected. The live price is still used for
the snapshot, which is a point-in-time valuation and wants it.

Where a quote unit cannot be established, nothing is stored and nothing is
valued. No marker is left either, so the next run simply retries. An unscaled
series is worse than an absent one - it sits beside converted closes, reads
as a corporate action, and re-seeds itself back to the raw values every run.

A price is normalised to its currency's major unit the moment it is read.
London quotes pence and reports `GBp`; downstream valuation gives an
unrecognised code a rate of 1.0, so a 4,208 pence share is valued as 4,208
pounds. Normalising only where prices are compared is not enough - the check
passes while the stored row stays a hundredfold out. `GBP` is not a minor
unit and must never be scaled.

A price series can be rescaled underneath us. The provider adjusts its
history retroactively for splits; ours stays as observed, so closes recorded
either side of one sit on different scales, and provenance cannot repair it
because both are ours. A day's move too large to be a price move re-seeds the
series from the provider, whose adjusted history is the one consistent scale
available.

Resolution must be stable. Candidates differ by hundredths of a percent and
live prices move, so choosing afresh each run flips between venues for no
gain, and every flip restarts that position's price history under a new
symbol. An existing mapping that still verifies and still carries history is
kept.

A row needs an instrument identifier, not just a quantity. Exports end in a
subtotal line whose quantity is a sum; admitted, it becomes a holding keyed on
the empty string, and several of them overwrite one another downstream.

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

Trend metrics are facts, never signals. `trends` reports how far a price sits
below a recent peak, which side of its moving averages it is on, and its RSI.
It does not say what to do, and nothing should be added that does: by price
alone, a stock that has stopped growing is indistinguishable from one that is
resting, and the evidence that acting on these beats holding is contested.

No metric is computed from too little data, and a window is never shortened
to fit. A 200-session average needs 200 sessions: non-trading days are
already absent from a price series, so holidays are no argument for
tolerance, and 170 closes averaged under a 200-session label is a different
number the output cannot be told apart from. A drawdown window is calendar
time, because "its six-month high" is a claim about time rather than
sessions, and it is reported only when the series reaches back that far. Where a series cannot support a
metric, the value is None and the dashboard shows an em dash - absent and
neutral are different claims.
