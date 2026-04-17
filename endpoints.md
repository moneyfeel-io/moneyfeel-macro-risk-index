# Endpoints

Base URL: `https://api.moneyfeel.ai/v1`

---

## Public Endpoints (no auth)

### GET /v1/status
Health check. Returns worker status and timestamp.

```bash
curl https://api.moneyfeel.ai/v1/status
```
```json
{"status": "ok", "worker": "mri-public-api", "version": "1.0", "ts": "2026-04-14T12:00:00.000Z"}
```

---

### GET /v1/regions
Returns available regions and timeframes.

```bash
curl https://api.moneyfeel.ai/v1/regions
```
```json
{
  "regions": ["GLOBAL", "US", "EU", "ASIA", "EM"],
  "timeframes": ["DAILY", "WEEKLY", "MONTHLY"],
  "coverage": "2007-01-04 to present",
  "updated": "daily at market close (UTC)"
}
```

---

### GET /v1/current
Current regime for all 5 regions. Updated daily at market close.

```bash
curl https://api.moneyfeel.ai/v1/current
```
```json
{
  "data": [
    {
      "region": "US",
      "regime_daily": "NEUTRAL",
      "regime_weekly": "BULL",
      "regime_monthly": "BULL",
      "score_daily": 0.38,
      "score_weekly": 0.31,
      "score_monthly": 0.29,
      "confidence_daily": 0.72,
      "confidence_weekly": 0.81,
      "color_daily": "#6b7a99",
      "color_weekly": "#4db2e6",
      "updated_at": "2026-04-14"
    }
  ]
}
```

---

## Authenticated Endpoints

All require `Authorization: Bearer mf_live_YOUR_KEY`

---

### GET /v1/history
Historical regime classifications.

**Parameters:**

| Param | Required | Default | Description |
|---|---|---|---|
| `region` | Yes | — | `GLOBAL` `US` `EU` `ASIA` `EM` |
| `tf` | No | `WEEKLY` | `DAILY` `WEEKLY` `MONTHLY` |
| `from` | No | `2007-01-01` | Start date `YYYY-MM-DD` |
| `to` | No | today | End date `YYYY-MM-DD` |

```bash
curl -H "Authorization: Bearer mf_live_YOUR_KEY" \
  "https://api.moneyfeel.ai/v1/history?region=US&tf=WEEKLY&from=2020-01-01"
```
```json
{
  "region": "US",
  "timeframe": "WEEKLY",
  "from": "2020-01-01",
  "to": "2099-12-31",
  "count": 324,
  "data": [
    {
      "as_of_date": "2020-01-03",
      "regime": "BULL",
      "regime_numeric": 1,
      "mri_score": 0.29,
      "prob_strong_bull": 0.04,
      "prob_bull": 0.58,
      "prob_neutral": 0.31,
      "prob_bear": 0.06,
      "prob_strong_bear": 0.01,
      "regime_confidence": 0.58,
      "days_in_regime": 14,
      "regime_changed": 0
    }
  ]
}
```

---

### GET /v1/regime/latest
Latest regime for a specific region and timeframe.

```bash
curl -H "Authorization: Bearer mf_live_YOUR_KEY" \
  "https://api.moneyfeel.ai/v1/regime/latest?region=EU&tf=WEEKLY"
```

---

### GET /v1/metrics
Strategy performance KPIs (Sharpe, CAGR, MaxDD, etc.).

**Parameters:** `region` (optional), `tf`

```bash
curl -H "Authorization: Bearer mf_live_YOUR_KEY" \
  "https://api.moneyfeel.ai/v1/metrics?region=US&tf=WEEKLY"
```

---

### GET /v1/timeseries
Daily strategy vs benchmark return series. Used for performance charts.

**Parameters:** `region`, `tf`, `from`

```bash
curl -H "Authorization: Bearer mf_live_YOUR_KEY" \
  "https://api.moneyfeel.ai/v1/timeseries?region=US&tf=WEEKLY&from=2020-01-01"
```
```json
{
  "region": "US",
  "timeframe": "WEEKLY",
  "data": [
    {
      "as_of_date": "2020-01-03",
      "bench_return": 0.0023,
      "strategy_return": 0.0023,
      "strategy_weight": 1.0,
      "cum_benchmark": 1.0023,
      "cum_strategy": 1.0023,
      "active_return": 0.0,
      "rolling_sharpe_6m": 0.82,
      "rolling_vol_6m": 0.14,
      "rolling_beta_6m": 0.97,
      "drawdown_series": -0.002
    }
  ]
}
```

---

### GET /v1/eoy
Year-by-year returns comparison.

```bash
curl -H "Authorization: Bearer mf_live_YOUR_KEY" \
  "https://api.moneyfeel.ai/v1/eoy?region=US&tf=WEEKLY"
```
```json
{
  "data": [
    {"year": 2020, "benchmark_ret": 18.33, "strategy_ret": 12.30, "won": false},
    {"year": 2022, "benchmark_ret": -18.18, "strategy_ret": -0.88, "won": true}
  ]
}
```

---

### GET /v1/drawdowns
Top 10 drawdown periods.

```bash
curl -H "Authorization: Bearer mf_live_YOUR_KEY" \
  "https://api.moneyfeel.ai/v1/drawdowns?region=US&tf=WEEKLY"
```

---

### GET /v1/download
Download full dataset as CSV (regime + strategy + timeseries merged).

```bash
curl -H "Authorization: Bearer mf_live_YOUR_KEY" \
  "https://api.moneyfeel.ai/v1/download?region=US&tf=WEEKLY" \
  -o mri_US_WEEKLY.csv
```

The CSV includes headers with source attribution and download date.
