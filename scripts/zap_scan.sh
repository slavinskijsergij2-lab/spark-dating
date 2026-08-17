#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Spark Dating — OWASP ZAP Security Scan
#
# Требования:
#   Docker установлен и запущен
#
# Запуск:
#   ./scripts/zap_scan.sh                      # baseline scan на prod
#   ./scripts/zap_scan.sh http://localhost:8000 # локальная версия
#   ./scripts/zap_scan.sh <URL> full           # полный активный скан (медленнее!)
#
# Отчёт сохраняется в reports/zap-YYYY-MM-DD.html
#
# ⚠ ВНИМАНИЕ: full scan — активный, генерирует реальный трафик атак.
#   Запускать только на тестовом окружении, НЕ на продакшне!
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BASE="${1:-https://spark-dating.club}"
MODE="${2:-baseline}"   # baseline | full
DATE=$(date +%Y-%m-%d-%H%M)
REPORT_DIR="reports"
mkdir -p "$REPORT_DIR"

ZAP_IMAGE="ghcr.io/zaproxy/zaproxy:stable"
REPORT_FILE="zap-${MODE}-${DATE}.html"
REPORT_PATH="$REPORT_DIR/$REPORT_FILE"

# Абсолютный путь нужен для docker volume
ABS_REPORT_DIR="$(cd "$REPORT_DIR" && pwd)"

echo "→ OWASP ZAP ${MODE} scan"
echo "  Target: $BASE"
echo "  Report: $REPORT_PATH"
echo ""

# Проверяем Docker
if ! command -v docker &>/dev/null; then
    echo "✗ Docker not found. Install Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker info &>/dev/null 2>&1; then
    echo "✗ Docker daemon not running. Start Docker and try again."
    exit 1
fi

echo "→ Pulling ZAP image (only first run takes time)..."
docker pull "$ZAP_IMAGE" --quiet

echo "→ Starting scan..."

if [ "$MODE" = "full" ]; then
    echo "⚠ Full/active scan — may take 10-30 minutes"
    docker run --rm \
        -v "$ABS_REPORT_DIR:/zap/wrk/:rw" \
        -u "$(id -u):$(id -g)" \
        "$ZAP_IMAGE" \
        zap-full-scan.py \
            -t "$BASE" \
            -r "$REPORT_FILE" \
            -I \
            -j \
            --auto \
            2>&1 | tail -50
else
    echo "→ Baseline scan (passive only, ~2-5 min)..."
    docker run --rm \
        -v "$ABS_REPORT_DIR:/zap/wrk/:rw" \
        -u "$(id -u):$(id -g)" \
        "$ZAP_IMAGE" \
        zap-baseline.py \
            -t "$BASE" \
            -r "$REPORT_FILE" \
            -I \
            2>&1 | tail -50
fi

echo ""
echo "✓ Scan complete: $REPORT_PATH"
echo "Open in browser to review vulnerabilities (FAIL=High, WARN=Medium, INFO=Low)"
echo ""
echo "Common checks ZAP performs:"
echo "  • Missing security headers (CSP, X-Frame-Options, etc.)"
echo "  • Cookie flags (HttpOnly, Secure, SameSite)"
echo "  • Information disclosure (server banners, error details)"
echo "  • Clickjacking protection"
echo "  • Cross-domain script inclusion"
echo ""
if [ "$MODE" = "baseline" ]; then
    echo "Tip: run with 'full' mode for active testing (only on staging!):"
    echo "  ./scripts/zap_scan.sh http://staging-url full"
fi
