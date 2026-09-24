#!/usr/bin/env python3
"""Prices the positions in positions.csv, values them in the base currency and
writes text reports.

Usage:
    python portfolio_monitor.py -p positions.csv [-c config.json] [-o report] [-H history.json]
"""

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime
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
    """One position, as read from positions.csv and filled in by
    `fetch_prices`."""
    ticker: str
    quantity: float
    currency: str
    exchange: Optional[str] = None
    avg_cost: Optional[float] = None
    # Currency of `avg_cost`; None means the same as `currency`.
    cost_currency: Optional[str] = None
    # Unit the listing quotes in, e.g. GBp (pence) for a GBP position.
    quote_currency: Optional[str] = None
    # Populated after fetching prices
    current_price: Optional[float] = None
    previous_close: Optional[float] = None
    price_date: Optional[str] = None
    name: Optional[str] = None
    # Settled sessions in the download, as {iso date: value}, in the major
    # currency unit. These feed the per-listing series.
    closes: dict[str, float] = field(default_factory=dict)
    # Captured even when the quote unit is unknown.
    volumes: dict[str, float] = field(default_factory=dict)


@dataclass
class PortfolioReport:
    """Totals, movers, alerts and positions for one valuation."""
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
    # Tickers excluded from total_value because their currency had no rate.
    unvalued: list[str] = field(default_factory=list)


@dataclass
class AlertConfig:
    """Thresholds for alerts."""
    daily_change_threshold_pct: float = 5.0
    consecutive_days_threshold: int = 3
    target_prices: dict[str, float] = field(default_factory=dict)
    portfolio_drop_threshold_pct: float = 10.0


@dataclass
class Settings:
    """Base currency and alert settings, as returned by `load_config`."""
    base_currency: str = 'EUR'
    alerts: AlertConfig = field(default_factory=AlertConfig)
    # See resolve.PREFER_QUOTE_CURRENCY.
    prefer_quote_currency: bool = False


def parse_number(s, decimal_sep=None):
    """Parse a number in plain or German notation, or None if it is blank.

    `decimal_sep` forces the decimal separator when the caller knows it.

        '1.234,56'  -> 1234.56
        '205,9263'  -> 205.9263
        '205.9263'  -> 205.9263
        '1.234.567' -> 1234567
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
    """The positions in a positions CSV file."""
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

            # Case-sensitive: GBp is pence, GBP is pounds.
            quote_currency = (norm_row.get('quote_currency') or '').strip()

            # Default: the position's currency.
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
    """{currency: rate to `base`} for each currency; pairs without a rate
    are omitted."""
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
    """Every currency a rate is needed for: listing and cost-basis
    currencies."""
    wanted = set()
    for pos in positions:
        wanted.add(pos.currency)
        wanted.add(pos.cost_currency or pos.currency)
    return {c for c in wanted if c}


def _converted(amount, currency, rates, base):
    """`amount` in `base` as a `Converted`, or None if `rates` has no rate
    for `currency`."""
    if amount is None or not currency:
        return None
    rate = rates.get(currency)
    return None if rate is None else Converted(Money(amount, currency), rate, base)


def _amount(converted):
    """The amount of a `Converted`, or 0 for None. For sorting and display,
    not for totals."""
    return converted.amount if converted else 0.0


def value_of(pos: Position, rates: dict, base: str):
    """A position's market value in `base`, or None where it cannot be had."""
    if pos.current_price is None:
        return None
    return _converted(pos.quantity * pos.current_price, pos.currency, rates, base)


def cost_of(pos: Position, rates: dict, base: str):
    """The position's cost basis in `base`, or None if it has none or its
    rate is missing. `avg_cost` is in `cost_currency` (default: the listing
    currency)."""
    if not pos.avg_cost:
        return None
    return _converted(pos.quantity * pos.avg_cost,
                      pos.cost_currency or pos.currency, rates, base)


def _quote_currency(ticker: str) -> Optional[str]:
    """The unit `ticker` quotes in (e.g. `GBp`), or None if unknown."""
    try:
        return yf.Ticker(ticker).fast_info["currency"]
    except Exception:
        return None


# Download window. Settled closes in it that are not yet stored get recorded.
DOWNLOAD_PERIOD = "1mo"


def fetch_prices(positions: list[Position]) -> list[Position]:
    """Fill in each position's price, previous close, name, and settled
    closes and volumes from one batch download.

    A position whose quote unit is unknown is left unpriced.
    """
    tickers = [p.ticker for p in positions]

    # Batch download for efficiency
    try:
        data = yf.download(tickers, period=DOWNLOAD_PERIOD, group_by='ticker', progress=False, threads=True)
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
                # Volumes from their own column, independent of closes.
                if 'Volume' in ticker_data:
                    for ts, vol in ticker_data['Volume'].items():
                        if ts.date() >= date.today():
                            continue        # not settled yet
                        try:
                            vol = float(vol)
                        except (ValueError, TypeError):
                            continue
                        # NaN means no data.
                        if vol == vol:
                            pos.volumes[ts.date().isoformat()] = vol

                # Get the last two trading days
                closes = ticker_data['Close'].dropna()
                if len(closes) >= 2:
                    pos.current_price = float(closes.iloc[-1])
                    pos.previous_close = float(closes.iloc[-2])
                elif len(closes) == 1:
                    pos.current_price = float(closes.iloc[-1])
                    pos.previous_close = pos.current_price

                # Unit from positions.csv, else asked from the provider.
                unit = pos.quote_currency or _quote_currency(pos.ticker)
                session = closes.index[-1].date()
                # Latest bar: the snapshot price. Settled bars: the series.
                settled = [ts for ts in closes.index if ts.date() < date.today()]
                quote = Quote.from_provider(pos.current_price, unit,
                                            session=session)
                if quote is None:
                    print(f"WARNING: no quote unit for {pos.ticker}; "
                          f"leaving it unpriced rather than assuming one")
                    pos.current_price = pos.previous_close = None
                else:
                    pos.current_price = quote.price
                    pos.previous_close = Quote.from_provider(
                        pos.previous_close, unit).price
                    # Set only if the latest bar has settled.
                    pos.price_date = (quote.session.isoformat()
                                      if quote.settled else None)
                    for ts in settled:
                        close = Quote.from_provider(float(closes.loc[ts]), unit,
                                                    session=ts.date())
                        pos.closes[close.session.isoformat()] = close.price

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
    """Value the positions in `base_currency` and collect movers and alerts.

    A position whose currency has no rate is left unpriced and listed in
    `unvalued`; the total excludes it.
    """

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

        # No rate: leave the position unpriced.
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
    # Price in the listing's currency; value in the base currency.
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
        # Per-share cost and price, both in the base currency.
        lines.append(f"  {'Ticker':<10} {'Avg Cost':>12} {'Current':>12} {'P&L':>14} {'P&L %':>10}")
        lines.append("  " + "-" * 56)

        total_unrealized = 0
        omitted = []
        for pos in positions_with_cost:
            value = value_of(pos, report.fx_rates, report.base_currency)
            cost = cost_of(pos, report.fx_rates, report.base_currency)
            # Positions without a rate are listed as omitted from the total.
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
    """Settings from `config_path`, or from `paths.CONFIG` if not given;
    defaults where the file is missing."""
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

    # Load positions
    print(f"📂 Loading asset-take from: {args.asset_take}")
    positions = load_portfolio(args.asset_take)
    print(f"   Found {len(positions)} positions")

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
