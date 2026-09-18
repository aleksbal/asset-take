# portfolio

Daily valuation of a stock/ETF portfolio. Prices from Yahoo Finance.

## Use

Import holdings — when you trade. Drop a broker export in `imports/` (any
filename; adapters recognise their own format by content):

    ./.venv/bin/python import_holdings.py            # newest file in imports/
    ./.venv/bin/python import_holdings.py <path>

Snapshot — daily:

    ./.venv/bin/python snapshot.py

Dashboard:

    ./.venv/bin/python dashboard.py && open dashboard.html

Text report:

    ./.venv/bin/python portfolio_monitor.py -p positions.csv -o report

## Files

| | |
|---|---|
| `adapters/` | one module per broker: `detect(path)`, `parse(path)` |
| `canonical.py` | the `Holding` record every adapter produces |
| `resolve.py` | ISIN → Yahoo ticker, verified against the broker's own price |
| `import_holdings.py` | export → `holdings.csv` + `positions.csv` |
| `isin_map.csv` | **the file you edit.** Change `ticker`, set `status=manual` to pin it |
| `holdings.csv` | canonical holdings, generated |
| `positions.csv` | pricing input, generated — edit `isin_map.csv`, not this |
| `dashboard.py` | renders `dashboard.html` — local, gitignored, contains your data |
| `history/` | one JSON per day; the series behind drift analysis |

Adding a broker means one new file in `adapters/`.

Yahoo can't match a German broker exactly — ING prices on Direkthandel, which
Yahoo doesn't carry. Expect ~0.1% on the total.

## Bank accounts (ING, FinTS)

    export FINTS_PRODUCT_ID=<your id>
    ./.venv/bin/python fetch_ing.py --days 30

Cash accounts, balances and transactions from ING (BLZ 50010517, endpoint
`https://fints.ing.de/fints/`). Prompts for Zugangsnummer and PIN at runtime;
nothing is written to disk but the FinTS system id in `.fints_state.bin`.

**Blocked** until a product ID is registered — free, via
<https://www.hbci-zka.de/register/prod_register.htm>, ~10-15 working days.
python-fints refuses to construct a client without one.

ING is reported not to support securities/Depot over FinTS (`HKWPD`), so this
covers cash only; `fetch_ing.py` prints what the bank actually advertises.
Holdings stay in `positions.csv`.

## Glossary

**ISIN** — International Securities Identification Number. A 12-character code
identifying a security worldwide, e.g. `IE00B4L5Y983`. Country prefix, then a
national code, then a check digit. One ISIN per fund, regardless of where it
trades. This is what German banks and ING's Depot export use.

**Ticker** — the short symbol a security trades under *on one exchange*, e.g.
`IWDA.AS`. Suffix is the exchange (`.AS` Amsterdam, `.DE` Xetra, `.L` London).
The same fund has a different ticker, and a slightly different price, on each
exchange. Yahoo Finance keys on ticker, not ISIN, which is why `positions.csv`
uses tickers and the mapping has to be done by hand.

**WKN** — Wertpapierkennnummer. The older 6-character German security ID,
still shown by some banks alongside the ISIN.

**P&L** — profit and loss. Here always *unrealized*: current value minus what
you paid, for things you still hold. Nothing is realized until you sell.

**avg_cost** — average price paid per share, across all your purchases of it.
Multiply by quantity to get what the position cost you. Needed for P&L.

**Cost basis** — total paid for a position (`avg_cost × quantity`).

**Weight** — a position's share of total portfolio value, in percent. Weights
drift as prices move, which is what "allocation drift" refers to.

**FX** — foreign exchange. If a holding is priced in USD, part of its daily
move in EUR is the exchange rate rather than the asset. Snapshots store
`fx_rates` so the two stay separable.

**Base currency** — the currency everything is converted into for totals. EUR.

**Depot** — German for a securities account, i.e. where your shares and ETFs
are held. Distinct from a cash account.

**Verrechnungskonto** — the cash settlement account attached to a Depot. Buys
and sells pass through it, so its transactions reveal trades even when the
Depot itself can't be read.

**FinTS** — Financial Transaction Services, formerly HBCI. The German banking
standard letting your own software talk to your bank. First-party: you
authenticate as yourself, with your own credentials.

**HBCI** — the former name for FinTS. Still used interchangeably.

**PIN/TAN** — the FinTS authentication scheme. The PIN is a shared secret sent
with each signed message; the TAN is the second factor confirmed out-of-band,
usually in the bank's app.

**SCA** — Strong Customer Authentication. The PSD2 requirement behind the TAN
prompt. Read access is exempt for 90 days after a successful login, which is
why FinTS logins lapse.

**PSD2** — the EU payment services directive that mandates SCA and created the
licensed third-party access regime (XS2A) that sits alongside FinTS.

**HKWPD** — the FinTS segment that requests Depot holdings. ING appears not to
support it, which is why holdings are maintained by hand here.

**Product ID** — an identifier issued by the Deutsche Kreditwirtschaft
identifying the *software*, not you. Not a credential and not a security
control; it grants no access. python-fints requires one anyway.

**BLZ** — Bankleitzahl, the German bank sort code. ING is `50010517`.

**Zugangsnummer** — ING's name for the login identifier. In FinTS terms it is
the *Benutzerkennung*.
