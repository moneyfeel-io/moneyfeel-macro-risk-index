-- QUERY 1: Overall Rating Accuracy
SELECT 
    rating,
    COUNT(*) as total,
    ROUND(AVG(actual_return_12m_pct) * 100, 1) as avg_return_pct,
    ROUND(COUNT(CASE WHEN rating_accuracy = 'correct' THEN 1 END) * 100.0 / 
        COUNT(CASE WHEN rating_accuracy IN ('correct', 'incorrect') THEN 1 END), 1) as accuracy_pct
FROM rating_performance_tracking
WHERE rating_accuracy IN ('correct', 'incorrect')
GROUP BY rating;

-- QUERY 2: Street Anchor Impact
SELECT 
    CASE WHEN street_anchor_applied = 1 THEN 'With Anchor' ELSE 'No Anchor' END as status,
    COUNT(*) as total,
    ROUND(AVG(actual_return_12m_pct) * 100, 1) as avg_return_pct,
    ROUND(AVG(street_anchor_clamp_severity) * 100, 1) as avg_severity_pct
FROM rating_performance_tracking
WHERE rating_accuracy IN ('correct', 'incorrect')
GROUP BY status;

-- QUERY 3: Value Trap Detection
SELECT 
    CASE WHEN value_trap_detected = 1 THEN 'Trap Flagged' ELSE 'No Trap' END as status,
    COUNT(*) as total,
    ROUND(AVG(actual_return_12m_pct) * 100, 1) as avg_return_pct,
    COUNT(CASE WHEN actual_return_12m_pct < -0.20 THEN 1 END) as severe_losses
FROM rating_performance_tracking
WHERE rating_accuracy IN ('correct', 'incorrect')
GROUP BY status;

-- QUERY 4: Recent Ratings (Last 20)
SELECT 
    symbol_yahoo,
    rating_date,
    rating,
    ROUND(expected_return_12m_pct * 100, 1) as exp_pct,
    ROUND(actual_return_12m_pct * 100, 1) as act_pct,
    rating_accuracy
FROM rating_performance_tracking
WHERE rating_accuracy IN ('correct', 'incorrect')
ORDER BY rating_date DESC
LIMIT 20;

-- QUERY 5: Pending Ratings Summary
SELECT 
    rating,
    COUNT(*) as total_pending,
    MIN(rating_date) as oldest,
    MAX(rating_date) as newest
FROM rating_performance_tracking
WHERE rating_accuracy = 'pending'
GROUP BY rating;
