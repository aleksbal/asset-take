# Alert Configuration

Configure alerts via a JSON config file to monitor specific conditions.

## Config File Structure

```json
{
  "base_currency": "EUR",
  "alerts": {
    "daily_change_threshold_pct": 5.0,
    "consecutive_days_threshold": 3,
    "portfolio_drop_threshold_pct": 10.0,
    "target_prices": {
      "AAPL": 250.00,
      "MSFT": 500.00
    }
  }
}
```

## Alert Types

### 1. Daily Change Alert
Triggers when a single stock moves more than X% in one day.

```json
"daily_change_threshold_pct": 5.0
```
- Default: 5.0%
- Recommended range: 3-7% depending on volatility tolerance

### 2. Consecutive Days Alert
Triggers when a stock moves in the same direction for N consecutive days.

```json
"consecutive_days_threshold": 3
```
- Default: 3 days
- Useful for identifying trends
- Requires history file (`--history` flag) to track across sessions

### 3. Portfolio Drop Alert
Triggers when total portfolio value drops more than X% in one day.

```json
"portfolio_drop_threshold_pct": 10.0
```
- Default: 10.0%
- Warning for significant portfolio drawdowns

### 4. Target Price Alerts
Triggers when a stock reaches a specified price target.

```json
"target_prices": {
  "AAPL": 250.00,
  "MSFT": 500.00,
  "SAP.DE": 200.00
}
```
- Set individual price targets per ticker
- Useful for profit-taking or buy signals

## Alert Output Examples

```
🚨 AAPL moved UP 6.2% today!
🚨 NVDA moved DOWN 5.8% today!
📈 MSFT has been up for 4 consecutive days
🎯 AAPL hit target price $250.00!
⚠️ PORTFOLIO DOWN 12.3% TODAY!
```

## Professional Alert Thresholds

| Investor Type | Daily Change | Consecutive Days | Portfolio Drop |
|--------------|--------------|------------------|----------------|
| Conservative | 3% | 5 days | 5% |
| Moderate | 5% | 3 days | 10% |
| Aggressive | 7% | 3 days | 15% |

## Tracking History

To enable consecutive day tracking across sessions, use the `--history` flag:

```bash
python portfolio_monitor.py -p asset-take.csv --history tracking.json
```

The history file stores the direction and count for each ticker.
