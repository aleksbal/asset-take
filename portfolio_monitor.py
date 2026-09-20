#!/usr/bin/env python3
"""
Stock Portfolio Monitor - Main Script
Fetches prices from Yahoo Finance, calculates P&L, generates reports.

Usage:
    python portfolio_monitor.py --asset-take positions.csv --config config.json --output report
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

from market import fx as fx_service
import paths
from market.money import Converted, Money
from market.quotes import Quote

try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance not installed. Run: pip install yfinance --break-system-packages")
    sys.exit(1)


@dataclass
class Position:
    """Represents a single asset-take position."""
    ticker: str
    quantity: float
    currency: str
    exchange: Optional[str] = None
    avg_cost: Optional[float] = None
    # The currency `avg_cost` is denominated in - the broker's, which is not
    # always the listing's. Absent means it shares `currency`, which is what
    # a positions file written before this column said implicitly.
    cost_currency: Optional[str] = None
    # The unit the venue quotes in, recorded at resolution. Not the same as
    # `currency`: London quotes pence under GBp while the position is in GBP.
    quote_currency: Optional[str] = None
    # Populated after fetching prices
    current_price: Optional[float] = None
    previous_close: Optional[float] = None
    price_date: Optional[str] = None
    name: Optional[str] = None


@dataclass
class PortfolioReport:
    """Contains all data for the asset-take report."""
    positions: list[Position]
    base_currency: str
    fx_rates: dict[str, float]
    total_value: float
    total_value_previous: float
    daily_change: float
    daily_change_pct: float
    gainers: list[tuple[Position, float]]  # (position, pct_change)
    losers: list[tuple[Position, float]]
    alerts: list[str]
    timestamp: datetime
    # Positions excluded from total_value because their listing currency had
    # no rate. They are left unpriced rather than misvalued, so the total is
    # understated - and a total that does not say so is a partial sum
    # presented as the portfolio.
    unvalued: list[str] = field(default_factory=list)


@dataclass
class AlertConfig:
    """Configuration for alerts."""
    daily_change_threshold_pct: float = 5.0
    consecutive_days_threshold: int = 3
    target_prices: dict[str, float] = field(default_factory=dict)
    portfolio_drop_threshold_pct: float = 10.0


@dataclass
class Settings:
    """Everything a run is configured with.

    A tuple of (base_currency, alerts) widened every time something became
    configurable, and each addition broke both callers.

    Declared after AlertConfig so the annotation is the class itself rather
    than a forward reference. `snapshot.py` loads this module through
    importlib without registering it in sys.modules, and dataclasses cannot
    resolve a string annotation against a module it cannot find.
    """
    base_currency: str = 'EUR'
    alerts: AlertConfig = field(default_factory=AlertConfig)
    # Whether resolution should prefer a listing quoting in the holding's own
    # currency over one with more history. Off; see resolve.PREFER_QUOTE_CURRENCY.
    prefer_quote_currency: bool = False


def parse_number(s, decimal_sep=None):
    """Parse a number in either plain or German notation.

    `decimal_sep` forces the interpretation where the caller knows the locale.
    A comma-delimited file cannot use a decimal comma unquoted, so a comma in
    such a file is a thousands separator: '1,234' is 1234, not 1.234. Guessing
    understates by 1000x, silently.

    '1.234,56' -> 1234.56   (German: dot thousands, comma decimal)
    '205,9263'  -> 205.9263 (German decimal comma)
    '205.9263'  -> 205.9263 (plain decimal point)
    '1.234.567' -> 1234567  (repeated dots can only be thousands)

    The original assumed German notation unconditionally and stripped every
    dot, silently turning '30.0' into 300.
    """
    s = (s or "").strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    if decimal_sep == ".":
        return float(s.replace(",", ""))
    if decimal_sep == ",":
        return float(s.replace(".", "").replace(",", "."))
    if "," in s and "." in s:
        # whichever comes last is the decimal separator
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    return float(s)


def load_portfolio(csv_path: str) -> list[Position]:
    """Load asset-take positions from CSV file."""
    positions = []

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        # Try to detect delimiter
        sample = f.read(2048)
        f.seek(0)

        # Check for common delimiters
        if ';' in sample and ',' not in sample:
            delimiter = ';'
        elif '\t' in sample:
            delimiter = '\t'
        else:
            delimiter = ','

        reader = csv.DictReader(f, delimiter=delimiter)

        # Normalize column names (lowercase, strip whitespace)
        fieldnames_map = {}
        for fname in reader.fieldnames or []:
            normalized = fname.lower().strip().replace(' ', '_')
            fieldnames_map[normalized] = fname

        for row in reader:
            # Create normalized row
            norm_row = {k.lower().strip().replace(' ', '_'): v for k, v in row.items()}

            # Extract required fields with flexible column names
            ticker = (norm_row.get('ticker') or norm_row.get('symbol') or
                     norm_row.get('isin') or norm_row.get('wkn') or '').strip()

            quantity_str = (norm_row.get('quantity') or norm_row.get('shares') or
                           norm_row.get('amount') or norm_row.get('anzahl') or '0')
            quantity = parse_number(quantity_str) or 0

            currency = (norm_row.get('currency') or norm_row.get('währung') or 'EUR').strip().upper()

            exchange = (norm_row.get('exchange') or norm_row.get('börse') or '').strip()

            avg_cost_str = (norm_row.get('avg_cost') or norm_row.get('average_cost') or
                           norm_row.get('einstandskurs') or norm_row.get('kaufkurs') or '')
            avg_cost = parse_number(avg_cost_str) if avg_cost_str else None

            # NOT upper-cased: the quote unit is case-significant. GBp is
            # pence and GBP is pounds, and folding them loses the distinction
            # the column exists to carry.
            quote_currency = (norm_row.get('quote_currency') or '').strip()

            # Where the file states none, the cost shares the position's
            # currency - the assumption every positions.csv made before the
            # column existed, and the one that held while a mismatched cost
            # was being dropped upstream.
            cost_currency = (norm_row.get('cost_currency') or '').strip().upper()

            if ticker and quantity > 0:
                positions.append(Position(
                    ticker=ticker,
                    quantity=quantity,
                    currency=currency,
                    exchange=exchange if exchange else None,
                    avg_cost=avg_cost,
                    cost_currency=cost_currency or currency,
                    quote_currency=quote_currency or None
                ))

    return positions


def fetch_fx_rates(currencies: set[str], base_currency: str) -> dict[str, float]:
    """Rates converting each currency to the base, omitting any unavailable.

    A missing rate is left out rather than defaulted to 1.0. The old fallback
    valued a foreign holding as though it were domestic and said so only in a
    warning nobody reads - an error of whatever the exchange rate happens to
    be, reported as a price.
    """
    rates = {base_currency: 1.0}
    for currency in currencies:
        if currency == base_currency:
            continue
        value = fx_service.rate(currency, base_currency)
        if value is None:
            print(f"WARNING: no {currency}/{base_currency} rate; positions in "
                  f"{currency} will be left unvalued rather than misvalued")
            continue
        rates[currency] = value
    return rates


def currencies(positions: list[Position]) -> set[str]:
    """Every currency a rate is needed for.

    Both the listing's and the cost basis's: they are no longer required to
    match, so fetching rates for only the former leaves a foreign cost basis
    unconvertible and its P&L silently absent.
    """
    wanted = set()
    for pos in positions:
        wanted.add(pos.currency)
        wanted.add(pos.cost_currency or pos.currency)
    return {c for c in wanted if c}


def _converted(amount, currency, rates, base):
    """`amount` in `base` at a rate already held, or None where none is.

    Never a default of 1.0. That values a foreign amount as though it were
    domestic - wrong by whatever the rate happens to be, and indistinguishable
    from a correct figure once it is a bare number on a page.
    """
    if amount is None or not currency:
        return None
    rate = rates.get(currency)
    return None if rate is None else Converted(Money(amount, currency), rate, base)


def _amount(converted):
    """The figure, or 0 where there is none.

    For ordering and for a display line that has already established the
    position is priced - never for a total, where a missing conversion must
    stay missing rather than contribute nothing and look counted.
    """
    return converted.amount if converted else 0.0


def value_of(pos: Position, rates: dict, base: str):
    """A position's market value in `base`, or None where it cannot be had."""
    if pos.current_price is None:
        return None
    return _converted(pos.quantity * pos.current_price, pos.currency, rates, base)


