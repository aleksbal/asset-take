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

import quotes

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
    # Populated after fetching prices
    current_price: Optional[float] = None
    previous_close: Optional[float] = None
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


@dataclass
class AlertConfig:
    """Configuration for alerts."""
    daily_change_threshold_pct: float = 5.0
    consecutive_days_threshold: int = 3
    target_prices: dict[str, float] = field(default_factory=dict)
    portfolio_drop_threshold_pct: float = 10.0


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

            if ticker and quantity > 0:
                positions.append(Position(
                    ticker=ticker,
                    quantity=quantity,
                    currency=currency,
                    exchange=exchange if exchange else None,
                    avg_cost=avg_cost
                ))

    return positions


def fetch_fx_rates(currencies: set[str], base_currency: str) -> dict[str, float]:
    """Fetch FX rates to convert all currencies to base currency."""
    fx_rates = {base_currency: 1.0}

    for currency in currencies:
        if currency == base_currency:
            continue

        # Yahoo Finance FX format: EURUSD=X
        pair = f"{currency}{base_currency}=X"
        try:
            ticker = yf.Ticker(pair)
            hist = ticker.history(period="1d")
            if not hist.empty:
                fx_rates[currency] = hist['Close'].iloc[-1]
            else:
                # Try reverse pair
                pair_rev = f"{base_currency}{currency}=X"
                ticker_rev = yf.Ticker(pair_rev)
                hist_rev = ticker_rev.history(period="1d")
                if not hist_rev.empty:
                    fx_rates[currency] = 1.0 / hist_rev['Close'].iloc[-1]
                else:
                    print(f"WARNING: Could not fetch FX rate for {currency}/{base_currency}, using 1.0")
                    fx_rates[currency] = 1.0
        except Exception as e:
            print(f"WARNING: FX fetch error for {currency}: {e}")
            fx_rates[currency] = 1.0

    return fx_rates


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
                # position a hundredfold.
                quote_currency = _quote_currency(pos.ticker)
                pos.current_price, _ = quotes.as_major(pos.current_price,
                                                       quote_currency)
                pos.previous_close, _ = quotes.as_major(pos.previous_close,
                                                        quote_currency)

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

    # Load historical data for consecutive day tracking
    history = {}
    if history_file and os.path.exists(history_file):
        with open(history_file, 'r') as f:
            history = json.load(f)

    for pos in positions:
        if pos.current_price is None:
            continue

        fx_rate = fx_rates.get(pos.currency, 1.0)

        # Calculate position value in base currency
        position_value = pos.quantity * pos.current_price * fx_rate
        total_value += position_value

        if pos.previous_close:
            position_value_prev = pos.quantity * pos.previous_close * fx_rate
            total_value_previous += position_value_prev

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
        timestamp=datetime.now()
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
    lines.append(f"  Total Value:      {format_currency(report.total_value, report.base_currency)}")
    lines.append(f"  Previous Close:   {format_currency(report.total_value_previous, report.base_currency)}")
    lines.append(f"  Daily Change:     {change_emoji} {format_currency(report.daily_change, report.base_currency)} ({report.daily_change_pct:+.2f}%)")
    lines.append("")

    # Top Gainers
    if report.gainers:
        lines.append("🟢 TOP GAINERS")
        lines.append("-" * 40)
        for pos, pct in report.gainers[:5]:
            value = pos.quantity * (pos.current_price or 0) * report.fx_rates.get(pos.currency, 1.0)
            lines.append(f"  {pos.ticker:8} +{pct:5.2f}%  |  {format_currency(value, report.base_currency):>12}")
        lines.append("")

    # Top Losers
    if report.losers:
        lines.append("🔴 TOP LOSERS")
        lines.append("-" * 40)
        for pos, pct in report.losers[:5]:
            value = pos.quantity * (pos.current_price or 0) * report.fx_rates.get(pos.currency, 1.0)
            lines.append(f"  {pos.ticker:8} {pct:5.2f}%  |  {format_currency(value, report.base_currency):>12}")
        lines.append("")

    # Full Position Details
    lines.append("📋 POSITION DETAILS")
    lines.append("-" * 60)
    lines.append(f"  {'Ticker':<10} {'Shares':>10} {'Price':>12} {'Value':>14} {'Daily %':>10}")
    lines.append("  " + "-" * 56)

    # Sort positions by value
    sorted_positions = sorted(
        [p for p in report.positions if p.current_price],
        key=lambda p: p.quantity * p.current_price * report.fx_rates.get(p.currency, 1.0),
        reverse=True
    )

    for pos in sorted_positions:
        value = pos.quantity * pos.current_price * report.fx_rates.get(pos.currency, 1.0)
        daily_pct = ((pos.current_price - pos.previous_close) / pos.previous_close * 100) if pos.previous_close else 0
        lines.append(f"  {pos.ticker:<10} {pos.quantity:>10.2f} {pos.current_price:>12.2f} {format_currency(value, report.base_currency):>14} {daily_pct:>+9.2f}%")

    lines.append("")

    # Unrealized P&L (if avg_cost available)
    positions_with_cost = [p for p in report.positions if p.avg_cost and p.current_price]
    if positions_with_cost:
        lines.append("💰 UNREALIZED P&L")
        lines.append("-" * 60)
        lines.append(f"  {'Ticker':<10} {'Avg Cost':>10} {'Current':>10} {'P&L':>14} {'P&L %':>10}")
        lines.append("  " + "-" * 56)

        total_unrealized = 0
        for pos in positions_with_cost:
            pnl = (pos.current_price - pos.avg_cost) * pos.quantity
            pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost) * 100
            fx_rate = report.fx_rates.get(pos.currency, 1.0)
            pnl_base = pnl * fx_rate
            total_unrealized += pnl_base
            lines.append(f"  {pos.ticker:<10} {pos.avg_cost:>10.2f} {pos.current_price:>10.2f} {format_currency(pnl_base, report.base_currency):>14} {pnl_pct:>+9.2f}%")

        lines.append("  " + "-" * 56)
        lines.append(f"  {'TOTAL':<10} {'':<10} {'':<10} {format_currency(total_unrealized, report.base_currency):>14}")
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
    lines.append(f"💼 {format_currency(report.total_value, report.base_currency)}")
    lines.append(f"{change_emoji} {report.daily_change_pct:+.2f}% ({format_currency(report.daily_change, report.base_currency)})")
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


