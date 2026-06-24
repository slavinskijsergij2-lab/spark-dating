#!/bin/bash
# Usage: CF_TOKEN=your_token ./scripts/setup_email_dns.sh
set -e

CF_TOKEN="${CF_TOKEN:-$1}"
if [ -z "$CF_TOKEN" ]; then
  echo "Usage: CF_TOKEN=your_token ./scripts/setup_email_dns.sh"
  exit 1
fi

DOMAIN="spark-dating.club"
RESEND_KEY="re_5RM4nXEF_7ZDWAt232CBwRaUZnuU7HcNE"
RESEND_DOMAIN_ID="8f4a8697-ac86-4688-b9d9-ee457f3c905d"

echo "→ Getting Cloudflare Zone ID for $DOMAIN..."
ZONE_ID=$(curl -s "https://api.cloudflare.com/client/v4/zones?name=$DOMAIN" \
  -H "Authorization: Bearer $CF_TOKEN" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['result'][0]['id'])")
echo "  Zone ID: $ZONE_ID"

add_record() {
  local TYPE=$1 NAME=$2 VALUE=$3 PRIORITY=$4
  local PAYLOAD
  if [ "$TYPE" = "MX" ]; then
    PAYLOAD="{\"type\":\"$TYPE\",\"name\":\"$NAME\",\"content\":\"$VALUE\",\"priority\":$PRIORITY,\"ttl\":1,\"proxied\":false}"
  else
    PAYLOAD="{\"type\":\"$TYPE\",\"name\":\"$NAME\",\"content\":\"$VALUE\",\"ttl\":1,\"proxied\":false}"
  fi
  RESULT=$(curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
    -H "Authorization: Bearer $CF_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")
  if echo "$RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); exit(0 if d['success'] else 1)" 2>/dev/null; then
    echo "  ✅ Added $TYPE $NAME"
  else
    echo "  ⚠️  $TYPE $NAME: $(echo $RESULT | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('errors','?'))" 2>/dev/null)"
  fi
}

echo "→ Adding DNS records..."
# DKIM
add_record "TXT" "resend._domainkey.$DOMAIN" \
  "p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDwJML7ByfJIZHNyCBXIrMe/v2Z5eCipB/mohsQoAZM2qcPnHf8MYXGvxPB+lV0u7yasC8C9A6YalmTP7R6OSLLkK1tcRG1dm1f5Nr+zeVdsY2knQLEKjyRTaocdsbUOMabn07BQtnwsav9CHhLue9LNt+s8DhEKiUHyzTV8YQxkQIDAQAB"
# SPF MX
add_record "MX" "send.$DOMAIN" "feedback-smtp.us-east-1.amazonses.com" 10
# SPF TXT
add_record "TXT" "send.$DOMAIN" "v=spf1 include:amazonses.com ~all"

echo "→ Waiting 5 seconds for DNS propagation..."
sleep 5

echo "→ Triggering Resend domain verification..."
VERIFY=$(curl -s -X POST "https://api.resend.com/domains/$RESEND_DOMAIN_ID/verify" \
  -H "Authorization: Bearer $RESEND_KEY")
echo "  $VERIFY"

echo "→ Checking domain status..."
STATUS=$(curl -s "https://api.resend.com/domains/$RESEND_DOMAIN_ID" \
  -H "Authorization: Bearer $RESEND_KEY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['status'])")
echo "  Status: $STATUS"

if [ "$STATUS" = "verified" ]; then
  echo "→ Setting RESEND_FROM in Railway..."
  railway variables --set "RESEND_FROM=Spark <noreply@spark-dating.club>"
  echo "✅ Done! Emails will now be sent from noreply@spark-dating.club"
else
  echo "⚠️  Domain not yet verified (status=$STATUS). DNS may need a few minutes to propagate."
  echo "    Run this to check later:"
  echo "    curl -s https://api.resend.com/domains/$RESEND_DOMAIN_ID -H 'Authorization: Bearer $RESEND_KEY' | python3 -m json.tool"
fi
