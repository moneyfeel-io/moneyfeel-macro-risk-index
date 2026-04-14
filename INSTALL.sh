#!/bin/bash
# Script di installazione rapido

echo "=============================================="
echo "INSTALLAZIONE MODIFICHE VALUATION"
echo "=============================================="

cd "$(dirname "$0")"

echo "[1/4] Copio config files..."
cp sector_params.yaml ../yahoo/valuation_engine/config/
cp config.py ../yahoo/valuation_engine/utils/

echo "[2/4] Copio scripts..."
cp create_rating_tracking_tables.py ../scripts/
cp update_rating_performance.py ../scripts/
chmod +x ../scripts/*.py

echo "[3/4] Eseguo migration..."
cd ../yahoo
python scripts/create_rating_tracking_tables.py

echo "[4/4] Test config..."
python -c "from valuation_engine.utils.config import CONFIG; print('Config OK:', 'Technology' in CONFIG.get_sector_anchor_params())"

echo ""
echo "=============================================="
echo "✅ INSTALLAZIONE COMPLETATA!"
echo "=============================================="
echo ""
echo "PROSSIMI PASSI:"
echo "1. Modifica daily_leading.py seguendo DAILY_LEADING_MODIFICATIONS.txt"
echo "2. Aggiungi le 3 funzioni da DAILY_LEADING_NEW_FUNCTIONS.py"
echo "3. Test: python scripts/test_fase_3_4.py"
echo ""
