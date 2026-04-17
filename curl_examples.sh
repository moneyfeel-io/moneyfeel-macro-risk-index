#!/bin/bash
# MoneyFeel MRI API — curl Examples
# ===================================
# Get your free API key at: https://moneyfeel.it/account

API_KEY="mf_live_YOUR_KEY"
BASE="https://api.moneyfeel.ai/v1"

# ── Public endpoints (no auth) ─────────────────────────────────────────────

# Health check
curl "$BASE/status"

# Available regions and timeframes
curl "$BASE/regions"

# Current regime for all 5 regions
curl "$BASE/current"

# ── Authenticated endpoints ────────────────────────────────────────────────

# US Weekly history (last 2 years)
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/history?region=US&tf=WEEKLY&from=2024-01-01"

# EU Daily history
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/history?region=EU&tf=DAILY&from=2024-01-01"

# GLOBAL Monthly — full history
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/history?region=GLOBAL&tf=MONTHLY"

# Latest regime for ASIA Weekly
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/regime/latest?region=ASIA&tf=WEEKLY"

# Performance metrics — US Weekly
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/metrics?region=US&tf=WEEKLY"

# All regions metrics
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/metrics?tf=WEEKLY"

# Strategy timeseries — EU Weekly from 2020
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/timeseries?region=EU&tf=WEEKLY&from=2020-01-01"

# Year-by-year returns
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/eoy?region=US&tf=WEEKLY"

# Top drawdowns
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/drawdowns?region=US&tf=WEEKLY"

# Download full CSV — save to file
curl -H "Authorization: Bearer $API_KEY" \
  "$BASE/download?region=US&tf=WEEKLY" \
  -o mri_US_WEEKLY.csv

# Download all regions (loop)
for REGION in GLOBAL US EU ASIA EM; do
  curl -H "Authorization: Bearer $API_KEY" \
    "$BASE/download?region=$REGION&tf=WEEKLY" \
    -o "mri_${REGION}_WEEKLY.csv"
  echo "Downloaded $REGION"
done
