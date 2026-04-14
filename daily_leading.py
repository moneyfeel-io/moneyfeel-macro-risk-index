"""
Pipeline LEADING - Predictive Valuation Engine

Differenze principali vs LAGGING:
- NTM-only nei COMPS (mode="leading")
- Street target blend (default 30%)
- Revision momentum (±15% tipico)
- Sentiment overlay e bull-bias sugli scenari
- Terminal growth più alto
- Guardrail Street con scenario shift
"""

from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import json
import yaml
from pathlib import Path
import sqlite3

from ..utils.logging import get_logger
from ..services.rf_registry import get_rf_for_symbol
from ..services.erp_crp_registry import get_erp_crp
from ..services.wacc import compute_wacc
from ..pipelines.triangulation import triangulate  # non usato direttamente ma safe-to-import
from ..services.forecast import compute_forecast
from ..services.rating import compute_rating

# Engines
from ..engines.comps import run_comps
from ..engines.dcf import run_dcf
from ..engines.ddm import run_ddm
from ..engines.residual_income import run_residual_income
from ..engines.sotp import run_sotp
# 🆕 v3.1: Monte Carlo DCF
from ..engines.dcf_monte_carlo_v31 import run_monte_carlo_dcf_v31

# Persistence
from ..persistence.results_writer import write_results_leading, export_results_parquet
from ..persistence.parameters_writer import write_parameters, export_parameters_parquet

# Utils
from ..utils.config import CONFIG

# DQS
from ..services.dqs import compute_dqs

# Value Trap
from ..services.value_trap_filter import evaluate_value_trap


logger = get_logger()

# ============================================
# Config Loader (LEADING)
# ============================================

def load_all_active_symbols(db) -> List[str]:
    return db.get_active_symbols()

def _safe_div(a, b):
    try:
        if a is None or b is None or b == 0:
            return None
        return float(a) / float(b)
    except Exception:
        return None

def _is_high_growth(info: Dict[str, Any]) -> bool:
    growth = info.get("revenue_growth") or info.get("ntm_revenue_growth")
    if growth and growth > 0.15:
        return True
    margin = _safe_div(info.get("ttm_ebitda"), info.get("ttm_total_revenue"))
    r40 = (growth or 0) * 100 + (margin or 0) * 100
    return r40 >= 40

def _fallback_price(db, symbol: str, jfd_price: Optional[float], info_price: Optional[float]) -> Optional[float]:
    if info_price is not None:
        return info_price
    last = db.get_last_eod_close(symbol)
    if last is not None:
        return last
    return jfd_price

