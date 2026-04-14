#!/usr/bin/env python3
"""Update Rating Performance - Weekly Job"""
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from database.operations import DatabaseManager

def evaluate_accuracy(rating, expected, actual):
    if rating == "Strong Buy":
        return "correct" if actual > 0.20 else "incorrect"
    elif rating == "Buy":
        return "correct" if actual > 0.05 else "incorrect"
    elif rating == "Accumulate":
        return "correct" if actual > 0.00 else "incorrect"
    elif rating == "Hold":
        return "correct" if -0.10 <= actual <= 0.10 else "incorrect"
    elif rating == "Reduce":
        return "correct" if actual < -0.05 else "incorrect"
    elif rating in ["Sell", "Strong Sell"]:
        return "correct" if actual < -0.10 else "incorrect"
    return "pending"

def get_price_at_date(db, symbol, target_date):
    with db.get_connection() as conn:
        cursor = conn.execute("""
            SELECT close FROM eod_prices_daily 
            WHERE symbol_yahoo = ? AND date = ?
        """, (symbol, target_date))
        row = cursor.fetchone()
        if row:
            return row[0]
        cursor = conn.execute("""
            SELECT close FROM eod_prices_daily 
            WHERE symbol_yahoo = ? 
            AND date BETWEEN date(?, '-5 days') AND date(?, '+5 days')
            ORDER BY ABS(julianday(date) - julianday(?))
            LIMIT 1
        """, (symbol, target_date, target_date, target_date))
        row = cursor.fetchone()
        return row[0] if row else None

def update_performance(target_date=None, dry_run=False):
    db = DatabaseManager()
    if target_date is None:
        target_date = (datetime.now() - timedelta(days=365)).date()
    else:
        target_date = datetime.fromisoformat(target_date).date()
    
    start_date = target_date - timedelta(days=7)
    end_date = target_date + timedelta(days=7)
    
    print("="*80)
    print(f"RATING PERFORMANCE UPDATE - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*80)
    print(f"Checking ratings from {start_date} to {end_date}")
    
    with db.get_connection() as conn:
        cursor = conn.execute("""
            SELECT symbol_yahoo, rating_date, rating, expected_return_12m_pct, price_at_rating
            FROM rating_performance_tracking
            WHERE rating_accuracy = 'pending'
            AND rating_date BETWEEN ? AND ?
        """, (start_date.isoformat(), end_date.isoformat()))
        pending = cursor.fetchall()
        
        if not pending:
            print("✅ No pending ratings")
            return
        
        print(f"Found {len(pending)} ratings to update\n")
        updated = 0
        skipped = 0
        
        for row in pending:
            symbol, rating_date, rating, expected, price_at_rating = row
            rating_date_dt = datetime.fromisoformat(rating_date).date()
            date_12m = (rating_date_dt + timedelta(days=365)).isoformat()
            price_now = get_price_at_date(db, symbol, date_12m)
            
            if not price_now:
                print(f"⚠️  SKIP {symbol}: No price at {date_12m}")
                skipped += 1
                continue
            
            actual = (price_now / price_at_rating) - 1.0
            accuracy = evaluate_accuracy(rating, expected, actual)
            status = "✅" if accuracy == "correct" else "❌"
            print(f"{status} {symbol:10s} {rating:12s} | Exp: {expected:>7.1%} | Act: {actual:>7.1%}")
            
            if not dry_run:
                conn.execute("""
                    UPDATE rating_performance_tracking
                    SET price_12m_later = ?, actual_return_12m_pct = ?, 
                        rating_accuracy = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE symbol_yahoo = ? AND rating_date = ?
                """, (price_now, actual, accuracy, symbol, rating_date))
            updated += 1
        
        if not dry_run:
            conn.commit()
        
        print(f"\n{'='*80}")
        print(f"✅ Updated: {updated}, Skipped: {skipped}")
        if dry_run:
            print("⚠️  DRY RUN - No changes saved")
        print("="*80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        update_performance(args.date, args.dry_run)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        sys.exit(1)
