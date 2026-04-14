"""
NUOVE FUNZIONI DA AGGIUNGERE A daily_leading.py
Copia queste funzioni NEL FILE daily_leading.py
"""

# ============================================
# FUNZIONE 1: get_sector_max_steps
# POSIZIONE: Aggiungere PRIMA di _street_anchor_clamp (circa linea 120)
# ============================================

def get_sector_max_steps(sector):
    """Get sector-specific base max_steps"""
    from ..utils.config import CONFIG
    if not sector:
        return 2
    sector_cfg = CONFIG.get_sector_anchor_params()
    if sector in sector_cfg:
        return sector_cfg[sector].get("base_max_steps", 2)
    return sector_cfg.get("default", {}).get("base_max_steps", 2)


# ============================================
# FUNZIONE 2: calculate_clamp_severity
# POSIZIONE: Aggiungere PRIMA di run_daily (circa linea 1650)
# ============================================

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


# ============================================
# FUNZIONE 3: track_rating_performance
# POSIZIONE: Aggiungere PRIMA di run_daily (circa linea 1680)
# ============================================

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
