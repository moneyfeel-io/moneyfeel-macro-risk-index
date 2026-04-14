# Rate Limits

## Current Limits (Free Plan)

| Limit | Value |
|---|---|
| Requests per minute | 30 |
| Requests per day | 2,000 |
| Historical coverage | Full (2007–present) |
| Regions | All 5 (GLOBAL, US, EU, ASIA, EM) |
| Timeframes | All 3 (DAILY, WEEKLY, MONTHLY) |

Daily quotas reset at **00:00 UTC**.

## Rate Limit Errors

When limits are exceeded, the API returns HTTP `429` with a JSON body and a `Retry-After` header.

### Per-minute rate limit exceeded
```json
{
  "error": "rate_limit_exceeded",
  "message": "Rate limit exceeded: 30 requests/minute. Retry after 42 seconds.",
  "status": 429
}
```
Header: `Retry-After: 42`

### Daily quota exceeded
```json
{
  "error": "daily_quota_exceeded",
  "message": "Daily quota exceeded: 2000 requests/day. Quota resets at 2026-04-15T00:00:00 UTC.",
  "status": 429
}
```

## Handling Rate Limits in Code

### Python
```python
import requests, time

def mri_get(url, headers, params, max_retries=3):
    for attempt in range(max_retries):
        r = requests.get(url, headers=headers, params=params)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", 60))
            print(f"Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            continue
        r.raise_for_status()
        return r.json()
    raise Exception("Max retries exceeded")
```

### R
```r
mri_get_safe <- function(url, key, params) {
  resp <- request(url) |>
    req_url_query(!!!params) |>
    req_headers(Authorization = paste("Bearer", key)) |>
    req_retry(max_tries = 3, is_transient = \(r) resp_status(r) == 429) |>
    req_perform()
  resp_body_json(resp)
}
```

## Tips for Staying Within Limits

- **Bulk downloads:** use `/v1/download` (1 request) instead of paginating `/v1/history`
- **Cache locally:** MRI data updates once per day — no need to poll more than once daily
- **Batch regions:** if you need all 5 regions, space requests across a minute rather than firing simultaneously
- **Daily data:** DAILY timeframe returns ~5,000 rows per region — download once and cache