def cost_of(pos: Position, rates: dict, base: str):
    """A position's cost basis in `base`, or None where it states none.

    `avg_cost` is denominated in `cost_currency` - what the broker charged in -
    which is not always what the listing quotes in. Both sides of a P&L are
    converted, so what is reported is the local gain expressed in the base
    currency; we hold no rate for the purchase date and do not pretend to.
    """
    if not pos.avg_cost:
        return None
    return _converted(pos.quantity * pos.avg_cost,
                      pos.cost_currency or pos.currency, rates, base)


def _quote_currency(ticker: str) -> Optional[str]:
    """The unit a venue quotes in, which is not always the currency it names."""
    try:
        return yf.Ticker(ticker).fast_info["currency"]
    except Exception:
        return None


def fetch_prices(positions: list[Position]) -> list[Position]:
    """Fetch current prices and previous close for all positions."""
    tickers = [p.ticker for p in positions]

    # Batch download for efficiency
    try:
        data = yf.download(tickers, period="5d", group_by='ticker', progress=False, threads=True)
    except Exception as e:
        print(f"ERROR fetching prices: {e}")
        return positions

    for pos in positions:
        try:
            if len(tickers) == 1:
                ticker_data = data
            else:
                ticker_data = data[pos.ticker] if pos.ticker in data.columns.get_level_values(0) else None

            if ticker_data is not None and not ticker_data.empty:
                # Get the last two trading days
                closes = ticker_data['Close'].dropna()
                if len(closes) >= 2:
                    pos.current_price = float(closes.iloc[-1])
                    pos.previous_close = float(closes.iloc[-2])
                elif len(closes) == 1:
                    pos.current_price = float(closes.iloc[-1])
                    pos.previous_close = pos.current_price

                # A download returns the venue's own quote unit. London sends
                # pence; valuing that with the pound's rate overstates the
                # position a hundredfold. The unit is recorded at resolution
                # so this does not depend on a lookup that can fail.
                unit = pos.quote_currency or _quote_currency(pos.ticker)
                quote = Quote.from_provider(pos.current_price, unit,
                                            session=closes.index[-1].date())
                if quote is None:
                    # An unconfirmed unit is indistinguishable from a major
                    # one, so the position is left unpriced. The dashboard
                    # reports that; a hundredfold overstatement it cannot.
                    print(f"WARNING: no quote unit for {pos.ticker}; "
                          f"leaving it unpriced rather than assuming one")
                    pos.current_price = pos.previous_close = None
                else:
                    pos.current_price = quote.price
                    pos.previous_close = Quote.from_provider(
                        pos.previous_close, unit).price
                    # Only a settled session may be written into a series: an
                    # in-progress bar recorded as a close is never corrected.
                    # The live price still feeds the snapshot, which is a
                    # point-in-time valuation and wants it.
                    pos.price_date = (quote.session.isoformat()
                                      if quote.settled else None)

            # Get company name
            try:
                info = yf.Ticker(pos.ticker).info
                pos.name = info.get('shortName') or info.get('longName') or pos.ticker
            except:
                pos.name = pos.ticker

        except Exception as e:
            print(f"WARNING: Could not fetch price for {pos.ticker}: {e}")

    return positions


