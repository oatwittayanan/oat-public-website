#!/bin/bash
# update_website.sh — รัน script เดียวเพื่ออัพเดทเว็บไซต์ทั้งหมด
# ใช้หลังจาก: แก้ไข stocks/*.md, valuations, หรืออยาก force-sync trends
#
# Usage:
#   ./scripts/update_website.sh              # full update
#   ./scripts/update_website.sh --no-push   # generate only, ไม่ push

set -e

PUSH=true
if [[ "$1" == "--no-push" ]]; then
  PUSH=false
fi

WEBSITE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OAT_OS_DIR="$(cd "$WEBSITE_DIR/.." && pwd)"
KNOWLEDGE_DIR="$OAT_OS_DIR/oat-investment-knowledge"

echo "=== OAT Website Update ==="
echo "Website : $WEBSITE_DIR"
echo "Push    : $PUSH"
echo ""

# Step 1: Generate trends.js
echo "[1/3] Generating trends.js..."
cd "$WEBSITE_DIR"
python3 scripts/generate_trends.py
echo ""

# Step 2: Generate knowledge.js + stocks.json
echo "[2/3] Generating knowledge.js + stocks.json..."
python3 scripts/generate_site_data.py
echo ""

# Step 3: Push oat-public-website
if [ "$PUSH" = true ]; then
  echo "[3/3] Committing and pushing oat-public-website..."
  git add trends.js knowledge.js data/stocks.json stock.html scripts/generate_trends.py scripts/generate_site_data.py
  if git diff --cached --quiet; then
    echo "  No changes to commit."
  else
    git commit -m "chore: update site data $(date +%Y-%m-%d)"
    git push
    echo "  Pushed."
  fi
else
  echo "[3/3] Skipped (--no-push)"
fi

echo ""
echo "Done."