# ============================================
# Street rating parser (guardrail)
# ============================================
def _parse_street_rating(row: Dict[str, Any]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """
    (Leading) Usa SEMPRE il numero se presente.
    Soglie Leading (Opzione A):
      <=1.5 strong_buy
      <=2.5 buy
      <=3.5 hold
      <=4.5 sell
      >4.5 strong_sell
    Ritorna (score_num, label_norm, key_norm).
    """
    score_num: Optional[float] = None
    label_norm: Optional[str] = None
    key_norm: Optional[str] = None
    try:
        txt = row.get("average_analyst_rating")
        if txt and isinstance(txt, str):
            parts = txt.split("-") if "-" in txt else txt.split()
            if parts:
                try:
                    score_num = float(parts[0].strip())
                except Exception:
                    score_num = None
        key = row.get("recommendation_key")
        if key and isinstance(key, str):
            key_norm = key.strip().lower()
    except Exception:
        pass

    def label_from_num(n: float) -> str:
        if n <= 1.5:  return "strong_buy"
        if n <= 2.5:  return "buy"
        if n <= 3.5:  return "hold"
        if n <= 4.5:  return "sell"
        return "strong_sell"

    if score_num is not None:
        label_norm = label_from_num(score_num)
    else:
        # fallback da recommendation_key
        if key_norm:
            if "strong" in key_norm and "buy" in key_norm:   label_norm = "strong_buy"
            elif "strong" in key_norm and "sell" in key_norm: label_norm = "strong_sell"
            elif "buy" in key_norm:                           label_norm = "buy"
            elif ("sell" in key_norm) or ("underperform" in key_norm) or ("reduce" in key_norm):
                label_norm = "sell"
            else:
                label_norm = "hold"
    return score_num, label_norm, key_norm

def get_sector_max_steps(sector):
    """Get sector-specific base max_steps"""
    from ..utils.config import CONFIG
    if not sector:
        return 2
    sector_cfg = CONFIG.get_sector_anchor_params()
    if sector in sector_cfg:
        return sector_cfg[sector].get("base_max_steps", 2)
    return sector_cfg.get("default", {}).get("base_max_steps", 2)

def _street_anchor_clamp(
    model_rating: str,
    street_score: Optional[float],
    dqs_score: Optional[float] = None,
    model_fv: Optional[float] = None,
    street_fv: Optional[float] = None,
    forecast_er: Optional[float] = None,
    sector: Optional[str] = None,
    mc_confidence: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    v2.14 - FIXED: Conservative clamp for negative Street consensus
    
    STLAM.MI case (FIXED):
    - Street: 2.78 = "Hold" (bucket 3)
    - Model: "Strong Buy" (bucket 0)
    - Logic: Street negative (>=2.5) → Conservative clamp to max(model, street-1) = Accumulate (2)
    
    Changes from v2.13:
    1. CHECK 0: Deny override if street_score >= 2.5 (Hold or worse)
    2. CAP max_steps to 2 if street negative
    3. Conservative clamp: max(model, street-1) if street >= 2.5 and model < street
    """
    
    from ..utils.config import CONFIG
    
    vp = CONFIG.valuation_policy() or {}
    guardrail_cfg = vp.get("street_anchor_guardrail", {})
    
    if not guardrail_cfg.get("enabled", True):
        return model_rating, None
    
    # ════════════════════════════════════════════════════════════════════════
    # 1. FUNDAMENTAL OVERRIDE (WITH DEFENSIVE CHECKS)
    # ════════════════════════════════════════════════════════════════════════

    override_cfg = guardrail_cfg.get("fundamental_override", {})
    override_denied = False

    if override_cfg.get("enabled", True):
        er_threshold = float(override_cfg.get("er_threshold", 0.40))
    
        if forecast_er and forecast_er > er_threshold:
            
            # CHECK 0: STREET CONSENSUS NEGATIVE? (PRIORITY)
            if street_score and street_score >= 2.5:
                override_denied = True
                logger.warning(
                    f"[STREET-ANCHOR] Fundamental override DENIED: "
                    f"Street consensus negative (score={street_score:.2f}), "
                    f"ER={forecast_er:.1%}"
                )

            # CHECK 0.5: MC CONFIDENCE LOW?
            elif mc_confidence and mc_confidence == "low":
                override_denied = True
                logger.warning(
                    f"[STREET-ANCHOR] Override DENIED: Low MC confidence, ER={forecast_er:.1%}"
                )
            
            # CHECK 1: DQS TOO LOW?
            elif dqs_score and dqs_score < 0.70:
                override_denied = True
                logger.warning(
                    f"[STREET-ANCHOR] Fundamental override DENIED: "
                    f"Low DQS ({dqs_score:.3f}), ER={forecast_er:.1%}"
                )
            
            # CHECK 2: DIVERGENCE TOO HIGH?
            elif model_fv and street_fv and street_fv > 0:
                divergence = abs(model_fv - street_fv) / street_fv
            
                if divergence > 1.50:
                    override_denied = True
                    logger.warning(
                        f"[STREET-ANCHOR] Fundamental override DENIED: "
                        f"Massive divergence ({divergence:.0%}), "
                        f"Model FV={model_fv:.2f} vs Street FV={street_fv:.2f}, "
                        f"ER={forecast_er:.1%}"
                    )
                
                else:
                    dqs_str = f"{dqs_score:.3f}" if dqs_score is not None else "N/A"
                    div_str = f"{divergence:.0%}"
                
                    logger.info(
                        f"[STREET-ANCHOR] Fundamental override APPROVED: "
                        f"ER={forecast_er:.1%} > {er_threshold:.0%}, "
                        f"DQS={dqs_str}, divergence={div_str}"
                    )
                    return model_rating, f"Fundamental override: ER={forecast_er:.1%}"

    # ════════════════════════════════════════════════════════════════════════
    # 2. COMPUTE ADAPTIVE MAX_STEPS (DQS + DIVERGENCE AWARE)
    # ════════════════════════════════════════════════════════════════════════

    adaptive_cfg = guardrail_cfg.get("adaptive_clamp", {})
    sector_base_max_steps = get_sector_max_steps(sector)
    
    if override_denied and model_fv and street_fv and street_fv > 0:
        divergence = abs(model_fv - street_fv) / street_fv
        if divergence > 1.50:
            max_steps = 1
            logger.info(
                f"[STREET-ANCHOR] Forced max_steps=1 (massive divergence {divergence:.0%})"
            )
        else:
            max_steps = 1
    
    elif adaptive_cfg.get("enabled", True) and dqs_score is not None and model_fv and street_fv and street_fv > 0:
        divergence = abs(model_fv - street_fv) / street_fv
        
        high_cfg = adaptive_cfg.get("high_confidence", {})
        dqs_high = float(high_cfg.get("dqs_threshold", 0.80))
        div_high = float(high_cfg.get("divergence_threshold", 0.20))
        
        low_cfg = adaptive_cfg.get("low_confidence", {})
        dqs_low = float(low_cfg.get("dqs_threshold", 0.70))
        div_low = float(low_cfg.get("divergence_threshold", 0.40))
        
        if dqs_score > dqs_high and divergence < div_high:
            max_steps = sector_base_max_steps
        elif dqs_score < dqs_low or divergence > div_low:
            max_steps = max(1, sector_base_max_steps - 1)
        else:
            max_steps = sector_base_max_steps
    
    elif dqs_score is not None:
        high_cfg = adaptive_cfg.get("high_confidence", {})
        low_cfg = adaptive_cfg.get("low_confidence", {})
        
        if dqs_score > float(high_cfg.get("dqs_threshold", 0.80)):
            max_steps = sector_base_max_steps
        elif dqs_score < float(low_cfg.get("dqs_threshold", 0.70)):
            max_steps = max(1, sector_base_max_steps - 1)
        else:
            max_steps = sector_base_max_steps
    else:
        max_steps = sector_base_max_steps
    
    # CAP MAX_STEPS IF STREET NEGATIVE
    if street_score and street_score >= 2.5 and max_steps > 2:
        max_steps = 2

    if street_score and street_score >= 2.5 and max_steps > 2:
        max_steps = 2
    
    logger.info(
        f"[STREET-ANCHOR] {sector or 'Unknown'} sector: "
        f"base_max_steps={sector_base_max_steps}, adjusted={max_steps}"
    )
    
    # ════════════════════════════════════════════════════════════════════════
    # 3. LADDER & BUCKET MAPPING
    # ════════════════════════════════════════════════════════════════════════
    
    ladder = ["Strong Buy", "Buy", "Accumulate", "Hold", "Reduce", "Sell", "Strong Sell"]
    idx = {r: i for i, r in enumerate(ladder)}

    def bucket_model(r: str) -> int:
        aliases = {
            "strongbuy": "Strong Buy", "strong_buy": "Strong Buy",
            "neutral": "Hold", "market perform": "Hold", "equal weight": "Hold",
            "underperform": "Sell", "underweight": "Sell"
        }
        rl = r.strip().lower() if r else ""
        if rl in aliases:
            return idx[aliases[rl]]
        for name in ladder:
            if rl == name.lower():
                return idx[name]
        return idx["Hold"]

    def bucket_street(s: float) -> int:
        if s <= 1.5:
            return 0
        if s <= 2.5:
            return 1
        if s <= 3.5:
            return 3
        if s <= 4.5:
            return 5
        return 6

    # ════════════════════════════════════════════════════════════════════════
    # 4. CLAMP LOGIC
    # ════════════════════════════════════════════════════════════════════════
    
    if street_score is None:
        return model_rating, None

    m = bucket_model(model_rating)
    s = bucket_street(street_score)
    
    # CONSERVATIVE CLAMP IF STREET NEGATIVE (v2.15)
    if street_score >= 2.5 and m < s:
    
        # Calcola divergence se disponibile
        divergence = None
        if model_fv and street_fv and street_fv > 0:
            divergence = abs(model_fv - street_fv) / street_fv
    
        # REGOLA 1: Street >= 3.0 (Hold/Negative) → HOLD
        if street_score >= 3.0:
            clamped_idx = max(m, s)
            clamped_rating = ladder[clamped_idx]
            logger.info(
                f"[STREET-ANCHOR] CONSERVATIVE CLAMP (street negative): "
                f"{model_rating} -> {clamped_rating} "
                f"(street_score={street_score:.2f})"
            )
            return clamped_rating, f"Street-anchor: conservative clamp->Hold (street {street_score:.2f})"
    
        # REGOLA 2: Street 2.5-3.0 + Divergence >150% → HOLD
        elif divergence and divergence > 1.50:
            clamped_idx = max(m, s)
            clamped_rating = ladder[clamped_idx]
            logger.warning(
                f"[STREET-ANCHOR] EXTREME DIVERGENCE CLAMP: "
                f"{model_rating} -> {clamped_rating} "
                f"(divergence {divergence:.0%}, street {street_score:.2f})"
            )
            return clamped_rating, f"Street-anchor: extreme divergence->Hold (div {divergence:.0%})"
    
        # REGOLA 3: Street 2.5-3.0 + Divergence <150% → ACCUMULATE
        else:
            clamped_idx = max(m, s - 1)
            clamped_rating = ladder[clamped_idx]
            logger.info(
                f"[STREET-ANCHOR] CONSERVATIVE CLAMP: "
                f"{model_rating} -> {clamped_rating} "
                f"(street_score={street_score:.2f})"
            )
            return clamped_rating, "Street-anchor: conservative clamp->Accumulate"
    
    # AGGRESSIVE CLAMP IF MASSIVE DIVERGENCE
    if override_denied and model_fv and street_fv and street_fv > 0:
        divergence = abs(model_fv - street_fv) / street_fv
        
        if divergence > 1.50:
            if m < s:
                clamped_idx = min(s + 1, 6)
                clamped_rating = ladder[clamped_idx]
                
                logger.info(
                    f"[STREET-ANCHOR] AGGRESSIVE CLAMP (divergence {divergence:.0%}): "
                    f"{model_rating} -> {clamped_rating} "
                    f"(street_score={street_score:.2f} [{ladder[s]}], forced more conservative)"
                )
                
                return clamped_rating, (
                    f"Street-anchor: street_score={street_score:.2f} [{ladder[s]}] "
                    f"aggressive clamp->{clamped_rating} (divergence {divergence:.0%})"
                )
            
            elif m > s:
                clamped_idx = max(s - 1, 0)
                clamped_rating = ladder[clamped_idx]
                
                logger.info(
                    f"[STREET-ANCHOR] AGGRESSIVE CLAMP (divergence {divergence:.0%}): "
                    f"{model_rating} -> {clamped_rating} "
                    f"(street_score={street_score:.2f} [{ladder[s]}], forced more optimistic)"
                )
                
                return clamped_rating, (
                    f"Street-anchor: street_score={street_score:.2f} [{ladder[s]}] "
                    f"aggressive clamp->{clamped_rating} (divergence {divergence:.0%})"
                )
    
    # NORMAL CLAMP
    low = max(0, s - max_steps)
    high = min(6, s + max_steps)
    
    if m < low:
        clamped_rating = ladder[low]
        logger.info(
            f"[STREET-ANCHOR] CLAMP APPLIED: "
            f"{model_rating} -> {clamped_rating} "
            f"(street_score={street_score:.2f} [{ladder[s]}] ±{max_steps})"
        )
        return clamped_rating, (
            f"Street-anchor: street_score={street_score:.2f} [{ladder[s]}] "
            f"±{max_steps} clamp->{clamped_rating}"
        )
    
    if m > high:
        clamped_rating = ladder[high]
        logger.info(
            f"[STREET-ANCHOR] CLAMP APPLIED: "
            f"{model_rating} -> {clamped_rating} "
            f"(street_score={street_score:.2f} [{ladder[s]}] ±{max_steps})"
        )
        return clamped_rating, (
            f"Street-anchor: street_score={street_score:.2f} [{ladder[s]}] "
            f"±{max_steps} clamp->{clamped_rating}"
        )
    
    return model_rating, None

# ============================================
# CORE DATA (NTM-focused) + Street targets normalized
# ============================================
def load_core_data(db, symbol: str, as_of_date: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    🆕 v2.9 - COMPLETE REWRITE: Split-safe + Currency-normalized price loading
    
    CRITICAL FIXES:
    ═══════════════════════════════════════════════════════════════════════════
    1. SPLIT HANDLING (NFLX fix):
       - Primary source: eod_prices_daily.close (ALWAYS latest, split-adjusted)
       - Fallback: info_snapshot.current_price → julia_fundamental_data.price
       - Why: valuation_snapshot can be 1 day stale (pre-split)
    
    2. CURRENCY NORMALIZATION (UK stocks fix):
       - Detect sub-unit currencies (GBp, GBX, ZAc, ILa)
       - Normalize: price /= 100, currency = base (GBp → GBP)
       - Apply to: price, market_cap, enterprise_value, street targets
       - Why: EOD prices stored in pence, valuation needs pounds
    
    3. STREET TARGET FX CONVERSION:
       - If target in sub-unit → normalize to base
       - If base != financial_currency → apply FX rate
       - Preserve original for audit (target_mean_raw)
    
    ═══════════════════════════════════════════════════════════════════════════
    
    Priority hierarchy:
    1. EOD price (latest split-adjusted) ✅ PRIMARY
    2. Info snapshot (current session)
    3. Julia aggregated (fallback)
    
    Args:
        db: Database manager
        symbol: Stock symbol (e.g., "NFLX", "BP.L")
        as_of_date: Valuation date
        config: Config dict (not used)
    
    Returns:
        data: Complete stock data dict with normalized prices/currencies
    
    Example transformations:
        NFLX (post-split 10:1 on 17 Nov):
        - EOD: $106.97 (split-adjusted) ✅
        - Snapshot: $1,154 (stale) ❌
        → Uses EOD $106.97 ✅
        
        BP.L (UK pence):
        - EOD: 453.85 GBp (raw)
        - After norm: 4.54 GBP ✅
        - Street: 626 GBp → 6.26 GBP ✅
    """
    
    # ════════════════════════════════════════════════════════════════════════
    # STEP 1: GET LATEST EOD PRICE (PRIMARY SOURCE - SPLIT-SAFE)
    # ════════════════════════════════════════════════════════════════════════
    
    price_eod_raw = db.get_last_eod_close(symbol)
    
    if price_eod_raw:
        logger.debug(f"[PRICE-SOURCE] {symbol}: EOD close {price_eod_raw:.2f} (latest, split-adjusted)")
    else:
        # Fallback hierarchy if EOD missing
        logger.warning(f"[PRICE-SOURCE] {symbol}: EOD missing, trying fallbacks...")
        
        info = db.get_info_snapshot_fields(symbol, ["current_price"]) or {}
        price_eod_raw = info.get("current_price")
        
        if price_eod_raw:
            logger.warning(f"[PRICE-SOURCE] {symbol}: Using info_snapshot {price_eod_raw:.2f}")
        else:
            jfd = db.get_julia_row(symbol) or {}
            price_eod_raw = jfd.get("price")
            
            if price_eod_raw:
                logger.warning(f"[PRICE-SOURCE] {symbol}: Using julia_row {price_eod_raw:.2f} (last resort)")
            else:
                logger.error(f"[PRICE-SOURCE] {symbol}: NO PRICE FOUND! Cannot valuate.")
                price_eod_raw = None
    
    # ════════════════════════════════════════════════════════════════════════
    # STEP 2: LOAD VALUATION SNAPSHOT (FOR NON-PRICE DATA)
    # ════════════════════════════════════════════════════════════════════════
    
    vs = db.get_valuation_snapshot_latest(symbol)
    
    if vs:
        # Extract fundamentals (NOT price - we use EOD price_eod_raw)
        currency_raw = vs.get("currency")
        financial_currency = vs.get("financial_currency")
        fx_rate = vs.get("fx_rate")
        market_cap_raw = vs.get("market_cap")
        enterprise_value_raw = vs.get("enterprise_value")
        
        revenue_ttm = vs.get("ttm_total_revenue")
        ebitda_ttm = vs.get("ttm_ebitda")
        fcf_ttm = vs.get("ttm_free_cashflow")
        
        # Street targets (raw, need normalization)
        target_mean_raw = vs.get("target_mean")
        target_median_raw = vs.get("target_median")
        target_high_raw = vs.get("target_high")
        target_low_raw = vs.get("target_low")
        
        analyst_count = vs.get("num_analyst_opinions")
        
    else:
        # Fallback if snapshot missing
        logger.warning(f"{symbol}: valuation_snapshots missing, using fallback sources")
        
        jfd = db.get_julia_row(symbol) or {}
        info_fields = [
            "current_price", "total_debt", "total_cash", "beta", "dividend_rate",
            "currency", "financial_currency", "shares_outstanding"
        ]
        info = db.get_info_snapshot_fields(symbol, info_fields) or {}
        
        currency_raw = jfd.get("currency") or info.get("currency")
        financial_currency = jfd.get("financial_currency") or info.get("financial_currency")
        fx_rate = None
        market_cap_raw = jfd.get("market_cap")
        enterprise_value_raw = jfd.get("enterprise_value")
        revenue_ttm = jfd.get("ttm_total_revenue")
        ebitda_ttm = jfd.get("ttm_ebitda")
        fcf_ttm = jfd.get("ttm_free_cashflow")
        
        target_mean_raw = jfd.get("target_mean")
        target_median_raw = jfd.get("target_median")
        target_high_raw = None
        target_low_raw = None
        
        analyst_count = jfd.get("num_analyst_opinions")
    
    # ════════════════════════════════════════════════════════════════════════
    # STEP 3: CURRENCY NORMALIZATION (UK STOCKS FIX)
    # ════════════════════════════════════════════════════════════════════════
    
    # Sub-unit currency map: (base_currency, divisor)
    SUBUNIT_MAP = {
        "GBp": ("GBP", 100.0),  # UK pence → pounds
        "GBX": ("GBP", 100.0),  # UK pence (alt code)
        "ZAc": ("ZAR", 100.0),  # South African cents → rand
        "ILa": ("ILS", 100.0),  # Israeli agorot → shekel
    }
    
    # Check if currency needs normalization
    if currency_raw and currency_raw in SUBUNIT_MAP:
        base_currency, divisor = SUBUNIT_MAP[currency_raw]
        
        # Normalize price (pence → pounds)
        if price_eod_raw:
            price_eod = price_eod_raw / divisor
            logger.info(
                f"[CURRENCY-NORM] {symbol}: "
                f"Price {price_eod_raw:.2f} {currency_raw} → {price_eod:.2f} {base_currency}"
            )
        else:
            price_eod = None
        
        # Normalize market cap & enterprise value
        if market_cap_raw:
            market_cap = market_cap_raw / divisor
        else:
            market_cap = None
        
        if enterprise_value_raw:
            enterprise_value = enterprise_value_raw / divisor
        else:
            enterprise_value = None
        
        # Update currency to base
        currency = base_currency
        
        logger.info(
            f"[CURRENCY-NORM] {symbol}: "
            f"Currency normalized {currency_raw} → {base_currency}"
        )
    
    else:
        # No normalization needed (USD, EUR, etc.)
        price_eod = price_eod_raw
        currency = currency_raw
        market_cap = market_cap_raw
        enterprise_value = enterprise_value_raw
        
        if price_eod:
            logger.debug(f"[CURRENCY-NORM] {symbol}: No normalization needed (currency={currency})")
    
    # ════════════════════════════════════════════════════════════════════════
    # STEP 4: STREET TARGET NORMALIZATION + FX CONVERSION
    # ════════════════════════════════════════════════════════════════════════
    
    if target_mean_raw and currency_raw and currency_raw in SUBUNIT_MAP:
        # Street target in sub-unit currency (e.g., 626 GBp)
        base_ccy, divisor = SUBUNIT_MAP[currency_raw]
        
        # Normalize to base currency (626 GBp → 6.26 GBP)
        target_mean_base = target_mean_raw / divisor
        target_median_base = target_median_raw / divisor if target_median_raw else None
        target_high_base = target_high_raw / divisor if target_high_raw else None
        target_low_base = target_low_raw / divisor if target_low_raw else None
        
        # Apply FX conversion if base != financial_currency
        if fx_rate and base_ccy != financial_currency:
            target_mean = target_mean_base * fx_rate
            target_median = target_median_base * fx_rate if target_median_base else None
            target_high = target_high_base * fx_rate if target_high_base else None
            target_low = target_low_base * fx_rate if target_low_base else None
            
            logger.info(
                f"[STREET-TARGET] {symbol}: "
                f"{target_mean_raw:.2f} {currency_raw} → "
                f"{target_mean_base:.2f} {base_ccy} → "
                f"{target_mean:.2f} {financial_currency} (FX={fx_rate:.4f})"
            )
        else:
            # No FX conversion needed
            target_mean = target_mean_base
            target_median = target_median_base
            target_high = target_high_base
            target_low = target_low_base
            
            logger.info(
                f"[STREET-TARGET] {symbol}: "
                f"{target_mean_raw:.2f} {currency_raw} → {target_mean:.2f} {base_ccy}"
            )
    
    else:
        # Street target in standard currency (no normalization)
        target_mean = target_mean_raw
        target_median = target_median_raw
        target_high = target_high_raw
        target_low = target_low_raw
    
    # ════════════════════════════════════════════════════════════════════════
    # STEP 5: LOAD ADDITIONAL FIELDS (DIVIDEND, ROE, BETA, ETC.)
    # ════════════════════════════════════════════════════════════════════════
    
    jfd = db.get_julia_row(symbol) or {}
    info = db.get_info_snapshot_fields(symbol, [
        "shares_outstanding", "beta", "dividend_rate", "payout_ratio", "dividend_yield",
        "total_debt", "total_cash", "two_hundred_day_average", "return_on_equity"
    ]) or {}
    
    # Dividend fields
    dividend_rate = info.get("dividend_rate") or jfd.get("dividend_rate")
    payout_ratio = info.get("payout_ratio") or jfd.get("payout_ratio")
    dividend_yield_db = info.get("dividend_yield") or jfd.get("dividend_yield")
    
    # Calculate dividend yield if not in DB
    if dividend_yield_db:
        dividend_yield = dividend_yield_db
    elif dividend_rate and price_eod and price_eod > 0:
        dividend_yield = dividend_rate / price_eod
    else:
        dividend_yield = None
    
    # ROE and beta
    roe = info.get("return_on_equity") or jfd.get("return_on_equity")
    beta_val = info.get("beta") or jfd.get("beta")
    
    # NTM estimates (from earnings_estimates table)
    eps_ntm = db.compute_ntm_eps_interpolated(symbol)
    rev_ntm = db.compute_ntm_revenue_interpolated(symbol)
    
    # Analyst metrics
    surprises = db.get_recent_surprises(symbol, limit=4)
    price_vol_ratio = db.get_price_vol_ratio(symbol, window=60)
    
    # Peers
    peers = db.get_peers_metrics(symbol) or []
    sector_pool = db.get_sector_pool_metrics(jfd.get("sector"))
    
    # ════════════════════════════════════════════════════════════════════════
    # STEP 6: BUILD DATA DICTIONARY
    # ════════════════════════════════════════════════════════════════════════
    
    data = {
        # ═══ IDENTITY ═══
        "symbol": symbol,
        "sector": jfd.get("sector"),
        "industry": jfd.get("industry"),
        "country": jfd.get("country"),
        
        # ═══ CURRENCY & PRICE (NORMALIZED) ═══
        "currency": currency,                    # ✅ Normalized (GBp → GBP)
        "financial_currency": financial_currency,
        "fx_rate": fx_rate,
        "price": price_eod,                      # ✅ From EOD (split-adjusted, normalized)
        "price_eod": price_eod,                  # ✅ From EOD (split-adjusted, normalized)
        
        # ═══ MARKET DATA ═══
        "shares_outstanding": info.get("shares_outstanding") or jfd.get("shares_outstanding"),
        "market_cap": market_cap,                # ✅ Normalized if sub-unit
        "enterprise_value": enterprise_value,    # ✅ Normalized if sub-unit
        
        # ═══ BALANCE SHEET ═══
        "total_debt": info.get("total_debt"),
        "cash": info.get("total_cash"),
        
        # ═══ INCOME STATEMENT (TTM) ═══
        "revenue_ttm": revenue_ttm,
        "ttm_ebitda": ebitda_ttm,
        "ttm_ebit": jfd.get("ttm_ebit"),
        "ttm_free_cashflow": fcf_ttm,
        "ebitda_ttm": ebitda_ttm,
        "ebit_ttm": jfd.get("ttm_ebit"),
        "fcf_ttm": fcf_ttm,
        "ebitda_margin_ttm": _safe_div(ebitda_ttm, revenue_ttm),
        
        # ═══ PER-SHARE METRICS ═══
        "bvps": jfd.get("book_value"),
        
        # ═══ PROFITABILITY ═══
        "return_on_equity": roe,
        "operating_margin": jfd.get("operating_margin"),
        "profit_margin": jfd.get("profit_margin"),
        "roe_ttm": jfd.get("return_on_equity"),
        
        # ═══ RISK ═══
        "beta": beta_val,
        
        # ═══ ESTIMATES (NTM) ═══
        "eps_ntm": eps_ntm,
        "eps_ntm_high": None,
        "eps_ntm_low": None,
        "revenue_ntm": rev_ntm,
        
        # ═══ DIVIDEND ═══
        "dividend_rate": dividend_rate,
        "dividend_yield": dividend_yield,
        "payout_ratio": payout_ratio,
        "dividend_rate_raw": info.get("dividend_rate") or jfd.get("dividend_rate"),
        
        # ═══ ANALYST COVERAGE ═══
        "analyst_count": analyst_count,
        
        # ═══ GROWTH ═══
        "ntm_eps_growth": jfd.get("ntm_eps_growth"),
        "ntm_revenue_growth": jfd.get("ntm_revenue_growth"),
        
        # ═══ STREET TARGETS (NORMALIZED + FX CONVERTED) ═══
        "target_mean": target_mean,              # ✅ Normalized + FX converted
        "target_median": target_median,          # ✅ Normalized + FX converted
        "target_high": target_high,              # ✅ Normalized + FX converted
        "target_low": target_low,                # ✅ Normalized + FX converted
        
        # ═══ TECHNICAL ═══
        "two_hundred_day_average": info.get("two_hundred_day_average"),
        
        # ═══ COMPS ═══
        "peer_count": len(peers),
        "peers_metrics": peers,
        "sector_pool_metrics": sector_pool,
        
        # ═══ QUALITY SIGNALS ═══
        "surprise_series": [s.get("surprise_percent") for s in surprises],
        "price_60d_vol_ratio": price_vol_ratio,
        "fx_consistent": (currency == financial_currency),
        
        # ═══ FLAGS ═══
        "high_growth_flag": _is_high_growth(jfd),
        "conglomerate_flag": jfd.get("is_conglomerate"),
        
        # ═══ SOTP ═══
        "segments": db.get_segments(symbol)
    }
    
    # ════════════════════════════════════════════════════════════════════════
    # STEP 7: AUDIT LOG
    # ════════════════════════════════════════════════════════════════════════
    
    # Format values safely (handle None)
    price_str = f"{price_eod:.2f}" if price_eod else "N/A"
    fx_str = f"{fx_rate:.4f}" if fx_rate else "N/A"
    target_str = f"{target_mean:.2f}" if target_mean else "N/A"

    logger.info(
        f"[DATA-SUMMARY] {symbol}: "
        f"price={price_str} {currency}, "
        f"fin_ccy={financial_currency}, "
        f"fx={fx_str}, "
        f"target={target_str}"
    )
    
    # Debug log for Financial Services ROE
    if data.get("sector") == "Financial Services":
        logger.debug(
            f"[DATA-DEBUG] {symbol}: "
            f"ROE={roe} (info={info.get('return_on_equity')}, jfd={jfd.get('return_on_equity')})"
        )
    
    return data

# ============================================
# Audit helpers
# ============================================
def _implied_multiples(fv: Optional[float], data: Dict[str, Any]) -> Dict[str, Any]:
    """Implied multiples al fair value per audit."""
    implied = {}
    try:
        fv_num = float(fv) if fv is not None else None
    except Exception:
        fv_num = None
    if fv_num is None:
        return implied

    eps_ntm = data.get("eps_ntm")
    ebitda_ttm = data.get("ttm_ebitda")
    revenue_ntm = data.get("revenue_ntm")
    shares = data.get("shares_outstanding")
    debt = data.get("total_debt") or 0.0
    cash = data.get("total_cash") or data.get("cash") or 0.0
    net_debt = max((debt or 0.0) - (cash or 0.0), 0.0)

    try:
        if eps_ntm and eps_ntm > 0:
            implied["pe_ntm"] = fv_num / float(eps_ntm)
    except Exception:
        pass

    try:
        if ebitda_ttm and ebitda_ttm > 0 and shares and shares > 0:
            ev = fv_num * float(shares) + net_debt
            implied["ev_ebitda_ltm"] = ev / float(ebitda_ttm)
    except Exception:
        pass

    try:
        if revenue_ntm and revenue_ntm > 0 and shares and shares > 0:
            ev = fv_num * float(shares) + net_debt
            implied["ev_sales_ntm"] = ev / float(revenue_ntm)
    except Exception:
        pass

    return implied

def _log_probe(symbol: str,
               data: Dict[str, Any],
               macro: Dict[str, Any],
               wacc: Dict[str, Any],
               dqs: Dict[str, Any],
               dcf: Optional[Dict[str, Any]],
               comps: Optional[Dict[str, Any]],
               ddm: Optional[Dict[str, Any]],
               residual: Optional[Dict[str, Any]],
               tri: Dict[str, Any],
               forecast: Dict[str, Any],
               rating: str,
               rating_reason: str,
               revision: Dict[str, Any],
               sentiment: Dict[str, Any],
               cycle_phase: str) -> None:
    """Logging strutturato (LEADING)."""
    inputs = {
        "price": data.get("price_eod"),
        "shares_outstanding": data.get("shares_outstanding"),
        "ttm_free_cashflow": data.get("ttm_free_cashflow"),
        "ttm_ebitda": data.get("ttm_ebitda"),
        "revenue_ttm": data.get("revenue_ttm"),
        "eps_ntm": data.get("eps_ntm"),
        "revenue_ntm": data.get("revenue_ntm"),
        "total_debt": data.get("total_debt"),
        "cash": data.get("total_cash") or data.get("cash"),
        "beta": data.get("beta"),
        "peers_count": data.get("peer_count"),
        "price_60d_vol_ratio": data.get("price_60d_vol_ratio"),
        "currency": data.get("financial_currency"),
        "sector": data.get("sector"),
        "target_mean": data.get("target_mean"),
        "revision_momentum": revision.get("score"),
        "sentiment_ratio": sentiment.get("ratio"),
        "cycle_phase": cycle_phase
    }

    waccs = {
        "rf": macro.get("rf"),
        "erp_us": macro.get("erp_us"),
        "crp": macro.get("crp_country"),
        "erp_country": macro.get("erp_country"),
        "wacc": wacc.get("wacc"),
        "ke": wacc.get("ke"),
        "kd": wacc.get("kd"),
        "beta_used": wacc.get("beta_used"),
        "tax_rate_used": wacc.get("tax_rate_used"),
        "spread_proxy": wacc.get("spread_proxy")
    }

    dcf_ass = {}
    if dcf:
        dcf_ass = dcf.get("assumptions", {})
        dcf_ass["horizon"] = dcf.get("horizon_years")
        dcf_ass["tv_g"] = dcf.get("terminal_growth_used")
        dcf_ass["fv"] = dcf.get("fair_value_base")

    comps_used = comps.get("used_multiples") if comps else []
    comps_fv = comps.get("fair_value_base") if comps else None
    ddm_fv = ddm.get("fair_value_base") if ddm else None
    residual_fv = residual.get("fair_value_base") if residual else None
    dcf_fv = dcf.get("fair_value_base") if dcf else None

    street_fv = data.get("target_mean")
    implied = _implied_multiples(tri.get("fair_value_base"), data)

    summary = {
        "tri_fv_base": tri.get("fair_value_base"),
        "tri_weights": tri.get("weights_applied"),
        "forecast_target_12m": forecast.get("target_12m"),
        "forecast_expected_return_pct": forecast.get("expected_return_12m_pct"),
        "rating": rating,
        "rating_reason": rating_reason,
        "dqs_score": dqs.get("dqs_score"),
        "dqs_class": dqs.get("dqs_class"),
        "revision_adjustment": revision.get("adjustment"),
        "sentiment": sentiment.get("sentiment"),
        "cycle_phase": cycle_phase
    }

    logger.info(f"[DEBUG] {symbol} inputs={inputs}")
    logger.info(f"[DEBUG] {symbol} macro_wacc={waccs}")
    logger.info(f"[DEBUG] {symbol} dcf_assumptions={dcf_ass}")
    logger.info(f"[DEBUG] {symbol} comps_used={comps_used} comps_fv={comps_fv} "
                f"dcf_fv={dcf_fv} ddm_fv={ddm_fv} residual_fv={residual_fv} street_fv={street_fv} "
                f"implied_at_fv={implied}")
    logger.info(f"[DEBUG] {symbol} summary={summary}")

def build_rows(
    symbol: str,
    tri: Dict[str, Any],
    forecast: Dict[str, Any],
    wacc: Dict[str, Any],
    dqs: Dict[str, Any],
    data: Dict[str, Any],
    macro: Dict[str, Any],
    run_id: str,
    as_of_date: str,
    dcf: Optional[Dict[str, Any]],
    comps: Optional[Dict[str, Any]],
    ddm_val: Optional[Dict[str, Any]],
    residual_val: Optional[Dict[str, Any]],
    rating: str,
    rating_reason: str,
    revision: Dict[str, Any],
    sentiment: Dict[str, Any],
    cycle_phase: Optional[str],
    bull_case_bias_applied: bool
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Build result and parameters rows for database insertion.
    
    🆕 v2.4 - Safe None handling:
    - All method outputs (dcf, comps, ddm, residual) can be None
    - Defensive extraction with None checks
    - No AttributeError crashes if methods skipped
    
    Changes:
    - expected_return_12m_pct già cappato da compute_forecast (±120%)
    - tv_g_percent pescato dal DCF: dcf['terminal_growth_used']
    - wacc_value direttamente da wacc['wacc'] (già sanificato)
    - scenari letti da tri['scenario_weights'] se presenti
    
    Args:
        symbol: Stock symbol
        tri: Triangulation output (fair values, weights)
        forecast: Forecast output (target_12m, expected_return)
        wacc: WACC data (wacc, ke, kd, beta, etc)
        dqs: Data Quality Score
        data: Subject stock data
        macro: Macro data (rf, erp, crp)
        run_id: Valuation run ID
        as_of_date: Valuation date
        dcf: DCF output (CAN BE None if sector excluded)
        comps: COMPS output (CAN BE None if no peers)
        ddm_val: DDM output (CAN BE None if no dividend)
        residual_val: Residual Income output (CAN BE None)
        rating: Rating string
        rating_reason: Rating reason
        revision: Revision momentum data
        sentiment: Sentiment data
        cycle_phase: Cycle phase
        bull_case_bias_applied: Bull case flag
    
    Returns:
        (result_row, params_row) tuple for DB insertion
    """
    import json
    
    # ============================================
    # SCENARIO WEIGHTS
    # ============================================
    scenario_w = tri.get("scenario_weights") or {}
    base_w = scenario_w.get("base")
    bull_w = scenario_w.get("bull")
    bear_w = scenario_w.get("bear")
    
    # ============================================
    # COMPS AUDIT (safe extraction)
    # ============================================
    used_multiples = []
    if comps:
        used_multiples = comps.get("used_multiples") or []
    
    comps_audit = {"used_multiples": used_multiples}
    
    if comps:
        if comps.get("peers_after_filters") is not None:
            comps_audit["peers_after_filters"] = comps.get("peers_after_filters")
        if comps.get("included_peers"):
            comps_audit["included_peers"] = comps.get("included_peers")[:15]
        if comps.get("excluded_peers_with_reason"):
            comps_audit["excluded_peers_with_reason"] = comps.get("excluded_peers_with_reason")[:30]
    
    comps_used_json = json.dumps(comps_audit)
    
    # ============================================
    # REVISION & SENTIMENT SCORES
    # ============================================
    rev_score = revision.get("score") or revision.get("momentum_score") or 0.0
    sent_score = sentiment.get("score") or sentiment.get("sentiment_score") or 0.0
    
    # ============================================
    # RESULT ROW (valuation results)
    # ============================================
    result_row: Dict[str, Any] = {
        "symbol_yahoo": symbol,
        "as_of_date": as_of_date,
        "run_id": run_id,
        "model_version": "leading_v2",
        "policy_version": "leading_v2",
        
        # Price & fair values
        "price_eod": data.get("price_eod"),
        "fair_value_base": tri.get("fair_value_base"),
        "fair_value_bull": tri.get("fair_value_bull"),
        "fair_value_bear": tri.get("fair_value_bear"),
        "target_12m": forecast.get("target_12m"),
        "expected_return_12m_pct": forecast.get("expected_return_12m_pct"),
        
        # Method weights
        "dcf_weight": tri.get("weights_applied", {}).get("dcf"),
        "comps_weight": tri.get("weights_applied", {}).get("comps"),
        "street_target_weight": tri.get("weights_applied", {}).get("street_target"),
        "sotp_weight": tri.get("weights_applied", {}).get("sotp"),
        "ddm_weight": tri.get("weights_applied", {}).get("ddm"),
        "residual_weight": tri.get("weights_applied", {}).get("residual"),
        "p_tbv_weight": tri.get("weights_applied", {}).get("p_tbv"),
        
        # Scenario weights
        "scenario_base_w": base_w,
        "scenario_bull_w": bull_w,
        "scenario_bear_w": bear_w,
        
        # Quality & rating
        "confidenza": forecast.get("confidence"),
        "dqs_score": dqs.get("dqs_score"),
        "rating": rating,
        "rating_reason": rating_reason,
        
        # Overlays
        "esg_overlay_applied": 0,
        "esg_overlay_bps": 0,
        
        # Peer metrics
        "peers_count": comps.get("peers_after_filters") if comps else None,
        
        # Leading indicators
        "revision_momentum_score": rev_score,
        "sentiment_score": sent_score,
        "cycle_phase": cycle_phase,
        "bull_case_bias_applied": 1 if bull_case_bias_applied else 0,
        
        # ============================================
        # 🆕 METHOD-SPECIFIC FAIR VALUES (CAMPI MANCANTI!)
        # ============================================
        "dcf_fv": dcf.get("fair_value_base") if dcf else None,
        "comps_fv": comps.get("fair_value_base") if comps else None,
        "ddm_fv": ddm_val.get("fair_value_base") if ddm_val else None,
        "residual_fv": residual_val.get("fair_value_base") if residual_val else None,
        "street_target_fv": data.get("target_mean"),
        
        # ============================================
        # 🆕 MONTE CARLO DCF (v3.0) - CAMPI MANCANTI!
        # ============================================
        "dcf_mc_p10": dcf.get("mc_p10") if dcf else None,
        "dcf_mc_p50": dcf.get("mc_p50") if dcf else None,
        "dcf_mc_p90": dcf.get("mc_p90") if dcf else None,
        "dcf_mc_mean": dcf.get("mc_mean") if dcf else None,
        "dcf_mc_std": dcf.get("mc_std") if dcf else None,
        "dcf_mc_cv": dcf.get("mc_cv") if dcf else None,
        "dcf_mc_confidence": dcf.get("confidence") if dcf else None,
        "dcf_mc_execution_time": dcf.get("execution_time") if dcf else None,

        # ============================================
        # 🆕 STREET RANGE (v2.7) - CAMPI MANCANTI!
        # ============================================
        "street_consensus": data.get("target_mean"),
        "street_low": data.get("target_low"),
        "street_high": data.get("target_high"),
        "street_range_pct": (
            ((data.get("target_high") - data.get("target_low")) / data.get("target_mean") * 100)
            if (data.get("target_high") and data.get("target_low") and data.get("target_mean"))
            else None
        ),
        "street_analyst_count": data.get("analyst_count"),

        # ============================================
        # 🆕 COMPS METRICS - CAMPI MANCANTI!
        # ============================================
        "comps_pe_median": (
            next(
                (
                    m.get("median") 
                    for m in (comps.get("used_multiples") or [])
                    if m.get("multiple") in ["pe_ntm", "pe_forward", "forward_pe"]
                ),
                None
            )
            if comps
            else None
        ),
        
        "comps_peers_used": (
            comps.get("peers_after_filters") or 
            comps.get("peers_count") or 
            len(comps.get("included_peers", []))
            if comps 
            else None
        ),

        # ============================================
        # 🆕 BOOTSTRAP CONFIDENCE INTERVALS - CAMPI MANCANTI!
        # ============================================
        "comps_bootstrap_p10": comps.get("bootstrap_p10") if comps else None,
        "comps_bootstrap_p50": comps.get("bootstrap_p50") if comps else None,
        "comps_bootstrap_p90": comps.get("bootstrap_p90") if comps else None,
        "comps_bootstrap_confidence": comps.get("bootstrap_confidence") if comps else None,
        "comps_bootstrap_cv": comps.get("bootstrap_cv") if comps else None,
        
        # ============================================
        # 🆕 DDM METRICS - CAMPI MANCANTI!
        # ============================================
        "ddm_payout_used": ddm_val.get("payout_ratio") if ddm_val else None,
        
        "ddm_gordon_pe": (
            ddm_val.get("fair_value_base") / data.get("eps_ntm")
            if (
                ddm_val 
                and ddm_val.get("fair_value_base") is not None
                and data.get("eps_ntm") is not None
                and data.get("eps_ntm") > 0
            )
            else None
        ),
        
        # ============================================
        # 🆕 SUBJECT FUNDAMENTALS - CAMPI MANCANTI!
        # ============================================
        "roe": data.get("return_on_equity"),
        "operating_margin": data.get("operating_margin"),
        "revenue_growth": data.get("ntm_revenue_growth"),
        
        # ============================================
        # 🆕 MC MODE & SCENARIO - CAMPI MANCANTI!
        # ============================================
        "mc_mode": dcf.get("mode") if dcf else None,
        "mc_scenario": dcf.get("scenario") if dcf else None,
        
        # ============================================
        # 🆕 BOOST STREET ANCHOR - CAMPO MANCANTE!
        # ============================================
        "boost_street_anchor": 1 if (comps and comps.get("boost_street_anchor")) else 0,
        
        # ============================================
        # 🆕 PEER MATCH MODE - CAMPO MANCANTE!
        # ============================================
        "peer_match_mode": comps.get("peer_match_mode") if comps else None,
        
        # ============================================
        # 🆕 INCLUDED PEERS JSON - CAMPO MANCANTE!
        # ============================================
        "included_peers": json.dumps(comps.get("included_peers")) if comps and comps.get("included_peers") else None,
        
        # ============================================
        # 🆕 DQS CLASS - CAMPO MANCANTE!
        # ============================================
        "dqs_class": dqs.get("dqs_class"),
    }
    
    # ============================================
    # PARAMS ROW (valuation parameters)
    # ============================================
    params_row: Dict[str, Any] = {
        "symbol_yahoo": symbol,
        "as_of_date": as_of_date,
        "run_id": run_id,
        "model_version": "leading_v2",
        "policy_version": "leading_v2",
        
        # Risk-free & premiums
        "rf_currency": data.get("financial_currency"),
        "rf_10y_value": macro.get("rf"),
        "erp_us_value": macro.get("erp_us"),
        "crp_country_value": macro.get("crp_country"),
        "erp_country_value": macro.get("erp_country"),
        
        # WACC components
        "beta_used": wacc.get("beta_used"),
        "tax_rate_used": wacc.get("tax_rate_used"),
        "kd_spread_proxy": wacc.get("spread_proxy"),
        "kd_value": wacc.get("kd"),
        "ke_value": wacc.get("ke"),
        "wacc_value": wacc.get("wacc"),
        
        # 🆕 REGIME DETECTION AUDIT FIELDS
        "wacc_base": wacc.get("wacc_base"),
        "regime_adjustment_bps": (wacc.get("regime_adjustment") or 0.0) * 10000,
        "regime_index_ticker": (wacc.get("regime_info") or {}).get("index_ticker"),
        "regime_index_pe": (wacc.get("regime_info") or {}).get("index_pe"),
        "regime_evaluation": (wacc.get("regime_info") or {}).get("evaluation"),
        "regime_deviation_sigma": (wacc.get("regime_info") or {}).get("deviation"),
        
        # DCF PARAMETERS (safe None handling)
        "horizon_years": dcf.get("horizon_years") if dcf else None,
        "tv_method": "Gordon" if dcf else None,
        "tv_g_percent": dcf.get("terminal_growth_used") if dcf else None,

        # 🆕 SENSITIVITY GRID (v2.5)
        "sensitivity_grid": (
            json.dumps(dcf.get("sensitivity_grid"))
            if dcf and dcf.get("sensitivity_grid")
            else None
        ),
        
        # Policies
        "capex_policy": "3Y_smoothing",
        "nwc_policy": "3Y_smoothing",
        "margin_reversion_policy": "mean_reversion",
        
        # COMPS PARAMETERS (safe None handling)
        "comps_multiples_used": comps_used_json,
        "trimming_rule": "p10-p90",
        "peers_excluded": str(comps.get("peers_excluded", [])) if comps else "[]",
        
        # SOTP
        "sotp_proxy_flag": 1 if data.get("conglomerate_flag") else 0,
        "segments_basis_text": "proxy" if data.get("conglomerate_flag") else None,
        
        # Data sources
        "ntm_inputs_source": "estimates_interpolated",
        "ltm_inputs_source": "quarterly_ttm",
        
        # DQS components
        "est_coverage": dqs.get("components", {}).get("coverage"),
        "est_dispersion": dqs.get("components", {}).get("dispersion"),
        "surprise_track": dqs.get("components", {}).get("surprise_track"),
        "price_vol": dqs.get("components", {}).get("price_vol"),
        "peer_depth": dqs.get("components", {}).get("peer_depth"),
        "fx_consistency": dqs.get("components", {}).get("fx_consistency"),
        
        # Warnings
        "warnings": str(list(set((wacc.get("warnings") or []) + (dqs.get("warnings") or [])))),
        "data_gaps": str([w for w in (wacc.get("warnings") or []) if "MISSING" in w]),
    }
    
    return result_row, params_row

def _apply_street_envelope(rating: str, street_label: Optional[str], vp: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    if not street_label:
        return rating, None
    order = ["Strong Buy", "Buy", "Accumulate", "Hold", "Reduce", "Sell", "Strong Sell"]
    idx = {name: i for i, name in enumerate(order)}
    def norm(r: str) -> str:
        r = (r or "").strip().lower()
        for name in order:
            if r == name.lower():
                return name
        aliases = {"strongbuy": "Strong Buy", "strong_buy": "Strong Buy", "neutral":"Hold",
                   "market perform":"Hold","equal weight":"Hold","underperform":"Sell","underweight":"Sell"}
        return aliases.get(r, "Hold")
    cur = norm(rating)
    env = (vp.get("street_anchor_guardrail") or {}).get("envelope", {}) or {}
    min_allowed = None; max_allowed = None
    if street_label == "strong_buy":
        min_allowed = env.get("strong_buy_min_rating", "Buy")
    elif street_label == "buy":
        min_allowed = env.get("buy_min_rating", "Hold")
    elif street_label == "sell":
        max_allowed = env.get("sell_max_rating", "Hold")
    elif street_label == "strong_sell":
        max_allowed = env.get("strong_sell_max_rating", env.get("sell_max_rating", "Hold"))
    clamp_reason = None
    if min_allowed:
        min_allowed = norm(min_allowed)
        if idx.get(cur, 999) > idx.get(min_allowed, 999):
            return min_allowed, f"Street-guardrail: street={street_label.replace('_',' ').title()} clamp→{min_allowed}"
    if max_allowed:
        max_allowed = norm(max_allowed)
        if idx.get(cur, -1) < idx.get(max_allowed, -1):
            return max_allowed, f"Street-guardrail: street={street_label.replace('_',' ').title()} clamp→{max_allowed}"
    return cur, None


# ============================================
# HELPER FUNCTIONS (definite PRIMA di run_daily)
# ============================================

def load_leading_config() -> Dict[str, Any]:
    """Carica la sezione 'leading' da valuation_config.yaml; fallback ai default."""
    try:
        config_path = Path(__file__).parent.parent / "config" / "valuation_config.yaml"
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get('leading', {}) or {
            'triangulation': {'dcf': 0.40, 'comps': 0.30, 'street_target': 0.30},
            'terminal_growth': {'default': 0.035},
            'scenarios': {'base': 0.50, 'bull': 0.30, 'bear': 0.20},
            'adjustments': {
                'revision_momentum': {'enabled': True, 'adjustment_factor': 0.15, 'threshold_positive': 0.10, 'threshold_negative': -0.10},
                'sentiment_overlay': {'enabled': True, 'lookback_days': 90, 'upgrade_threshold': 1.5, 'downgrade_threshold': 0.67},
                'bull_case_bias': {'enabled': True, 'scenario_shift': {'base': 0.40, 'bull': 0.40, 'bear': 0.20}},
                'cycle_adjustment': {'enabled': True, 'sectors': ['Energy','Materials','Basic Materials']}
            }
        }
    except Exception as e:
        logger.warning(f"Could not load valuation_config.yaml: {e}. Using defaults.")
        return {
            'triangulation': {'dcf': 0.40, 'comps': 0.30, 'street_target': 0.30},
            'terminal_growth': {'default': 0.035},
            'scenarios': {'base': 0.50, 'bull': 0.30, 'bear': 0.20},
            'adjustments': {
                'revision_momentum': {'enabled': True, 'adjustment_factor': 0.15, 'threshold_positive': 0.10, 'threshold_negative': -0.10},
                'sentiment_overlay': {'enabled': True, 'lookback_days': 90, 'upgrade_threshold': 1.5, 'downgrade_threshold': 0.67},
                'bull_case_bias': {'enabled': True, 'scenario_shift': {'base': 0.40, 'bull': 0.40, 'bear': 0.20}},
                'cycle_adjustment': {'enabled': True, 'sectors': ['Energy','Materials','Basic Materials']}
            }
        }
        
def adjust_wacc_for_leading(wacc: Dict[str, Any], data: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    WACC Leading (definitiva):
    - Terminal growth per settore (da config se presente, altrimenti fallback)
    - Utilities: regulated discount -35 bps su WACC (senza superare max(Ke,Kd)+ε)
    - Sanitize: clamp [5%,15%] e WACC ≤ max(Ke,Kd)+ε
    """
    sector = (data.get("sector") or "").strip()
    symbol = data.get("symbol") or "UNKNOWN"

    tg_cfg = (config.get("terminal_growth") or {}) if isinstance(config, dict) else {}
    tg_default = float(tg_cfg.get("default", 0.035))
    tg_util    = float(tg_cfg.get("utilities", 0.030))
    tg_hg      = float(tg_cfg.get("high_growth_sectors", 0.035))
    tg_cons    = float(tg_cfg.get("consumer_cyclical", tg_hg))
    tg_comm    = float(tg_cfg.get("communication_services", 0.035))
    tg_energy  = float(tg_cfg.get("commodity_cyclicals", 0.030))

    if sector == "Utilities":
        terminal_g = tg_util
    elif sector in ("Information Technology","Technology","Healthcare","Health Care"):
        terminal_g = tg_hg
    elif sector == "Consumer Cyclical":
        terminal_g = tg_cons
    elif sector == "Communication Services":
        terminal_g = tg_comm
    elif sector in ("Energy","Materials","Basic Materials"):
        terminal_g = tg_energy
    else:
        terminal_g = tg_default

    wacc["terminal_growth_leading"] = terminal_g

    def _sf(x):
        try: return float(x)
        except: return None

    ke = _sf(wacc.get("ke"))
    kd = _sf(wacc.get("kd"))
    ww = _sf(wacc.get("wacc"))

    # Utilities regulated discount
    if sector == "Utilities" and ww is not None:
        ww = ww - 0.0035

    # Sanitize
    # 🆕 FIX: Allow WACC slightly above Ke for low-rf countries (Swiss, Japan)
    # Upper bound: max(Ke, Kd) + 150bps (was 5bps) to accommodate rf floor adjustments
    upper = max(ke or 0.0, kd or 0.0) + 0.015  # ✅ Changed from 0.0005 to 0.015
    
    if ww is None:
        ww = max((ke or 0.10) - 0.01, 0.08)
    
    # Final clamp [5%, 15%] with relaxed upper bound
    wacc["wacc"] = max(0.05, min(min(upper, 0.15), ww))
    
    # 🆕 DEBUG LOG
    logger.debug(
        f"[WACC-LEADING-SANITY] {symbol}: "
        f"ww={ww:.4f}, ke={ke:.4f}, kd={kd:.4f}, upper={upper:.4f}, "
        f"final_wacc={wacc['wacc']:.4f}"
    )

    try:
        logger.info(f"[WACC-LEADING] {symbol}: terminal_growth={terminal_g:.2%} wacc={wacc['wacc']:.4f}")
    except Exception:
        pass

    return wacc

def force_tv_growth_utilities(wacc: Dict[str, Any], dcf: Dict[str, Any], sector: str) -> None:
    """
    Forza coerenza terminal growth per Utilities (leading) se mismatch.
    Aggiorna sia wacc sia il blocco DCF prima della persistenza.
    """
    if sector == "Utilities":
        tg_wacc = wacc.get("terminal_growth_leading")
        tg_dcf = dcf.get("terminal_growth_used")
        # Priorità all'override wacc se presente
        if tg_wacc is not None and (tg_dcf is None or abs(tg_dcf - tg_wacc) > 1e-6):
            dcf["terminal_growth_used"] = tg_wacc
        # fallback hard se comunque resta <0.029
        if dcf.get("terminal_growth_used", 0) < 0.029:
            dcf["terminal_growth_used"] = 0.030
            
# ============================================
# Revision Momentum
# ============================================
def compute_revision_momentum(db, symbol: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    EPS NTM oggi vs ~3 mesi fa
    Returns: { 'score': float, 'adjustment': float, 'direction': 'positive'|'negative'|'neutral' }
    """
    result = {'score': 0.0, 'adjustment': 1.0, 'direction': 'neutral'}
    try:
        momentum_config = config.get('adjustments', {}).get('revision_momentum', {})
        if not momentum_config.get('enabled', True):
            return result

        lookback_months = momentum_config.get('lookback_months', 3)
        threshold_pos = momentum_config.get('threshold_positive', 0.10)
        threshold_neg = momentum_config.get('threshold_negative', -0.10)
        adjustment_factor = momentum_config.get('adjustment_factor', 0.15)

        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT avg_estimate, snapshot_date
                FROM earnings_estimates
                WHERE symbol_yahoo = ? AND period = '0y'
                ORDER BY snapshot_date DESC
                LIMIT 1
            """, (symbol,))
            current_row = cur.fetchone()
            if not current_row or current_row['avg_estimate'] is None:
                return result
            eps_current = float(current_row['avg_estimate'])
            current_date = datetime.fromisoformat(current_row['snapshot_date'])

            cutoff_date = (current_date - timedelta(days=lookback_months * 30)).isoformat()
            cur.execute("""
                SELECT avg_estimate
                FROM earnings_estimates
                WHERE symbol_yahoo = ?
                  AND period = '0y'
                  AND snapshot_date <= ?
                ORDER BY snapshot_date DESC
                LIMIT 1
            """, (symbol, cutoff_date))
            historical_row = cur.fetchone()
            if not historical_row or historical_row['avg_estimate'] is None:
                return result

            eps_historical = float(historical_row['avg_estimate'])
            if eps_historical == 0:
                return result

            revision_score = (eps_current - eps_historical) / abs(eps_historical)
            result['score'] = revision_score
            if revision_score > threshold_pos:
                result['adjustment'] = 1.0 + adjustment_factor
                result['direction'] = 'positive'
                logger.info(f"[REVISION] {symbol}: +{revision_score:.1%} → FV premium {adjustment_factor:.0%}")
            elif revision_score < threshold_neg:
                result['adjustment'] = 1.0 - adjustment_factor
                result['direction'] = 'negative'
                logger.info(f"[REVISION] {symbol}: {revision_score:.1%} → FV penalty {adjustment_factor:.0%}")
        return result
    except Exception as e:
        logger.warning(f"{symbol}: Revision momentum calculation failed - {e}")
        return result

# ============================================
# Sentiment Score (Up/Down ratio)
# ============================================
def compute_sentiment_score(db, symbol: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcola upgrade/downgrade ratio per sentiment.
    Returns: { 'ratio': float, 'sentiment': 'bullish'|'bearish'|'neutral', 'scenario_shift': None }
    """
    result = {'ratio': 1.0, 'sentiment': 'neutral', 'scenario_shift': None}
    try:
        sentiment_config = config.get('adjustments', {}).get('sentiment_overlay', {})
        if not sentiment_config.get('enabled', True):
            return result

        lookback_days = sentiment_config.get('lookback_days', 90)
        upgrade_threshold = sentiment_config.get('upgrade_threshold', 1.5)
        downgrade_threshold = sentiment_config.get('downgrade_threshold', 0.67)

        cutoff_date = (datetime.now() - timedelta(days=lookback_days)).date().isoformat()
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) as cnt
                FROM upgrade_downgrade
                WHERE symbol_yahoo = ?
                  AND action_date >= ?
                  AND action IN ('up', 'main', 'init')
            """, (symbol, cutoff_date))
            upgrades = cur.fetchone()['cnt']

            cur.execute("""
                SELECT COUNT(*) as cnt
                FROM upgrade_downgrade
                WHERE symbol_yahoo = ?
                  AND action_date >= ?
                  AND action IN ('down', 'reit')
            """, (symbol, cutoff_date))
            downgrades = cur.fetchone()['cnt']

        if upgrades + downgrades == 0:
            return result

        ratio = upgrades / downgrades if downgrades > 0 else float(upgrades)
        result['ratio'] = ratio
        if ratio > upgrade_threshold:
            result['sentiment'] = 'bullish'
            logger.info(f"[SENTIMENT] {symbol}: Bullish ({upgrades} upgrades / {downgrades} downgrades)")
        elif ratio < downgrade_threshold:
            result['sentiment'] = 'bearish'
            logger.info(f"[SENTIMENT] {symbol}: Bearish ({upgrades} upgrades / {downgrades} downgrades)")
        return result
    except Exception as e:
        logger.warning(f"{symbol}: Sentiment calculation failed - {e}")
        return result

# ============================================
# Bull Case Bias (scenario weights)
# ============================================
def apply_bull_case_bias(config: Dict[str, Any],
                         revision: Dict[str, Any],
                         sentiment: Dict[str, Any],
                         data: Dict[str, Any]) -> Dict[str, float]:
    """
    Se >=2 trigger (revision positiva, sentiment bullish, prezzo>200MA), shift scenari verso bull.
    """
    bias_config = config.get('adjustments', {}).get('bull_case_bias', {})
    base_sw = config.get('scenarios', {'base': 0.50, 'bull': 0.30, 'bear': 0.20})
    if not bias_config.get('enabled', True):
        return base_sw

    triggers = []
    if revision.get('direction') == 'positive':
        triggers.append('revision_momentum_positive')
    if sentiment.get('sentiment') == 'bullish':
        triggers.append('sentiment_positive')
    price = data.get('price_eod')
    ma_200 = data.get('two_hundred_day_average')
    if price and ma_200 and price > ma_200:
        triggers.append('price_momentum_positive')

    if len(triggers) >= 2:
        logger.info(f"[BULL BIAS] Triggers: {triggers} → Shift to bull scenario")
        return bias_config.get('scenario_shift', {'base': 0.40, 'bull': 0.40, 'bear': 0.20})
    return base_sw
    
# ============================================
# Cycle Adjustment (commodities)
# ============================================
def detect_cycle_phase(db, symbol: str, sector: str, config: Dict[str, Any]) -> str:
    """
    Ritorna 'peak' | 'trough' | 'mid' per settori commodity.
    """
    cycle_config = config.get('adjustments', {}).get('cycle_adjustment', {})
    if not cycle_config.get('enabled', True):
        return 'mid'

    commodity_sectors = cycle_config.get('sectors', ['Energy', 'Materials', 'Basic Materials'])
    if sector not in commodity_sectors:
        return 'mid'

    try:
        vs = db.get_valuation_snapshot_latest(symbol)
        if not vs:
            return 'mid'
        current_ev_ebitda = vs.get('ltm_ev_ebitda')
        if not current_ev_ebitda:
            return 'mid'

        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT AVG(ltm_ev_ebitda) as avg_multiple
                FROM valuation_snapshots
                WHERE symbol_yahoo = ?
                  AND ltm_ev_ebitda IS NOT NULL
                  AND as_of_date >= date('now', '-5 years')
            """, (symbol,))
            row = cur.fetchone()
        if not row or row['avg_multiple'] is None:
            return 'mid'

        avg_multiple = float(row['avg_multiple'])
        ratio = current_ev_ebitda / avg_multiple
        if ratio > 1.2:
            logger.info(f"[CYCLE] {symbol}: PEAK (EV/EBITDA {ratio:.1f}x vs 5Y avg)")
            return 'peak'
        elif ratio < 0.8:
            logger.info(f"[CYCLE] {symbol}: TROUGH (EV/EBITDA {ratio:.1f}x vs 5Y avg)")
            return 'trough'
        return 'mid'
    except Exception as e:
        logger.warning(f"{symbol}: Cycle detection failed - {e}")
        return 'mid'
        
def triangulate_leading(data: Dict[str, Any],
                        dcf_val: Optional[Dict[str, Any]],
                        comps_val: Optional[Dict[str, Any]],
                        ddm_val: Optional[Dict[str, Any]],
                        residual_val: Optional[Dict[str, Any]],
                        sotp_val: Optional[Dict[str, Any]],
                        dqs: Dict[str, Any],
                        config: Dict[str, Any],
                        scenario_weights: Dict[str, float]) -> Dict[str, Any]:
    """
    🆕 v2.3 - USE triangulate() from triangulation.py (config-driven)
    
    CRITICAL FIX: Non duplicare logica triangulation!
    - triangulate() già applica sector weights (DDM 30% per Financial Services)
    - triangulate() già include street_target (se pipeline="leading")
    - NO manual Street blend dopo!
    
    Args:
        data: Subject data
        dcf_val: DCF result
        comps_val: COMPS result
        ddm_val: DDM result (Gordon Model)
        residual_val: Residual Income result
        sotp_val: SOTP result
        dqs: Data Quality Score
        config: Config dict (not used, for compatibility)
        scenario_weights: Scenario weights (base/bull/bear)
    
    Returns:
        Triangulation result dict
    """
    from ..pipelines.triangulation import triangulate
    
    # ============================================
    # 1. CALL triangulate() (standard function)
    # ============================================
    result = triangulate(
        data=data,
        dcf=dcf_val,
        comps=comps_val,
        ddm=ddm_val,
        residual=residual_val,
        sotp=sotp_val,
        dqs=dqs,
        pipeline="leading"  # 🆕 Pass pipeline parameter
    )
    
    # ============================================
    # 2. ADD scenario_weights (for compatibility)
    # ============================================
    result["scenario_weights"] = scenario_weights
    
    # ============================================
    # 3. BULL/BEAR scenarios (already computed)
    # ============================================
    # triangulate() già calcola fair_value_bull/bear
    # Niente da fare qui
    
    return result

def calculate_clamp_severity(rating_before, rating_after):
    """Calculate clamp severity [0, 1]"""
    ladder = ["Strong Buy", "Buy", "Accumulate", "Hold", "Reduce", "Sell", "Strong Sell"]
    idx = {r: i for i, r in enumerate(ladder)}
    
    def normalize(r):
        if not r:
            return "Hold"
        r_lower = r.lower().strip()
        for rating in ladder:
            if r_lower == rating.lower():
                return rating
        return "Hold"
    
    before_norm = normalize(rating_before)
    after_norm = normalize(rating_after)
    before_idx = idx.get(before_norm, 3)
    after_idx = idx.get(after_norm, 3)
    delta = abs(after_idx - before_idx)
    return delta / 6.0

def track_rating_performance(db, symbol, rating_date, rating, rating_reason,
                            expected_return_12m_pct, price_at_rating, fair_value_base,
                            street_anchor_applied, street_anchor_clamp_severity,
                            value_trap_detected, fundamental_override):
    """Insert rating performance tracking"""
    try:
        with db.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO rating_performance_tracking (
                    symbol_yahoo, rating_date, rating, rating_reason,
                    expected_return_12m_pct, price_at_rating, fair_value_base,
                    street_anchor_applied, street_anchor_clamp_severity,
                    value_trap_detected, fundamental_override, rating_accuracy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (symbol, rating_date, rating, rating_reason,
                  expected_return_12m_pct, price_at_rating, fair_value_base,
                  1 if street_anchor_applied else 0, street_anchor_clamp_severity,
                  1 if value_trap_detected else 0, 1 if fundamental_override else 0))
            conn.commit()
    except Exception as e:
        logger.warning(f"[TRACKING] {symbol}: Failed - {e}")

# ============================================
# MAIN LEADING pipeline
# ============================================

def run_daily(db, data: Dict[str, Any], macro: Dict[str, Any], as_of_date: str):
    """
    Leading pipeline:
    - WACC → DCF → COMPS → DDM/Residual/SOTP → TRIANGULATION
    - Monte Carlo DCF v3.1 only in LEADING
    - Forecast/Rating + persistence
    """
    symbol = data.get("symbol")
    sector = data.get("sector")
    country = data.get("country")

    # 1. WACC
    wacc = compute_wacc(data, macro)

    # 2. Methods
    dcf_result = run_dcf(data, wacc, dqs={})
    comps_result = run_comps(data, dqs={}, mode="leading", conn=db.get_connection(), wacc=wacc)
    ddm_result = run_ddm(data, wacc)
    residual_result = run_residual_income(data, wacc)
    sotp_result = run_sotp(data, comps_val=comps_result)

    # === DCF MINIMAL FALLBACK (Financial Services) ===
    if not (dcf_result and dcf_result.get("fair_value_base")):
        if (sector or "").strip() == "Financial Services":
            try:
                rev_ntm = data.get("revenue_ntm")
                shares = data.get("shares_outstanding")
                net_debt = max((data.get("total_debt") or 0.0) - (data.get("cash") or 0.0), 0.0)
                if not rev_ntm and data.get("revenue_ttm"):
                    g_ntm = data.get("ntm_revenue_growth") or 0.03
                    rev_ntm = float(data["revenue_ttm"]) * (1.0 + float(g_ntm))
                if not shares:
                    shares = data.get("shares_outstanding") or 1.0
                fcf_margin = 0.12
                if data.get("ttm_free_cashflow") and data.get("revenue_ttm"):
                    try:
                        fcf_margin = float(data["ttm_free_cashflow"]) / float(data["revenue_ttm"])
                        if not (0.02 <= fcf_margin <= 0.50):
                            fcf_margin = 0.12
                    except:
                        fcf_margin = 0.12
                tv_g = wacc.get("terminal_growth_leading") or 0.03
                horizon_years = 5
                if rev_ntm and shares and wacc.get("wacc"):
                    g_ntm = data.get("ntm_revenue_growth") or 0.03
                    w = float(wacc["wacc"])
                    fcfs = []
                    revenue = float(rev_ntm)
                    for t in range(1, horizon_years + 1):
                        lam = (horizon_years - t) / (horizon_years - 1) if horizon_years > 1 else 0.0
                        gt = float(tv_g) + (float(g_ntm) - float(tv_g)) * lam
                        revenue *= (1.0 + gt)
                        fcfs.append(revenue * fcf_margin)
                    fcf_terminal = fcfs[-1] * (1.0 + float(tv_g))
                    denom = max(w - float(tv_g), 0.001)
                    tv = fcf_terminal / denom
                    pv_fcfs = sum(fcfs[t-1] / ((1.0 + w) ** t) for t in range(1, horizon_years + 1))
                    pv_tv = tv / ((1.0 + w) ** horizon_years)
                    enterprise_value = pv_fcfs + pv_tv
                    equity_value = enterprise_value - (net_debt or 0.0)
                    fv_base = equity_value / float(shares) if float(shares) > 0 else None
                    if fv_base and fv_base > 0:
                        dcf_result = {
                            "fair_value_base": float(fv_base),
                            "horizon_years": horizon_years,
                            "terminal_growth_used": float(tv_g),
                            "assumptions": {
                                "wacc": w,
                                "g_ntm": float(g_ntm),
                                "fcf_margin": float(fcf_margin),
                                "fcf1": fcfs[0] if fcfs else None
                            }
                        }
                        logger.info(f"[DCF-FALLBACK] {symbol}: Minimal DCF built (FV=${fv_base:.2f}) for sensitivity/MC")
            except Exception as e:
                logger.warning(f"[DCF-FALLBACK] {symbol}: Minimal DCF construction failed - {e}")

    # Sensitivity Grid (se DCF disponibile)
    if dcf_result and dcf_result.get("fair_value_base"):
        try:
            from ..engines.sensitivity import generate_sensitivity_grid
            base_params = {
                "wacc": wacc.get("wacc"),
                "terminal_g": dcf_result.get("terminal_growth_used"),
                "g1": (dcf_result.get("assumptions") or {}).get("g_ntm"),
                "fcf_margin": (dcf_result.get("assumptions") or {}).get("fcf_margin"),
            }
            sens = generate_sensitivity_grid(base_params=base_params, stock_data=data, wacc_data=wacc)
            if sens:
                dcf_result["sensitivity_grid"] = sens
                logger.info(f"[SENSITIVITY] {symbol}: Grid generated (dominant lever: {sens.get('dominant_lever')})")
        except Exception as e:
            logger.warning(f"[SENSITIVITY] {symbol}: Grid generation failed - {e}")

    # 3. Triangulation
    tri = triangulate(
        data=data,
        dcf=dcf_result,
        comps=comps_result,
        ddm=ddm_result,
        residual=residual_result,
        sotp=sotp_result,
        dqs={},
        pipeline="leading"
    )

    # 4. DQS with FALLBACK for analyst_count
    analyst_count = data.get("analyst_count")
    
    # CRITICAL FIX: If analyst_count is NULL but we have target_mean, 
    # assume at least 3 analysts (otherwise DQS drops by 0.20!)
    if not analyst_count:
        if data.get("target_mean"):
            analyst_count = 3
            logger.info(f"[DQS-FIX] {symbol}: No analyst_count, using fallback=3 (has target_mean)")
        else:
            analyst_count = 0
    
    dqs = compute_dqs({
        "analyst_count": analyst_count,  # ← FIX: Use fallback!
        "eps_ntm": data.get("eps_ntm"),
        "eps_ntm_high": data.get("eps_ntm_high"),
        "eps_ntm_low": data.get("eps_ntm_low"),
        "surprise_series": data.get("surprise_series"),
        "price_60d_vol_ratio": data.get("price_60d_vol_ratio"),
        "peer_count": comps_result.get("peers_after_filters") if comps_result else 0,
        "fx_consistent": True
    })

    # 5. Monte Carlo
    mc = None
    if dcf_result and dcf_result.get("fair_value_base"):
        try:
            mc = run_monte_carlo_dcf_v31(
                stock_data=data,
                wacc_data=wacc,
                dcf_base=dcf_result,
                db=db,
                sector=sector,
                as_of_date=as_of_date,
                n_runs=1000,
                use_parallel=True
            )
        except Exception as e:
            logger.warning(f"[LEADING] {symbol}: MC v3.1 failed - {e}")

    # 6. Forecast
    fv_anchor = mc.get("mc_p50") if mc else None
    forecast = compute_forecast(
        tri={**tri, "fair_value_base": fv_anchor if fv_anchor else tri.get("fair_value_base")},
        data={**data, "dqs_class": dqs.get("dqs_class"), "target_mean": data.get("target_mean")},
        wacc=wacc
    )

   # 7. Rating
    rating, rating_reason = compute_rating(
        expected_return_12m_pct=forecast.get("expected_return_12m_pct"),
        dqs_class=dqs.get("dqs_class"),
        methods_count=sum(1 for k, v in tri.get("method_values", {}).items() if v is not None),
        peers_count=comps_result.get("peers_after_filters") if comps_result else None,
        sector=sector
    )

    rating_before_clamp = rating
    
    # 🆕 STREET ANCHOR NUMERICO ADAPTIVE (MANCAVA!)
    jfd_row = db.get_julia_row(symbol) or {}
    score_num, _, _ = _parse_street_rating(jfd_row)

    mc_confidence = mc.get("confidence") if mc else None
    rating_after, anchor_reason = _street_anchor_clamp(
        model_rating=rating,
        street_score=score_num,
        dqs_score=dqs.get("dqs_score"),
        model_fv=tri.get("fair_value_base"), 
        street_fv=data.get("target_mean"),
        forecast_er=forecast.get("expected_return_12m_pct"),
        sector=sector,
        mc_confidence=mc_confidence
    )
    
    clamp_applied = rating_after != rating
    if clamp_applied:
        rating = rating_after
        rating_reason = f"{rating_reason} | {anchor_reason}"
    
    # Value trap check (già presente)
    trap = evaluate_value_trap(data, comps_result, dcf_result, mc, dqs)
    if trap.get("override_rating"):
        rating = trap["override_rating"]
        rating_reason += " | value_trap_override"

    # 8. Compute additional metrics for build_rows
    revision = {"score": 0.0, "momentum_score": 0.0}  # Default (può essere popolato se abiliti revision momentum)
    sentiment = {"score": 0.0, "sentiment_score": 0.0}  # Default
    cycle_phase = None  # Default
    bull_case_bias_applied = False  # Default
    
    # 9. Build rows usando la funzione completa (RIPRISTINATA!)
    result_row, params_row = build_rows(
        symbol=symbol,
        tri=tri,
        forecast=forecast,
        wacc=wacc,
        dqs=dqs,
        data=data,
        macro=macro,
        run_id=f"leading_single_{symbol}_{as_of_date}",
        as_of_date=as_of_date,
        dcf=dcf_result,
        comps=comps_result,
        ddm_val=ddm_result,
        residual_val=residual_result,
        rating=rating,
        rating_reason=rating_reason,
        revision=revision,
        sentiment=sentiment,
        cycle_phase=cycle_phase,
        bull_case_bias_applied=bull_case_bias_applied
    )

    # 10. Write to DB immediately (per-symbol mode)
    write_results_leading(db, [result_row])
    if params_row:
        write_parameters(db, [params_row])
    
    # TRACKING
    track_rating_performance(
        db=db, symbol=symbol, rating_date=as_of_date,
        rating=rating, rating_reason=rating_reason,
        expected_return_12m_pct=forecast.get("expected_return_12m_pct"),
        price_at_rating=data.get("price_eod"),
        fair_value_base=tri.get("fair_value_base"),
        street_anchor_applied=clamp_applied,
        street_anchor_clamp_severity=calculate_clamp_severity(rating_before_clamp, rating) if clamp_applied else 0.0,
        value_trap_detected=trap.get("is_value_trap", False),
        fundamental_override=(anchor_reason and "override" in anchor_reason.lower()) if clamp_applied else False
    )

    return result_row

def run_leading(db, as_of_date=None, symbols_subset=None, export_dir="exports", run_id=None):
    # Alias compatibile per i test/benchmark
    return run_daily(
        db,
        as_of_date=as_of_date,
        symbols_subset=symbols_subset,
        export_dir=export_dir,
        run_id=run_id
    )

# ============================================
# Summary
# ============================================
def log_summary(results_batch: List[Dict[str, Any]], params_batch: List[Dict[str, Any]]):
    """Statistiche sintetiche LEADING."""
    if not results_batch:
        logger.info("[LEADING] Valuation: no results.")
        return

    avg_wacc = _avg([p.get("wacc_value") for p in params_batch])
    avg_expected = _avg([r.get("expected_return_12m_pct") for r in results_batch])
    low_conf = sum(1 for r in results_batch if r.get("confidenza") == "Low")

    avg_revision = _avg([r.get("revision_momentum_score") for r in results_batch])
    positive_momentum = sum(1 for r in results_batch if (r.get("revision_momentum_score") or 0) > 0.10)
    bull_bias_count = sum(1 for r in results_batch if r.get("bull_case_bias_applied") == 1)

    avg_wacc_str = f"{avg_wacc:.2%}" if avg_wacc is not None else "NA"
    # FIX: expected_return salvato come decimale → moltiplica *100 per stampa in %
    avg_expected_str = f"{(avg_expected*100):.2f}%" if avg_expected is not None else "NA"
    avg_revision_str = f"{avg_revision:.1%}" if avg_revision is not None else "NA"

    logger.info(
        f"[LEADING] Valuation summary: symbols={len(results_batch)}, "
        f"avg_wacc={avg_wacc_str}, "
        f"avg_expected_return={avg_expected_str}, "
        f"low_conf={low_conf}, "
        f"avg_revision_momentum={avg_revision_str}, "
        f"positive_momentum={positive_momentum}, "
        f"bull_bias_applied={bull_bias_count}"
    )

def _avg(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None