def calculate_report(positions: list[Position], base_currency: str,
                    fx_rates: dict[str, float], alert_config: AlertConfig,
                    history_file: Optional[str] = None) -> PortfolioReport:
    """Calculate asset-take metrics and generate report data."""

    total_value = 0.0
    total_value_previous = 0.0
    position_changes = []
    alerts = []
    unvalued = []

    # Load historical data for consecutive day tracking
    history = {}
    if history_file and os.path.exists(history_file):
        with open(history_file, 'r') as f:
            history = json.load(f)

    for pos in positions:
        if pos.current_price is None:
            continue

        # No rate means no value. Defaulting to 1.0 here would undo the point
        # of omitting it above: a USD holding counted as though it were EUR,
        # wrong by whatever the exchange rate is. Unpriced is reported;
        # misvalued is not.
        value = value_of(pos, fx_rates, base_currency)
        if value is None:
            pos.current_price = pos.previous_close = None
            unvalued.append(pos.ticker)
            continue
        total_value += value.amount

        if pos.previous_close:
            previous = _converted(pos.quantity * pos.previous_close,
                                  pos.currency, fx_rates, base_currency)
            total_value_previous += previous.amount

            # Calculate daily change
            pct_change = ((pos.current_price - pos.previous_close) / pos.previous_close) * 100
            position_changes.append((pos, pct_change))

            # Check alerts
            if abs(pct_change) >= alert_config.daily_change_threshold_pct:
                direction = "UP" if pct_change > 0 else "DOWN"
                alerts.append(f"🚨 {pos.ticker} moved {direction} {abs(pct_change):.1f}% today!")

            # Check target prices
            if pos.ticker in alert_config.target_prices:
                target = alert_config.target_prices[pos.ticker]
                if pos.current_price >= target:
                    alerts.append(f"🎯 {pos.ticker} hit target price ${target:.2f}!")

            # Track consecutive days
            ticker_history = history.get(pos.ticker, {'direction': None, 'days': 0})
            current_direction = 'up' if pct_change > 0 else 'down' if pct_change < 0 else None

            if current_direction == ticker_history.get('direction'):
                ticker_history['days'] += 1
            else:
                ticker_history = {'direction': current_direction, 'days': 1}

            history[pos.ticker] = ticker_history

            if ticker_history['days'] >= alert_config.consecutive_days_threshold:
                alerts.append(f"📈 {pos.ticker} has been {current_direction} for {ticker_history['days']} consecutive days")

    # Save updated history
    if history_file:
        with open(history_file, 'w') as f:
            json.dump(history, f)

    # Sort for gainers/losers
    position_changes.sort(key=lambda x: x[1], reverse=True)
    gainers = [(p, c) for p, c in position_changes if c > 0][:5]
    losers = [(p, c) for p, c in position_changes if c < 0][-5:][::-1]

    # Portfolio-level alerts
    daily_change = total_value - total_value_previous
    daily_change_pct = (daily_change / total_value_previous * 100) if total_value_previous > 0 else 0

    if daily_change_pct <= -alert_config.portfolio_drop_threshold_pct:
        alerts.insert(0, f"⚠️ PORTFOLIO DOWN {abs(daily_change_pct):.1f}% TODAY!")

    return PortfolioReport(
        positions=positions,
        base_currency=base_currency,
        fx_rates=fx_rates,
        total_value=total_value,
        total_value_previous=total_value_previous,
        daily_change=daily_change,
        daily_change_pct=daily_change_pct,
        gainers=gainers,
        losers=losers,
        alerts=alerts,
        timestamp=datetime.now(),
        unvalued=unvalued,
    )


