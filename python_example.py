"""
MoneyFeel MRI API — Python Example
===================================
Full example: fetch regime history, timeseries and download CSV.

Requirements:
    pip install requests pandas

Get your free API key at: https://moneyfeel.it/account
"""

import requests
import pandas as pd
from io import StringIO

API_KEY = "mf_live_YOUR_KEY"
BASE    = "https://api.moneyfeel.ai/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def get_current():
    """Get current regime for all regions (no auth required)."""
    r = requests.get(f"{BASE}/current")
    r.raise_for_status()
    return r.json()["data"]


def get_history(region="US", tf="WEEKLY", from_date="2020-01-01"):
    """Get historical regime classifications."""
    r = requests.get(
        f"{BASE}/history",
        params={"region": region, "tf": tf, "from": from_date},
        headers=HEADERS
    )
    r.raise_for_status()
    return pd.DataFrame(r.json()["data"])


def get_timeseries(region="US", tf="WEEKLY", from_date="2020-01-01"):
    """Get strategy vs benchmark daily return series."""
    r = requests.get(
        f"{BASE}/timeseries",
        params={"region": region, "tf": tf, "from": from_date},
        headers=HEADERS
    )
    r.raise_for_status()
    return pd.DataFrame(r.json()["data"])


def download_csv(region="US", tf="WEEKLY", output_path=None):
    """Download full dataset as CSV."""
    r = requests.get(
        f"{BASE}/download",
        params={"region": region, "tf": tf},
        headers=HEADERS
    )
    r.raise_for_status()

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(r.text)
        print(f"Saved to {output_path}")
        return output_path
    else:
        # Return as DataFrame (skip comment lines starting with #)
        lines = [l for l in r.text.splitlines() if not l.startswith("#")]
        return pd.read_csv(StringIO("\n".join(lines)))


def get_metrics(region="US", tf="WEEKLY"):
    """Get strategy performance KPIs."""
    r = requests.get(
        f"{BASE}/metrics",
        params={"region": region, "tf": tf},
        headers=HEADERS
    )
    r.raise_for_status()
    data = r.json()["data"]
    return data[0] if data else {}


# ── Example usage ──────────────────────────────────────────────────────────

if __name__ == "__main__":

    # 1. Current regime (no auth)
    print("=== Current Regime ===")
    current = get_current()
    for row in current:
        print(f"  {row['region']}: {row['regime_weekly']} (score {row['score_weekly']})")

    # 2. US Weekly history
    print("\n=== US Weekly History (last 5 rows) ===")
    history = get_history("US", "WEEKLY", "2024-01-01")
    print(history[["as_of_date", "regime", "mri_score", "regime_confidence"]].tail())

    # 3. Performance metrics
    print("\n=== US Weekly Metrics ===")
    metrics = get_metrics("US", "WEEKLY")
    print(f"  CAGR Overlay:  {metrics.get('cagr_strategy')}%")
    print(f"  Sharpe Ratio:  {metrics.get('sharpe')}")
    print(f"  Max Drawdown:  {metrics.get('max_drawdown')}%")

    # 4. Download full CSV
    print("\n=== Downloading full US Weekly dataset ===")
    df = download_csv("US", "WEEKLY")
    print(f"  Rows: {len(df)}, Columns: {len(df.columns)}")
    print(f"  Date range: {df['as_of_date'].min()} → {df['as_of_date'].max()}")
