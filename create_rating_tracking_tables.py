#!/usr/bin/env python3
"""Database Migration - Rating Performance Tracking Tables"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from database.operations import DatabaseManager

CREATE_RATING_PERFORMANCE = """
CREATE TABLE IF NOT EXISTS rating_performance_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_yahoo TEXT NOT NULL,
    rating_date DATE NOT NULL,
    rating TEXT NOT NULL,
    rating_reason TEXT,
    expected_return_12m_pct REAL,
    price_at_rating REAL NOT NULL,
    fair_value_base REAL,
    street_anchor_applied INTEGER DEFAULT 0,
    street_anchor_clamp_severity REAL,
    value_trap_detected INTEGER DEFAULT 0,
    fundamental_override INTEGER DEFAULT 0,
    price_12m_later REAL,
    actual_return_12m_pct REAL,
    rating_accuracy TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol_yahoo, rating_date),
    FOREIGN KEY (symbol_yahoo) REFERENCES symbols_master(symbol_yahoo) ON DELETE CASCADE
);
"""

CREATE_INDICES_PERF = """
CREATE INDEX IF NOT EXISTS idx_rating_perf_symbol_date ON rating_performance_tracking(symbol_yahoo, rating_date);
CREATE INDEX IF NOT EXISTS idx_rating_perf_accuracy ON rating_performance_tracking(rating_accuracy);
CREATE INDEX IF NOT EXISTS idx_rating_perf_rating ON rating_performance_tracking(rating);
"""

CREATE_ANALYST_QUALITY = """
CREATE TABLE IF NOT EXISTS analyst_quality (
    analyst_firm TEXT PRIMARY KEY,
    accuracy_score REAL DEFAULT 0.5,
    timing_score REAL DEFAULT 0.5,
    coverage_breadth INTEGER DEFAULT 0,
    total_calls INTEGER DEFAULT 0,
    correct_calls INTEGER DEFAULT 0,
    avg_return_delta REAL DEFAULT 0.0,
    tier TEXT DEFAULT 'unknown',
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK(accuracy_score BETWEEN 0 AND 1),
    CHECK(timing_score BETWEEN 0 AND 1),
    CHECK(tier IN ('tier1', 'tier2', 'tier3', 'unknown'))
);
"""

SEED_ANALYSTS = """
INSERT OR IGNORE INTO analyst_quality (analyst_firm, tier, accuracy_score, timing_score) VALUES
('Goldman Sachs', 'tier1', 0.75, 0.75),
('Morgan Stanley', 'tier1', 0.73, 0.72),
('JP Morgan', 'tier1', 0.72, 0.71),
('Bank of America', 'tier1', 0.70, 0.70),
('Barclays', 'tier2', 0.65, 0.65),
('Citigroup', 'tier2', 0.65, 0.64),
('Wells Fargo', 'tier2', 0.63, 0.63),
('UBS', 'tier2', 0.62, 0.62),
('Deutsche Bank', 'tier2', 0.60, 0.60),
('Credit Suisse', 'tier2', 0.58, 0.58);
"""

CREATE_ANALYST_HISTORY = """
CREATE TABLE IF NOT EXISTS analyst_ratings_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_yahoo TEXT NOT NULL,
    analyst_firm TEXT NOT NULL,
    analyst_name TEXT,
    rating TEXT NOT NULL,
    rating_numeric REAL,
    target_price REAL,
    rating_date DATE NOT NULL,
    FOREIGN KEY (analyst_firm) REFERENCES analyst_quality(analyst_firm),
    FOREIGN KEY (symbol_yahoo) REFERENCES symbols_master(symbol_yahoo) ON DELETE CASCADE
);
"""

CREATE_INDICES_HIST = """
CREATE INDEX IF NOT EXISTS idx_analyst_ratings_symbol ON analyst_ratings_history(symbol_yahoo, rating_date);
CREATE INDEX IF NOT EXISTS idx_analyst_ratings_firm ON analyst_ratings_history(analyst_firm);
"""

CREATE_VIEW = """
CREATE VIEW IF NOT EXISTS rating_accuracy_metrics AS
SELECT 
    rating,
    COUNT(*) as total_ratings,
    AVG(actual_return_12m_pct) as avg_return,
    COUNT(CASE WHEN rating_accuracy = 'correct' THEN 1 END) * 1.0 / 
        COUNT(CASE WHEN rating_accuracy IN ('correct', 'incorrect') THEN 1 END) as accuracy_rate,
    AVG(CASE WHEN street_anchor_applied = 1 THEN 1 ELSE 0 END) as anchor_rate,
    AVG(CASE WHEN value_trap_detected = 1 THEN 1 ELSE 0 END) as trap_rate,
    AVG(street_anchor_clamp_severity) as avg_clamp_severity
FROM rating_performance_tracking
WHERE rating_accuracy IN ('correct', 'incorrect')
GROUP BY rating;
"""

def run_migration():
    print("="*80)
    print("DATABASE MIGRATION: Rating Performance Tracking")
    print("="*80)
    db = DatabaseManager()
    try:
        with db.get_connection() as conn:
            print("[1/7] Creating rating_performance_tracking...")
            conn.execute(CREATE_RATING_PERFORMANCE)
            print("      ✅ Done")
            
            print("[2/7] Creating indices...")
            conn.executescript(CREATE_INDICES_PERF)
            print("      ✅ Done")
            
            print("[3/7] Creating analyst_quality...")
            conn.execute(CREATE_ANALYST_QUALITY)
            print("      ✅ Done")
            
            print("[4/7] Seeding analysts...")
            conn.executescript(SEED_ANALYSTS)
            print("      ✅ Done")
            
            print("[5/7] Creating analyst_ratings_history...")
            conn.execute(CREATE_ANALYST_HISTORY)
            print("      ✅ Done")
            
            print("[6/7] Creating indices...")
            conn.executescript(CREATE_INDICES_HIST)
            print("      ✅ Done")
            
            print("[7/7] Creating view...")
            conn.execute(CREATE_VIEW)
            print("      ✅ Done")
            
            conn.commit()
        
        print("\n" + "="*80)
        print("✅ MIGRATION COMPLETED")
        print("="*80)
        return True
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return False

if __name__ == "__main__":
    sys.exit(0 if run_migration() else 1)
