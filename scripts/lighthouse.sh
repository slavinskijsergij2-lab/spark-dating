#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Spark Dating — Lighthouse аудит (производительность, SEO, accessibility)
#
# Требования:
#   npm install -g lighthouse chrome-launcher
#   (или использовать Chrome DevTools → Lighthouse напрямую)
#
# Запуск:
#   ./scripts/lighthouse.sh                     # prod
#   ./scripts/lighthouse.sh http://localhost:8000  # локально
#
# Отчёт сохраняется в reports/lighthouse-YYYY-MM-DD.html
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BASE="${1:-https://spark-dating.club}"
DATE=$(date +%Y-%m-%d-%H%M)
REPORT_DIR="reports"
mkdir -p "$REPORT_DIR"

echo "→ Running Lighthouse on $BASE"
echo "  (may take 30-60 seconds...)"

# Запуск Lighthouse
npx lighthouse "$BASE" \
  --output=html \
  --output-path="$REPORT_DIR/lighthouse-$DATE.html" \
  --preset=desktop \
  --chrome-flags="--headless --no-sandbox --disable-gpu" \
  --only-categories=performance,accessibility,best-practices,seo,pwa \
  --quiet

# Дублируем для мобильных (важно для dating-сайта)
npx lighthouse "$BASE" \
  --output=html \
  --output-path="$REPORT_DIR/lighthouse-mobile-$DATE.html" \
  --form-factor=mobile \
  --chrome-flags="--headless --no-sandbox --disable-gpu" \
  --only-categories=performance,accessibility,best-practices,seo,pwa \
  --quiet

echo ""
echo "✓ Reports saved:"
echo "  Desktop: $REPORT_DIR/lighthouse-$DATE.html"
echo "  Mobile:  $REPORT_DIR/lighthouse-mobile-$DATE.html"
echo ""
echo "Open in browser to see scores (0-100 for each category)."
echo "Target: Performance ≥ 80, Accessibility ≥ 90, SEO ≥ 90"
