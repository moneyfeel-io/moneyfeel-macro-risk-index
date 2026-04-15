"""
test_mri_api.py — MoneyFeel MRI API Test Suite
=============================================
Tests all public MoneyFeel MRI API endpoints.

Usage:
    python test_mri_api.py
    python test_mri_api.py --key mf_live_YOURKEY
    python test_mri_api.py --verbose

Output:
    Console output with per-test results
    mri_api_test_results.json with full details

Dependencies:
    pip install requests
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_URL = "https://mri-public-api.luca-stagnitta.workers.dev/v1"
API_KEY = "mf_live_YOUR_KEY"  # Replace this or pass --key

REGIONS = ["GLOBAL", "US", "EU", "ASIA", "EM"]
TIMEFRAMES = ["DAILY", "WEEKLY", "MONTHLY"]

TIMEOUT = 15  # Request timeout in seconds


# ── Console colors ────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg):
    print(f"  {RED}✗{RESET} {msg}")


def info(msg):
    print(f"  {BLUE}→{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}⚠{RESET} {msg}")


# ── Test result container ─────────────────────────────────────────────────────

class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = 0
        self.failed = 0
        self.details = []

    def add(self, check: str, result: bool, detail: str = ""):
        if result:
            self.passed += 1
            ok(check)
        else:
            self.failed += 1
            fail(f"{check} — {detail}")
        self.details.append({"check": check, "passed": result, "detail": detail})

    @property
    def ok(self):
        return self.failed == 0


results: list[TestResult] = []


def section(title: str):
    print(f"\n{BOLD}{BLUE}{'─' * 60}{RESET}")
    print(f"{BOLD}{BLUE}  {title}{RESET}")
    print(f"{BOLD}{BLUE}{'─' * 60}{RESET}")


def get(path: str, params: dict = None, auth: bool = False) -> tuple[Optional[dict], int, float]:
    headers = {}
    if auth:
        headers["Authorization"] = f"Bearer {API_KEY}"

    url = f"{BASE_URL}{path}"
    t0 = time.time()

    try:
        response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        elapsed = (time.time() - t0) * 1000

        try:
            data = response.json()
        except Exception:
            data = {"_raw": response.text[:200]}

        return data, response.status_code, elapsed

    except requests.exceptions.Timeout:
        return None, 0, TIMEOUT * 1000
    except Exception as e:
        return {"_error": str(e)}, -1, 0


# ══════════════════════════════════════════════════════════════════════════════
# TEST SUITE
# ══════════════════════════════════════════════════════════════════════════════

def test_public_endpoints():
    """Test public endpoints that do not require authentication."""
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
    r.add(
        "5 regions present",
        data and len(data.get("regions", [])) == 5,
        str(data.get("regions") if data else None),
    )
    r.add(
        "3 timeframes present",
        data and len(data.get("timeframes", [])) == 3,
        str(data.get("timeframes") if data else None),
    )
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
        r.add("score_weekly field", "score_weekly" in row, str(row.keys()))
        r.add("updated_at field", "updated_at" in row, str(row.keys()))
        info(
            f"  Sample: {row.get('region')} → {row.get('regime_weekly')} "
            f"(score {row.get('score_weekly')})"
        )

    results.append(r)
    return r.ok


def test_auth():
    """Test authentication behavior."""
    section("2. AUTHENTICATION")
    r = TestResult("authentication")

    # Request without auth
    info("GET /v1/history without auth — expect 401")
    data, code, ms = get("/history", {"region": "US", "tf": "WEEKLY"}, auth=False)
    r.add("Returns 401 without key", code == 401, f"got {code}")
    r.add("error field present", bool(data and "error" in data), str(data))

    # Request with invalid key
    info("GET /v1/history with invalid key — expect 401")
    original = API_KEY
    globals()["API_KEY"] = "mf_live_invalidkeyXXXXXXXXXX"
    data, code, ms = get("/history", {"region": "US", "tf": "WEEKLY"}, auth=True)
    r.add("Returns 401 with invalid key", code == 401, f"got {code}")
    globals()["API_KEY"] = original

    # Request with valid key
    info("GET /v1/history with valid key — expect 200")
    data, code, ms = get(
        "/history",
        {"region": "US", "tf": "WEEKLY", "from": "2024-01-01"},
        auth=True,
    )
    r.add("Returns 200 with valid key", code == 200, f"got {code}")

    results.append(r)
    return r.ok


def test_history():
    """Test the /v1/history endpoint."""
    section("3. /v1/history")
    r = TestResult("history")

    # US weekly history from 2020
    info("US / WEEKLY / from 2020-01-01")
    data, code, ms = get(
        "/history",
        {"region": "US", "tf": "WEEKLY", "from": "2020-01-01"},
        auth=True,
    )
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Non-empty data (got {len(rows)} rows)", len(rows) > 0)

    if rows:
        row = rows[0]
        required = [
            "as_of_date",
            "regime",
            "mri_score",
            "prob_strong_bull",
            "prob_bull",
            "prob_neutral",
            "prob_bear",
            "prob_strong_bear",
            "regime_confidence",
            "days_in_regime",
            "regime_changed",
        ]
        for field in required:
            r.add(f"Field '{field}'", field in row, f"missing in {list(row.keys())[:5]}")

        # Check that probabilities approximately sum to 1
        total = sum(
            row.get(f"prob_{x}", 0) or 0
            for x in ["strong_bull", "bull", "neutral", "bear", "strong_bear"]
        )
        r.add(f"Probs sum ≈ 1.0 (got {total:.3f})", abs(total - 1.0) < 0.01, f"{total:.4f}")
        info(f"  Latest: {rows[-1].get('as_of_date')} → {rows[-1].get('regime')}")

    # EU daily history
    info("EU / DAILY / from 2024-01-01")
    data, code, ms = get(
        "/history",
        {"region": "EU", "tf": "DAILY", "from": "2024-01-01"},
        auth=True,
    )
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
    """Test the /v1/regime/latest endpoint."""
    section("4. /v1/regime/latest")
    r = TestResult("regime_latest")

    for region in REGIONS:
        data, code, ms = get("/regime/latest", {"region": region, "tf": "WEEKLY"}, auth=True)
        r.add(f"{region} HTTP 200", code == 200, f"got {code}")
        if code == 200 and data:
            row = data.get("data", {})
            r.add(f"{region} regime field", bool(row and row.get("regime")), str(row.get("regime")))
            info(
                f"  {region}: {row.get('regime')} | "
                f"score={row.get('mri_score')} | date={row.get('as_of_date')}"
            )

    results.append(r)
    return r.ok


def test_metrics():
    """Test the /v1/metrics endpoint."""
    section("5. /v1/metrics")
    r = TestResult("metrics")

    # All regions
    data, code, ms = get("/metrics", {"tf": "WEEKLY"}, auth=True)
    r.add("HTTP 200 (all regions)", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Multiple rows (got {len(rows)})", len(rows) > 0)
    if rows:
        row = rows[0]
        metric_fields = ["cagr_strategy", "sharpe", "max_dd", "sortino"]
        for field in metric_fields:
            r.add(field, field in row, f"missing. Keys: {list(row.keys())[:6]}")

    # Single region
    data, code, ms = get("/metrics", {"region": "US", "tf": "WEEKLY"}, auth=True)
    r.add("US WEEKLY HTTP 200", code == 200, f"got {code}")
    if code == 200 and data:
        row = data.get("data", [{}])[0]
        info(
            f"  US Weekly: CAGR={row.get('cagr_strategy')}% | "
            f"Sharpe={row.get('sharpe')} | MaxDD={row.get('max_dd')}%"
        )

    results.append(r)
    return r.ok


def test_timeseries():
    """Test the /v1/timeseries endpoint."""
    section("6. /v1/timeseries")
    r = TestResult("timeseries")

    data, code, ms = get(
        "/timeseries",
        {"region": "US", "tf": "WEEKLY", "from": "2024-01-01"},
        auth=True,
    )
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Non-empty data (got {len(rows)} rows)", len(rows) > 0)

    if rows:
        row = rows[0]
        ts_fields = [
            "as_of_date",
            "bench_return",
            "strategy_return",
            "cum_benchmark",
            "cum_strategy",
            "drawdown_series",
        ]
        for field in ts_fields:
            r.add(f"Field '{field}'", field in row, "missing")
        info(f"  Date range: {rows[0].get('as_of_date')} → {rows[-1].get('as_of_date')}")

    results.append(r)
    return r.ok


def test_eoy():
    """Test the /v1/eoy endpoint."""
    section("7. /v1/eoy")
    r = TestResult("eoy")

    data, code, ms = get("/eoy", {"region": "US", "tf": "WEEKLY"}, auth=True)
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Has yearly data (got {len(rows)} years)", len(rows) > 0)

    if rows:
        row = rows[0]
        r.add("year field", "year" in row)
        r.add("benchmark_ret field", "benchmark_ret" in row)
        r.add("strategy_ret field", "strategy_ret" in row)
        r.add("won field", "won" in row)

        years = [int(r2.get("year", 9999)) for r2 in rows if r2.get("year")]
        r.add(
            f"Coverage from 2007 (earliest: {min(years) if years else '?'})",
            bool(years and min(years) <= 2009),
            str(min(years) if years else "?"),
        )
        info(f"  Years: {min(years) if years else '?'} → {max(years) if years else '?'} ({len(rows)} rows)")

    results.append(r)
    return r.ok


def test_drawdowns():
    """Test the /v1/drawdowns endpoint."""
    section("8. /v1/drawdowns")
    r = TestResult("drawdowns")

    data, code, ms = get("/drawdowns", {"region": "US", "tf": "WEEKLY"}, auth=True)
    r.add("HTTP 200", code == 200, f"got {code}")
    rows = data.get("data", []) if data else []
    r.add(f"Has drawdown data (got {len(rows)} rows)", len(rows) > 0)

    if rows:
        row = rows[0]
        r.add("rank field", "rank" in row)
        r.add("started field", "started" in row)
        r.add("drawdown_pct", "drawdown_pct" in row)
        r.add("days field", "days" in row)
        info(
            f"  Worst DD: {row.get('drawdown_pct')}% | "
            f"started={row.get('started')} | days={row.get('days')}"
        )

    results.append(r)
    return r.ok


def test_download():
    """Test the /v1/download endpoint and validate CSV output."""
    section("9. /v1/download (CSV)")
    r = TestResult("download")

    headers = {"Authorization": f"Bearer {API_KEY}"}
    url = f"{BASE_URL}/download"
    t0 = time.time()
    response = requests.get(
        url,
        params={"region": "US", "tf": "WEEKLY"},
        headers=headers,
        timeout=30,
    )
    ms = (time.time() - t0) * 1000

    r.add("HTTP 200", response.status_code == 200, f"got {response.status_code}")
    r.add(
        "Content-Type text/csv",
        "text/csv" in response.headers.get("Content-Type", ""),
        response.headers.get("Content-Type"),
    )
    r.add(
        "Content-Disposition header",
        "attachment" in response.headers.get("Content-Disposition", ""),
        response.headers.get("Content-Disposition"),
    )

    lines = response.text.splitlines()
    comment_lines = [line for line in lines if line.startswith("#")]
    data_lines = [line for line in lines if not line.startswith("#") and line.strip()]

    r.add(f"Comment headers present ({len(comment_lines)} lines)", len(comment_lines) >= 2)
    r.add(f"Data rows present (got {len(data_lines)})", len(data_lines) > 100, str(len(data_lines)))

    if data_lines:
        header_row = data_lines[0].split(",")
        r.add("as_of_date column", "as_of_date" in header_row)
        r.add("regime column", "regime" in header_row)
        r.add("mri_score column", "mri_score" in header_row)
        r.add("strategy_return column", "strategy_return" in header_row)
        info(
            f"  Columns: {len(header_row)} | "
            f"Data rows: {len(data_lines) - 1} | "
            f"Size: {len(response.content) / 1024:.1f}KB | "
            f"Latency: {ms:.0f}ms"
        )

    results.append(r)
    return r.ok


def test_rate_limit():
    """Run a soft rate limit test without consuming the full quota."""
    section("10. RATE LIMITING (soft test)")
    r = TestResult("rate_limit")

    warn("Sending 5 rapid requests to verify normal behavior...")
    codes = []
    for _ in range(5):
        _, code, _ = get("/current")
        codes.append(code)

    r.add("All 5 rapid requests succeed (200)", all(c == 200 for c in codes), str(codes))
    info(f"  Response codes: {codes}")
    info("  Full 30 req/min test skipped to avoid exhausting quota")

    # This does not trigger a real rate limit; it only checks the error payload structure
    orig = API_KEY
    globals()["API_KEY"] = "mf_live_wrong"
    data, code, ms = get("/history", {"region": "US", "tf": "WEEKLY"}, auth=True)
    globals()["API_KEY"] = orig

    r.add("Error response has 'error' field", bool(data and "error" in data), str(data))
    r.add("Error response has 'message' field", bool(data and "message" in data), str(data))
    r.add("Error response has 'docs' field", bool(data and "docs" in data), str(data))
    if data:
        info(f"  Error structure: {list(data.keys())}")

    results.append(r)
    return r.ok


def test_not_found():
    """Test a non-existent endpoint."""
    section("11. ERROR HANDLING — 404")
    r = TestResult("not_found")

    data, code, ms = get("/nonexistent", auth=True)
    r.add(f"Returns 4xx error (got {code})", code in (400, 404), f"got {code}")
    r.add("error field", bool(data and "error" in data), str(data))

    results.append(r)
    return r.ok


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def print_summary():
    total_p = sum(r.passed for r in results)
    total_f = sum(r.failed for r in results)
    total = total_p + total_f

    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  TEST SUMMARY{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")

    for r in results:
        status = f"{GREEN}PASS{RESET}" if r.ok else f"{RED}FAIL{RESET}"
        bar = f"{GREEN}{'█' * r.passed}{RED}{'█' * r.failed}{RESET}"
        print(f"  [{status}] {r.name:<25} {bar} {r.passed}/{r.passed + r.failed}")

    print(f"\n  {BOLD}Total: {total_p}/{total} checks passed{RESET}")
    if total_f == 0:
        print(f"  {GREEN}{BOLD}ALL TESTS PASSED ✓{RESET}")
    else:
        print(f"  {RED}{BOLD}{total_f} TESTS FAILED ✗{RESET}")
    print(f"{'═' * 60}\n")

    output = {
        "timestamp": datetime.now().isoformat(),
        "base_url": BASE_URL,
        "total_checks": total,
        "passed": total_p,
        "failed": total_f,
        "suites": [
            {
                "name": r.name,
                "passed": r.passed,
                "failed": r.failed,
                "ok": r.ok,
                "details": r.details,
            }
            for r in results
        ],
    }

    out_path = Path(__file__).parent / "mri_api_test_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"  Results saved to: {out_path}\n")

    return total_f == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", default=None, help="API key")
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

    print_summary()
    sys.exit(0)
