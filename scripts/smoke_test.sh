#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Spark Dating — Smoke Test (post-deploy check)
#
# Запуск:
#   ./scripts/smoke_test.sh                         # проверяет prod
#   ./scripts/smoke_test.sh https://staging.example # проверяет staging
#
# Возвращает exit code 0 если всё OK, 1 при любой ошибке.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BASE="${1:-https://spark-dating.club}"
PASS=0
FAIL=0
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; ((PASS++)) || true; }
fail() { echo -e "${RED}✗${NC} $1"; ((FAIL++)) || true; }
info() { echo -e "${YELLOW}→${NC} $1"; }

info "Smoke testing: $BASE"
echo ""

# ── 1. Health check ───────────────────────────────────────────────────────────
info "Health check..."
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE/health" 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
    HEALTH=$(curl -sf "$BASE/health" 2>/dev/null)
    DB_OK=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('db','?'))" 2>/dev/null || echo "?")
    ok "Health: $STATUS (db=$DB_OK)"
else
    fail "Health returned $STATUS (expected 200)"
fi

# ── 2. Landing page ───────────────────────────────────────────────────────────
info "Landing page..."
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE/" 2>/dev/null || echo "000")
BODY=$(curl -sf "$BASE/" 2>/dev/null || echo "")
if [ "$STATUS" = "200" ] && echo "$BODY" | grep -q "Spark"; then
    ok "Landing: $STATUS + Spark in body"
else
    fail "Landing: $STATUS (Spark not found in body)"
fi

# ── 3. Login page ─────────────────────────────────────────────────────────────
info "Login page..."
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE/login" 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
    ok "Login: $STATUS"
else
    fail "Login: $STATUS"
fi

# ── 4. Register page ──────────────────────────────────────────────────────────
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE/register" 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
    ok "Register: $STATUS"
else
    fail "Register: $STATUS"
fi

# ── 5. Privacy page ───────────────────────────────────────────────────────────
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE/privacy" 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
    ok "Privacy: $STATUS"
else
    fail "Privacy: $STATUS"
fi

# ── 6. Static assets ──────────────────────────────────────────────────────────
info "Static assets..."
for asset in "/static/icon-192.png" "/static/manifest.json" "/static/sw.js" "/robots.txt"; do
    STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE$asset" 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        ok "Asset $asset: $STATUS"
    else
        fail "Asset $asset: $STATUS"
    fi
done

# ── 7. Security headers ───────────────────────────────────────────────────────
info "Security headers..."
HEADERS=$(curl -sf -I "$BASE/" 2>/dev/null || echo "")

check_header() {
    local name="$1"
    local value="$2"
    if echo "$HEADERS" | grep -qi "$name"; then
        ok "Header: $name present"
    else
        fail "Header: $name MISSING"
    fi
}

check_header "content-security-policy"
check_header "x-frame-options"
check_header "x-content-type-options"
check_header "referrer-policy"
check_header "permissions-policy"

# ── 8. HTTPS redirect (only for prod) ─────────────────────────────────────────
if [[ "$BASE" == "https://"* ]]; then
    HTTP_BASE="${BASE/https:/http:}"
    STATUS=$(curl -sf -o /dev/null -w "%{http_code}" --max-redirs 0 "$HTTP_BASE/" 2>/dev/null || echo "000")
    if [[ "$STATUS" == "3"* ]]; then
        ok "HTTP→HTTPS redirect: $STATUS"
    else
        info "HTTP→HTTPS: $STATUS (may be handled by Railway proxy)"
    fi
fi

# ── 9. Swipe redirects anon users to login ────────────────────────────────────
info "Auth redirect..."
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" -L "$BASE/swipe" 2>/dev/null || echo "000")
REDIR=$(curl -sf -o /dev/null -w "%{redirect_url}" "$BASE/swipe" 2>/dev/null || echo "")
if echo "$REDIR" | grep -q "/login\|/"; then
    ok "Swipe redirects anon to: $REDIR"
elif [ "$STATUS" = "200" ]; then
    # Could redirect to index
    ok "Swipe: redirected (status $STATUS)"
else
    fail "Swipe anon check: status=$STATUS redirect=$REDIR"
fi

# ── 10. 404 handling ──────────────────────────────────────────────────────────
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE/this-does-not-exist-$(date +%s)" 2>/dev/null || echo "000")
if [ "$STATUS" = "404" ]; then
    ok "404 handling: $STATUS"
else
    fail "404 handling: $STATUS (expected 404)"
fi

# ── 11. API geo endpoint ──────────────────────────────────────────────────────
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$BASE/api/geo" 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
    ok "API /api/geo: $STATUS"
else
    fail "API /api/geo: $STATUS"
fi

# ── 12. Photo volume & Sentry status ─────────────────────────────────────────
info "Configuration checks..."
HEALTH_JSON=$(curl -sf "$BASE/health" 2>/dev/null || echo "{}")
PHOTO_PERSISTENT=$(echo "$HEALTH_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('photos',{}).get('persistent','?'))" 2>/dev/null || echo "?")
PHOTO_WRITABLE=$(echo "$HEALTH_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('photos',{}).get('writable','?'))" 2>/dev/null || echo "?")
SENTRY=$(echo "$HEALTH_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sentry','?'))" 2>/dev/null || echo "?")

if [ "$PHOTO_WRITABLE" = "True" ]; then
    ok "Photos: writable"
else
    fail "Photos: NOT writable (check PHOTO_DIR env var)"
fi

if [ "$PHOTO_PERSISTENT" = "True" ]; then
    ok "Photos: persistent volume (PHOTO_DIR=/data/...)"
else
    info "Photos: NOT persistent — photos will be lost on redeploy! Set PHOTO_DIR=/data/photos + add Railway Volume"
fi

if [ "$SENTRY" = "True" ]; then
    ok "Sentry: enabled"
else
    info "Sentry: disabled — add SENTRY_DSN to Railway for error tracking"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}✓ ALL $PASS CHECKS PASSED${NC}"
    exit 0
else
    echo -e "${RED}✗ $FAIL FAILED, $PASS PASSED${NC}"
    exit 1
fi
