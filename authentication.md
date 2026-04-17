# Authentication

All authenticated endpoints require an API key passed in the `Authorization` header.

## Getting a Key

1. Register for free at [moneyfeel.it](https://moneyfeel.it)
2. Go to your [account page](https://moneyfeel.it/account)
3. Find the **MRI API Access** section → **Generate API Key**

Keys are free for all registered users. No credit card required.

## Using Your Key

Pass the key in the `Authorization` header:

```
Authorization: Bearer mf_live_YOUR_KEY
```

### curl
```bash
curl -H "Authorization: Bearer mf_live_YOUR_KEY" \
  "https://api.moneyfeel.ai/v1/history?region=US&tf=WEEKLY"
```

### Python
```python
import requests

headers = {"Authorization": "Bearer mf_live_YOUR_KEY"}
r = requests.get(
    "https://api.moneyfeel.ai/v1/history",
    params={"region": "US", "tf": "WEEKLY"},
    headers=headers
)
data = r.json()
```

### R
```r
library(httr2)

request("https://api.moneyfeel.ai/v1/history") |>
  req_url_query(region = "US", tf = "WEEKLY") |>
  req_headers(Authorization = "Bearer mf_live_YOUR_KEY") |>
  req_perform() |>
  resp_body_json()
```

### JavaScript / Node.js
```javascript
const response = await fetch(
  "https://api.moneyfeel.ai/v1/history?region=US&tf=WEEKLY",
  { headers: { "Authorization": "Bearer mf_live_YOUR_KEY" } }
);
const data = await response.json();
```

## Key Management

- Keys are permanent — they do not expire
- You can **regenerate** or **revoke** your key anytime from your account page
- Revocation is immediate — requests using a revoked key return `401` instantly
- You can only have one active key per account

## Security

- Never commit your API key to public repositories
- Store keys in environment variables or secret managers
- If your key is compromised, revoke it immediately from your account page

## Errors

```json
// Missing header
{"error": "missing_api_key", "message": "Authorization header required. Use: Authorization: Bearer mf_live_YOUR_KEY"}

// Invalid or revoked key
{"error": "invalid_api_key", "message": "API key not found or revoked. Generate a new one at moneyfeel.it/account."}
```
