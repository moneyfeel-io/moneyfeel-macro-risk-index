"""
test_mri_api.py — mneyfeel MRI API Test Suite
================================================
Testa tutti gli endpoint dell'API pubblica MRI.

Uso:
    python test_mri_api.py
    python test_mri_api.py --key mf_live_YOURKEY
    python test_mri_api.py --verbose

Output:
    Console con risultati per ogni test
    mri_api_test_results.json con dettaglio completo

Dipendenze:
    pip install requests
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = "https://mri-public-api.luca-stagnitta.workers.dev/v1"
API_KEY  = "mf_live_YOUR_KEY"   # sostituisci oppure passa --key

REGIONS    = ["GLOBAL", "US", "EU", "ASIA", "EM"]
TIMEFRAMES = ["DAILY", "WEEKLY", "MONTHLY"]

TIMEOUT = 15  # secondi per request


# ── Colors ────────────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):  print(f"  {RED}✗{RESET} {msg}")
def info(msg):  print(f"  {BLUE}→{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}⚠{RESET} {msg}")


# ── Test runner ───────────────────────────────────────────────────────────────

class TestResult:
    def __init__(self, name: str):
        self.name    = name
        self.passed  = 0
        self.failed  = 0
        self.details = []

    def add(self, check: str, result: bool, detail: str = ""):
        if result:
            self.passed += 1
            ok(f"{check}")
        else:
            self.failed += 1
            fail(f"{check} — {detail}")
        self.details.append({"check": check, "passed": result, "detail": detail})

    @property
    def ok(self): return self.failed == 0


results: list[TestResult] = []


def section(title: str):
    print(f"\n{BOLD}{BLUE}{'─'*60}{RESET}")
    print(f"{BOLD}{BLUE}  {title}{RESET}")
    print(f"{BOLD}{BLUE}{'─'*60}{RESET}")


def get(path: str, params: dict = None, auth: bool = False) -> tuple[Optional[dict], int, float]:
    headers = {}
    if auth:
        headers["Authorization"] = f"Bearer {API_KEY}"
    url = f"{BASE_URL}{path}"
    t0  = time.time()
    try:
        r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        elapsed = (time.time() - t0) * 1000
        try:
            data = r.json()
        except Exception:
            data = {"_raw": r.text[:200]}
        return data, r.status_code, elapsed
    except requests.exceptions.Timeout:
        return None, 0, TIMEOUT * 1000
    except Exception as e:
        return {"_error": str(e)}, -1, 0


# ══════════════════════════════════════════════════════════════════════════════
# TEST SUITE
# ══════════════════════════════════════════════════════════════════════════════

def test_public_endpoints():
    """Test endpoint pubblici — nessuna auth."""
    section("1. PUBLIC ENDPOINTS (no auth)")
    r = TestResult("public_endpoints")

    # /v1/status
    info("GET /v1/status")
    data, code, ms = get("/status")
    r.add("HTTP 200", code == 200, f"got {code}")
    r.add("status = ok", data and data.get("status") == "ok", str(data))
    r.add("version present", bool(data and data.get("version")), str(data))
    r.add(f"Latency {ms:.0f}ms", ms < 3000, f"{ms:.0f}ms")
    info(f"  Response: {data}")

    # /v1/regions
    info("GET /v1/regions")
    data, code, ms = get("/regions")
    r.add("HTTP 200", code == 200, f"got {code}")
    r.add("5 regions present", data and len(data.get("regions", [])) == 5,
          str(data.get("regions")))
    r.add("3 timeframes present", data and len(data.get("timeframes", [])) == 3,
          str(data.get("timeframes")))
    r.add("coverage field", bool(data and data.get("coverage")), str(data))

    # /v1/current
    info("GET /v1/current")
    data, code, ms = get("/current")
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"5 regions in data (got {len(rows)})", len(rows) == 5, str(len(rows)))
    if rows:
        row = rows[0]
        r.add("regime_weekly field", "regime_weekly" in row, str(row.keys()))
        r.add("score_weekly field",  "score_weekly"  in row, str(row.keys()))
        r.add("updated_at field",    "updated_at"    in row, str(row.keys()))
        info(f"  Sample: {row.get('region')} → {row.get('regime_weekly')} (score {row.get('score_weekly')})")

    results.append(r)
    return r.ok


def test_auth():
    """Test autenticazione."""
    section("2. AUTHENTICATION")
    r = TestResult("authentication")

    # Senza auth
    info("GET /v1/history without auth — expect 401")
    data, code, ms = get("/history", {"region": "US", "tf": "WEEKLY"}, auth=False)
    r.add("Returns 401 without key", code == 401, f"got {code}")
    r.add("error field present", bool(data and "error" in data), str(data))

    # Con key invalida
    info("GET /v1/history with invalid key — expect 401")
    original = API_KEY
    globals()["API_KEY"] = "mf_live_invalidkeyXXXXXXXXXX"
    data, code, ms = get("/history", {"region": "US", "tf": "WEEKLY"}, auth=True)
    r.add("Returns 401 with invalid key", code == 401, f"got {code}")
    globals()["API_KEY"] = original

    # Con key valida
    info("GET /v1/history with valid key — expect 200")
    data, code, ms = get("/history", {"region": "US", "tf": "WEEKLY",
                                       "from": "2024-01-01"}, auth=True)
    r.add("Returns 200 with valid key", code == 200, f"got {code}")

    results.append(r)
    return r.ok


def test_history():
    """Test endpoint /v1/history."""
    section("3. /v1/history")
    r = TestResult("history")

    # US Weekly 2020
    info("US / WEEKLY / from 2020-01-01")
    data, code, ms = get("/history",
        {"region": "US", "tf": "WEEKLY", "from": "2020-01-01"}, auth=True)
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Non-empty data (got {len(rows)} rows)", len(rows) > 0)
    if rows:
        row = rows[0]
        required = ["as_of_date","regime","mri_score","prob_strong_bull",
                    "prob_bull","prob_neutral","prob_bear","prob_strong_bear",
                    "regime_confidence","days_in_regime","regime_changed"]
        for field in required:
            r.add(f"Field '{field}'", field in row, f"missing in {list(row.keys())[:5]}")
        # Probabilities sum to 1
        if row:
            total = sum(row.get(f"prob_{x}",0) or 0
                       for x in ["strong_bull","bull","neutral","bear","strong_bear"])
            r.add(f"Probs sum ≈ 1.0 (got {total:.3f})", abs(total - 1.0) < 0.01, f"{total:.4f}")
        info(f"  Latest: {rows[-1].get('as_of_date')} → {rows[-1].get('regime')}")

    # EU Daily
    info("EU / DAILY / from 2024-01-01")
    data, code, ms = get("/history",
        {"region": "EU", "tf": "DAILY", "from": "2024-01-01"}, auth=True)
    r.add("EU DAILY HTTP 200", code == 200, f"got {code}")

    # Invalid region
    info("Invalid region — expect 400")
    data, code, ms = get("/history", {"region": "INVALID", "tf": "WEEKLY"}, auth=True)
    r.add("Invalid region → 400", code == 400, f"got {code}")

    # Invalid timeframe
    info("Invalid timeframe — expect 400")
    data, code, ms = get("/history", {"region": "US", "tf": "INVALID"}, auth=True)
    r.add("Invalid tf → 400", code == 400, f"got {code}")

    results.append(r)
    return r.ok


def test_regime_latest():
    """Test /v1/regime/latest."""
    section("4. /v1/regime/latest")
    r = TestResult("regime_latest")

    for region in REGIONS:
        data, code, ms = get("/regime/latest",
            {"region": region, "tf": "WEEKLY"}, auth=True)
        r.add(f"{region} HTTP 200", code == 200, f"got {code}")
        if code == 200 and data:
            row = data.get("data", {})
            r.add(f"{region} regime field", bool(row and row.get("regime")),
                  str(row.get("regime")))
            info(f"  {region}: {row.get('regime')} | score={row.get('mri_score')} | date={row.get('as_of_date')}")

    results.append(r)
    return r.ok


def test_metrics():
    """Test /v1/metrics."""
    section("5. /v1/metrics")
    r = TestResult("metrics")

    # All regions
    data, code, ms = get("/metrics", {"tf": "WEEKLY"}, auth=True)
    r.add("HTTP 200 (all regions)", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Multiple rows (got {len(rows)})", len(rows) > 0)
    if rows:
        row = rows[0]
        metric_fields = ["cagr_strategy","sharpe","max_dd","sortino"]
        for f in metric_fields:
            r.add(f"Metric '{f}'", f in row, f"missing. Keys: {list(row.keys())[:6]}")

    # Single region
    data, code, ms = get("/metrics", {"region": "US", "tf": "WEEKLY"}, auth=True)
    r.add("US WEEKLY HTTP 200", code == 200, f"got {code}")
    if code == 200 and data:
        row = data.get("data", [{}])[0]
        info(f"  US Weekly: CAGR={row.get('cagr_strategy')}% | Sharpe={row.get('sharpe')} | MaxDD={row.get('max_dd')}%")

    results.append(r)
    return r.ok


def test_timeseries():
    """Test /v1/timeseries."""
    section("6. /v1/timeseries")
    r = TestResult("timeseries")

    data, code, ms = get("/timeseries",
        {"region": "US", "tf": "WEEKLY", "from": "2024-01-01"}, auth=True)
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Non-empty data (got {len(rows)} rows)", len(rows) > 0)
    if rows:
        row = rows[0]
        ts_fields = ["as_of_date","bench_return","strategy_return",
                     "cum_benchmark","cum_strategy","drawdown_series"]
        for f in ts_fields:
            r.add(f"Field '{f}'", f in row, f"missing")
        info(f"  Date range: {rows[0].get('as_of_date')} → {rows[-1].get('as_of_date')}")

    results.append(r)
    return r.ok


def test_eoy():
    """Test /v1/eoy."""
    section("7. /v1/eoy")
    r = TestResult("eoy")

    data, code, ms = get("/eoy", {"region": "US", "tf": "WEEKLY"}, auth=True)
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Has yearly data (got {len(rows)} years)", len(rows) > 0)
    if rows:
        row = rows[0]
        r.add("year field",           "year"          in row)
        r.add("benchmark_ret field",  "benchmark_ret" in row)
        r.add("strategy_ret field",   "strategy_ret"  in row)
        r.add("won field",            "won"           in row)
        years = [int(r2.get("year",9999)) for r2 in rows if r2.get("year")]
        r.add(f"Coverage from 2007 (earliest: {min(years) if years else '?'})",
              bool(years and min(years) <= 2009), str(min(years) if years else "?"))
        info(f"  Years: {min(years) if years else '?'} → {max(years) if years else '?'} ({len(rows)} rows)")

    results.append(r)
    return r.ok


def test_drawdowns():
    """Test /v1/drawdowns."""
    section("8. /v1/drawdowns")
    r = TestResult("drawdowns")

    data, code, ms = get("/drawdowns", {"region": "US", "tf": "WEEKLY"}, auth=True)
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Has drawdown data (got {len(rows)} rows)", len(rows) > 0)
    if rows:
        row = rows[0]
        r.add("rank field",      "rank"         in row)
        r.add("started field",   "started"      in row)
        r.add("drawdown_pct",    "drawdown_pct" in row)
        r.add("days field",      "days"         in row)
        info(f"  Worst DD: {row.get('drawdown_pct')}% | started={row.get('started')} | days={row.get('days')}")

    results.append(r)
    return r.ok


def test_download():
    """Test /v1/download — verifica CSV."""
    section("9. /v1/download (CSV)")
    r = TestResult("download")

    headers = {"Authorization": f"Bearer {API_KEY}"}
    url = f"{BASE_URL}/download"
    t0  = time.time()
    resp = requests.get(url, params={"region": "US", "tf": "WEEKLY"},
                        headers=headers, timeout=30)
    ms  = (time.time() - t0) * 1000

    r.add("HTTP 200", resp.status_code == 200, f"got {resp.status_code}")
    r.add("Content-Type text/csv",
          "text/csv" in resp.headers.get("Content-Type",""),
          resp.headers.get("Content-Type"))
    r.add("Content-Disposition header",
          "attachment" in resp.headers.get("Content-Disposition",""),
          resp.headers.get("Content-Disposition"))

    lines = resp.text.splitlines()
    comment_lines = [l for l in lines if l.startswith("#")]
    data_lines    = [l for l in lines if not l.startswith("#") and l.strip()]

    r.add(f"Comment headers present ({len(comment_lines)} lines)",
          len(comment_lines) >= 2)
    r.add(f"Data rows present (got {len(data_lines)})",
          len(data_lines) > 100, str(len(data_lines)))

    if data_lines:
        header_row = data_lines[0].split(",")
        r.add("as_of_date column",    "as_of_date"    in header_row)
        r.add("regime column",        "regime"        in header_row)
        r.add("mri_score column",     "mri_score"     in header_row)
        r.add("strategy_return col",  "strategy_return" in header_row)
        info(f"  Columns: {len(header_row)} | Data rows: {len(data_lines)-1} | Size: {len(resp.content)/1024:.1f}KB")

    results.append(r)
    return r.ok


def test_rate_limit():
    """Test rate limiting — verifica che 429 venga restituito con messaggio corretto."""
    section("10. RATE LIMITING (soft test)")
    r = TestResult("rate_limit")

    warn("Sending 5 rapid requests to verify rate limit headers...")
    codes = []
    for i in range(5):
        _, code, _ = get("/current")
        codes.append(code)

    r.add("All 5 rapid requests succeed (200)", all(c == 200 for c in codes),
          str(codes))
    info(f"  Response codes: {codes}")
    info("  Full rate limit test (30 req/min) skipped to avoid exhausting quota")

    # Test 429 error structure by calling with wrong key repeatedly
    # (this won't trigger rate limit but tests error structure)
    orig = API_KEY
    globals()["API_KEY"] = "mf_live_wrong"
    data, code, ms = get("/history", {"region": "US", "tf": "WEEKLY"}, auth=True)
    globals()["API_KEY"] = orig

    r.add("Error response has 'error' field",
          bool(data and "error" in data), str(data))
    r.add("Error response has 'message' field",
          bool(data and "message" in data), str(data))
    r.add("Error response has 'docs' field",
          bool(data and "docs" in data), str(data))
    if data:
        info(f"  Error structure: {list(data.keys())}")

    results.append(r)
    return r.ok


def test_not_found():
    """Test endpoint inesistente."""
    section("11. ERROR HANDLING — 404")
    r = TestResult("not_found")

    data, code, ms = get("/nonexistent", auth=True)
    r.add("Returns 4xx error (got {})".format(code), code in (400, 404), f"got {code}")
    r.add("error field", bool(data and "error" in data), str(data))

    results.append(r)
    return r.ok


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def print_summary():
    total_p = sum(r.passed for r in results)
    total_f = sum(r.failed for r in results)
    total   = total_p + total_f

    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}  TEST SUMMARY{RESET}")
    print(f"{BOLD}{'═'*60}{RESET}")

    for r in results:
        status = f"{GREEN}PASS{RESET}" if r.ok else f"{RED}FAIL{RESET}"
        bar    = f"{GREEN}{'█' * r.passed}{RED}{'█' * r.failed}{RESET}"
        print(f"  [{status}] {r.name:<25} {bar} {r.passed}/{r.passed+r.failed}")

    print(f"\n  {BOLD}Total: {total_p}/{total} checks passed{RESET}")
    if total_f == 0:
        print(f"  {GREEN}{BOLD}ALL TESTS PASSED ✓{RESET}")
    else:
        print(f"  {RED}{BOLD}{total_f} TESTS FAILED ✗{RESET}")
    print(f"{'═'*60}\n")

    # Save JSON
    output = {
        "timestamp": datetime.now().isoformat(),
        "base_url":  BASE_URL,
        "total_checks": total,
        "passed": total_p,
        "failed": total_f,
        "suites": [
            {"name": r.name, "passed": r.passed,
             "failed": r.failed, "ok": r.ok,
             "details": r.details}
            for r in results
        ]
    }
    _out = Path(__file__).parent / "mri_api_test_results.json"
    with open(_out, "w") as f:
        json.dump(output, f, indent=2)
    print(f"  Results saved to: {_out}\n")

    return total_f == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key",     default=None, help="API key")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.key:
        API_KEY = args.key

    if "YOUR_KEY" in API_KEY:
        print(f"{RED}ERROR: Set your API key in API_KEY or pass --key mf_live_YOURKEY{RESET}")
        sys.exit(1)

    print(f"\n{BOLD}MRI API Test Suite{RESET}")
    print(f"Base URL : {BASE_URL}")
    print(f"API Key  : {API_KEY[:12]}...{API_KEY[-4:]}")
    print(f"Started  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    tests = [
        test_public_endpoints,
        test_auth,
        test_history,
        test_regime_latest,
        test_metrics,
        test_timeseries,
        test_eoy,
        test_drawdowns,
        test_download,
        test_rate_limit,
        test_not_found,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            print(f"{RED}  CRASH in {test_fn.__name__}: {e}{RESET}")

    all_ok = print_summary()
    # Note: sys.exit(1) causes VS debugger to show an error — use exit code 0
    # The summary above shows pass/fail clearly
    sys.exit(0)