def load_config(config_path: Optional[str]) -> tuple[str, AlertConfig]:
    """Load configuration from JSON file or use defaults."""
    base_currency = 'EUR'
    alert_config = AlertConfig()

    if config_path and os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)

        base_currency = config.get('base_currency', 'EUR')

        if 'alerts' in config:
            alerts = config['alerts']
            alert_config.daily_change_threshold_pct = alerts.get('daily_change_threshold_pct', 5.0)
            alert_config.consecutive_days_threshold = alerts.get('consecutive_days_threshold', 3)
            alert_config.target_prices = alerts.get('target_prices', {})
            alert_config.portfolio_drop_threshold_pct = alerts.get('portfolio_drop_threshold_pct', 10.0)

    return base_currency, alert_config


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
    base_currency, alert_config = load_config(args.config)
    print(f"📌 Base currency: {base_currency}")

    # Load asset-take
    print(f"📂 Loading asset-take from: {args.portfolio}")
    positions = load_portfolio(args.portfolio)
    print(f"   Found {len(positions)} positions")

    # Get unique currencies
    currencies = set(p.currency for p in positions)
    print(f"💱 Currencies: {', '.join(currencies)}")

    # Fetch FX rates
    print("💱 Fetching FX rates...")
    fx_rates = fetch_fx_rates(currencies, base_currency)

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
