# Changelog

## [1.0.2] — 2026-04-17

### Changed

- Updated the default public API base URL to `https://api.moneyfeel.ai/v1`
- Aligned documentation, examples and test suite with the official custom domain
- Confirmed end-to-end compatibility for the Python client and public MRI API

## [1.0.1] — 2026-04-16

### Improved

- Refined package documentation and usage examples
- Improved code comments and formatting across the test suite
- Minor cleanup of repository structure and developer-facing documentation

## [1.0.0] — 2026-04-15

### Initial Release

- Public API for MoneyFeel MRI data
- 5 regions: GLOBAL, US, EU, ASIA, EM
- 3 timeframes: DAILY, WEEKLY, MONTHLY
- Historical coverage: 2007-01-04 to present
- Endpoints: /current, /history, /regime/latest, /metrics, /timeseries, /eoy, /drawdowns, /download
- API key authentication via Cloudflare KV
- Rate limiting: 30 req/min, 2,000 req/day per key
- MRI model: bocpd_mindur20 v9.5 with map_linear_macro score mapping