def format_currency(amount: float, currency: str) -> str:
    """Format amount with currency symbol."""
    symbols = {'EUR': '€', 'USD': '$', 'GBP': '£', 'CHF': 'CHF '}
    symbol = symbols.get(currency, currency + ' ')
    return f"{symbol}{amount:,.2f}"


def generate_long_report(report: PortfolioReport) -> str:
    """Generate detailed email report."""
    lines = []

    # Header
    lines.append("=" * 60)
    lines.append(f"📊 DAILY PORTFOLIO REPORT - {report.timestamp.strftime('%A, %B %d, %Y')}")
    lines.append("=" * 60)
    lines.append("")

    # Alerts section
    if report.alerts:
        lines.append("🚨 ALERTS")
        lines.append("-" * 40)
        for alert in report.alerts:
            lines.append(f"  {alert}")
        lines.append("")

    # Summary
    lines.append("📈 PORTFOLIO SUMMARY")
    lines.append("-" * 40)
    change_emoji = "🟢" if report.daily_change >= 0 else "🔴"
    incomplete = "*" if report.unvalued else ""
    lines.append(f"  Total Value:      {format_currency(report.total_value, report.base_currency)}{incomplete}")
    lines.append(f"  Previous Close:   {format_currency(report.total_value_previous, report.base_currency)}")
    lines.append(f"  Daily Change:     {change_emoji} {format_currency(report.daily_change, report.base_currency)} ({report.daily_change_pct:+.2f}%){incomplete}")
    if report.unvalued:
        # Position details filters on current_price, so these vanish from the
        # table entirely. A reader who is not told cannot tell a smaller
        # portfolio from an understated one.
        lines.append(f"  * excludes {len(report.unvalued)} position"
                     f"{'s' if len(report.unvalued) > 1 else ''} with no "
                     f"exchange rate ({', '.join(report.unvalued)}). The total "
                     f"is understated; the daily change describes only the "
                     f"remaining holdings, so a large move in an omitted "
                     f"position is not in it - nor in the drop alert.")
    lines.append("")

    # Top Gainers
    if report.gainers:
        lines.append("🟢 TOP GAINERS")
        lines.append("-" * 40)
        for pos, pct in report.gainers[:5]:
            value = value_of(pos, report.fx_rates, report.base_currency)
            value = value.amount if value else 0
            lines.append(f"  {pos.ticker:8} +{pct:5.2f}%  |  {format_currency(value, report.base_currency):>12}")
        lines.append("")

    # Top Losers
    if report.losers:
        lines.append("🔴 TOP LOSERS")
        lines.append("-" * 40)
        for pos, pct in report.losers[:5]:
            value = value_of(pos, report.fx_rates, report.base_currency)
            value = value.amount if value else 0
            lines.append(f"  {pos.ticker:8} {pct:5.2f}%  |  {format_currency(value, report.base_currency):>12}")
        lines.append("")

    # Full Position Details
    lines.append("📋 POSITION DETAILS")
    lines.append("-" * 60)
    # The price is the listing's own, so it carries the listing's currency.
    # 33 shares at an unlabelled 152.71 beside a value of EUR 4,386 reads as
    # an arithmetic error rather than a conversion.
    lines.append(f"  {'Ticker':<10} {'Shares':>10} {'Price':>14} {'Value':>14} {'Daily %':>10}")
    lines.append("  " + "-" * 56)

    # Sort positions by value
    sorted_positions = sorted(
        [p for p in report.positions if p.current_price],
        key=lambda p: _amount(value_of(p, report.fx_rates, report.base_currency)),
        reverse=True
    )

    for pos in sorted_positions:
        value = _amount(value_of(pos, report.fx_rates, report.base_currency))
        daily_pct = ((pos.current_price - pos.previous_close) / pos.previous_close * 100) if pos.previous_close else 0
        priced = f"{pos.current_price:,.2f} {pos.currency}"
        lines.append(f"  {pos.ticker:<10} {pos.quantity:>10.2f} {priced:>14} {format_currency(value, report.base_currency):>14} {daily_pct:>+9.2f}%")

    lines.append("")

    # Unrealized P&L (if avg_cost available)
    positions_with_cost = [p for p in report.positions if p.avg_cost and p.current_price]
    if positions_with_cost:
        lines.append("💰 UNREALIZED P&L")
        lines.append("-" * 60)
        # Per share, both converted to the base currency. Printing the raw
        # avg_cost beside the raw price put two different currencies in
        # adjacent columns with nothing to say so - SPCX showed a 152.71 EUR
        # cost against a 152.71 USD price and invited the obvious comparison.
        lines.append(f"  {'Ticker':<10} {'Avg Cost':>12} {'Current':>12} {'P&L':>14} {'P&L %':>10}")
        lines.append("  " + "-" * 56)

        total_unrealized = 0
        omitted = []
        for pos in positions_with_cost:
            # Both sides converted, because they need not share a currency any
            # more. Subtracting an unconverted cost from a converted value
            # reports the exchange rate itself as a gain or loss.
            value = value_of(pos, report.fx_rates, report.base_currency)
            cost = cost_of(pos, report.fx_rates, report.base_currency)
            # fetch_fx_rates omits a pair it cannot price rather than
            # inventing 1.0, so a cost currency can still arrive without a
            # rate. Skipping silently and then printing TOTAL states a sum of
            # the rows that happened to work as though it were the portfolio.
            if value is None or cost is None or not cost.amount:
                omitted.append(pos.ticker)
                continue
            pnl_base = value.amount - cost.amount
            pnl_pct = (value.amount / cost.amount - 1) * 100
            total_unrealized += pnl_base
            per_share_cost = format_currency(cost.amount / pos.quantity,
                                             report.base_currency)
            per_share_now = format_currency(value.amount / pos.quantity,
                                            report.base_currency)
            lines.append(f"  {pos.ticker:<10} {per_share_cost:>12} {per_share_now:>12} {format_currency(pnl_base, report.base_currency):>14} {pnl_pct:>+9.2f}%")

        lines.append("  " + "-" * 56)
        label = "TOTAL*" if omitted else "TOTAL"
        lines.append(f"  {label:<10} {'':<12} {'':<12} {format_currency(total_unrealized, report.base_currency):>14}")
        if omitted:
            lines.append(f"  * incomplete: excludes {len(omitted)} position"
                         f"{'s' if len(omitted) > 1 else ''} with no exchange "
                         f"rate for the cost basis ({', '.join(omitted)}). A "
                         f"P&L can be negative, so the omitted rows could move "
                         f"this figure either way.")
        lines.append("")

    # FX Rates
    if len(report.fx_rates) > 1:
        lines.append("💱 FX RATES")
        lines.append("-" * 40)
        for currency, rate in report.fx_rates.items():
            if currency != report.base_currency:
                lines.append(f"  {currency}/{report.base_currency}: {rate:.4f}")
        lines.append("")

    # Footer
    lines.append("=" * 60)
    lines.append(f"Generated: {report.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("Data source: Yahoo Finance (delayed up to 15 min)")
    lines.append("=" * 60)

    return "\n".join(lines)


def generate_short_report(report: PortfolioReport) -> str:
    """Generate concise WhatsApp-friendly report."""
    lines = []

    # Header
    date_str = report.timestamp.strftime('%d.%m.%Y')
    change_emoji = "🟢" if report.daily_change >= 0 else "🔴"

    lines.append(f"📊 *Portfolio {date_str}*")
    lines.append("")
    lines.append(f"💼 {format_currency(report.total_value, report.base_currency)}"
                 f"{'*' if report.unvalued else ''}")
    lines.append(f"{change_emoji} {report.daily_change_pct:+.2f}% "
                 f"({format_currency(report.daily_change, report.base_currency)})"
                 f"{'*' if report.unvalued else ''}")
    if report.unvalued:
        lines.append(f"*excl. {len(report.unvalued)} unpriced: "
                     f"{', '.join(report.unvalued)}")
    lines.append("")

    # Alerts (abbreviated)
    if report.alerts:
        for alert in report.alerts[:3]:  # Max 3 alerts
            lines.append(alert)
        lines.append("")

    # Top movers (max 3 each)
    if report.gainers:
        top_gainers = " | ".join([f"{p.ticker} +{c:.1f}%" for p, c in report.gainers[:3]])
        lines.append(f"📈 {top_gainers}")

    if report.losers:
        top_losers = " | ".join([f"{p.ticker} {c:.1f}%" for p, c in report.losers[:3]])
        lines.append(f"📉 {top_losers}")

    return "\n".join(lines)


def save_reports(report: PortfolioReport, output_base: str):
    """Save both long and short reports to files."""
    timestamp = report.timestamp.strftime('%Y%m%d')

    # Long report (email)
    long_path = f"{output_base}_email_{timestamp}.txt"
    with open(long_path, 'w', encoding='utf-8') as f:
        f.write(generate_long_report(report))
    print(f"✅ Email report saved: {long_path}")

    # Short report (WhatsApp)
    short_path = f"{output_base}_whatsapp_{timestamp}.txt"
    with open(short_path, 'w', encoding='utf-8') as f:
        f.write(generate_short_report(report))
    print(f"✅ WhatsApp report saved: {short_path}")

    return long_path, short_path


def load_config(config_path: Optional[str] = None) -> Settings:
    """Settings from a JSON file, or the defaults where there is none.

    Falls back to `paths.CONFIG` (data/config.json), which is where an
    instance's own settings belong. Passing None used to mean "no config at
    all", so nothing outside the CLI ever read one - `snapshot.py` called
    `load_config(None)` and silently got the defaults every run.
    """
    settings = Settings()
    path = config_path or paths.CONFIG

    if path and os.path.exists(path):
        with open(path, 'r') as f:
            config = json.load(f)

        settings.base_currency = config.get('base_currency', 'EUR')
        settings.prefer_quote_currency = bool(
            config.get('prefer_quote_currency', False))

        if 'alerts' in config:
            alerts = config['alerts']
            a = settings.alerts
            a.daily_change_threshold_pct = alerts.get('daily_change_threshold_pct', 5.0)
            a.consecutive_days_threshold = alerts.get('consecutive_days_threshold', 3)
            a.target_prices = alerts.get('target_prices', {})
            a.portfolio_drop_threshold_pct = alerts.get('portfolio_drop_threshold_pct', 10.0)

    return settings


def main():
    parser = argparse.ArgumentParser(description='Stock Portfolio Monitor')
    parser.add_argument('--asset-take', '-p', required=True, help='Path to asset-take CSV file')
    parser.add_argument('--config', '-c', help='Path to config JSON file (optional)')
    parser.add_argument('--output', '-o', default='portfolio_report', help='Output file base name')
    parser.add_argument('--history', '-H', help='Path to history file for tracking consecutive days')

    args = parser.parse_args()

    print("🚀 Stock Portfolio Monitor")
    print("=" * 40)

    # Load configuration
    settings = load_config(args.config)
    base_currency, alert_config = settings.base_currency, settings.alerts
    print(f"📌 Base currency: {base_currency}")

    # Load asset-take. argparse maps --asset-take to args.asset_take; this
    # read args.portfolio, so every invocation raised AttributeError.
    print(f"📂 Loading asset-take from: {args.asset_take}")
    positions = load_portfolio(args.asset_take)
    print(f"   Found {len(positions)} positions")

    # Every currency a rate is needed for, cost bases included: a cost basis
    # in a third currency would otherwise reach cost_of() without a rate and
    # drop that position's P&L from the report in silence.
    #
    # Not bound to a local named `currencies` - that shadowed this helper.
    wanted = currencies(positions)
    print(f"💱 Currencies: {', '.join(sorted(wanted))}")

    # Fetch FX rates
    print("💱 Fetching FX rates...")
    fx_rates = fetch_fx_rates(wanted, base_currency)

    # Fetch prices
    print("📈 Fetching stock prices...")
    positions = fetch_prices(positions)

    # Calculate report
    print("📊 Calculating asset-take metrics...")
    report = calculate_report(positions, base_currency, fx_rates, alert_config, args.history)

    # Generate and save reports
    print("📝 Generating reports...")
    long_path, short_path = save_reports(report, args.output)

    # Print summary
    print("\n" + "=" * 40)
    print("📊 SUMMARY")
    print(f"   Total Value: {format_currency(report.total_value, report.base_currency)}")
    change_sign = "+" if report.daily_change >= 0 else ""
    print(f"   Daily Change: {change_sign}{report.daily_change_pct:.2f}%")
    if report.alerts:
        print(f"   Alerts: {len(report.alerts)}")
    print("=" * 40)


if __name__ == '__main__':
    main()
