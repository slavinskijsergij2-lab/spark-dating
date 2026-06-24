#!/usr/bin/env python3
"""
One-shot Stripe setup for Spark Dating.

Usage:
    python3 scripts/setup_stripe.py <STRIPE_SECRET_KEY>

What it does:
1. Creates "Spark Premium Monthly" recurring price
2. Creates "Spark Premium Lifetime" one-time price
3. Creates a webhook endpoint on Stripe pointing to spark-dating.club
4. Sets all required env vars on Railway via CLI

Run once. Safe to re-run (detects existing products).
"""

import json
import subprocess
import sys
import urllib.request
import urllib.error

APP_URL = "https://spark-dating.club"
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "invoice.payment_succeeded",
    "customer.subscription.deleted",
    "customer.subscription.paused",
]

MONTHLY_PRICE_RUB = 29900   # 299.00 RUB in kopecks
LIFETIME_PRICE_RUB = 99900  # 999.00 RUB in kopecks


def stripe_request(method: str, path: str, data: dict | None, secret_key: str) -> dict:
    url = f"https://api.stripe.com/v1/{path}"
    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = None
    if data:
        from urllib.parse import urlencode
        body = urlencode(data, doseq=True).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = json.loads(e.read())
        raise RuntimeError(f"Stripe API error: {err.get('error', {}).get('message', str(err))}")


def find_existing_product(name: str, secret_key: str) -> str | None:
    resp = stripe_request("GET", "products?limit=100", None, secret_key)
    for p in resp.get("data", []):
        if p.get("name") == name and p.get("active"):
            return p["id"]
    return None


def find_existing_price(product_id: str, secret_key: str) -> str | None:
    resp = stripe_request("GET", f"prices?product={product_id}&active=true&limit=10", None, secret_key)
    data = resp.get("data", [])
    if data:
        return data[0]["id"]
    return None


def find_existing_webhook(url: str, secret_key: str) -> tuple[str, str] | None:
    resp = stripe_request("GET", "webhook_endpoints?limit=100", None, secret_key)
    for wh in resp.get("data", []):
        if wh.get("url") == url:
            return wh["id"], wh.get("secret", "")
    return None


def railway_set_vars(vars_dict: dict) -> None:
    for key, value in vars_dict.items():
        result = subprocess.run(
            ["railway", "variables", "set", f"{key}={value}"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"  ⚠️  Railway CLI error for {key}: {result.stderr.strip()}")
        else:
            print(f"  ✓ Railway: {key} = {value[:12]}...")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/setup_stripe.py <STRIPE_SECRET_KEY>")
        print("\nGet your key from: https://dashboard.stripe.com/apikeys")
        sys.exit(1)

    sk = sys.argv[1].strip()
    if not sk.startswith("sk_"):
        print("Error: key must start with sk_test_ or sk_live_")
        sys.exit(1)

    is_live = sk.startswith("sk_live_")
    mode = "LIVE" if is_live else "TEST"
    print(f"\n🔑 Stripe mode: {mode}")
    print("=" * 50)

    # ── 1. Monthly product + price ───────────────────────────────────────────
    print("\n1️⃣  Monthly subscription...")
    monthly_product_id = find_existing_product("Spark Premium Monthly", sk)
    if monthly_product_id:
        print(f"   Found existing product: {monthly_product_id}")
    else:
        product = stripe_request("POST", "products", {
            "name": "Spark Premium Monthly",
            "description": "Ежемесячная подписка Spark Premium — все возможности разблокированы",
        }, sk)
        monthly_product_id = product["id"]
        print(f"   Created product: {monthly_product_id}")

    monthly_price_id = find_existing_price(monthly_product_id, sk)
    if monthly_price_id:
        print(f"   Found existing price: {monthly_price_id}")
    else:
        price = stripe_request("POST", "prices", {
            "product": monthly_product_id,
            "unit_amount": MONTHLY_PRICE_RUB,
            "currency": "rub",
            "recurring[interval]": "month",
            "nickname": "Monthly RUB",
        }, sk)
        monthly_price_id = price["id"]
        print(f"   Created price: {monthly_price_id}")

    # ── 2. Lifetime product + price ──────────────────────────────────────────
    print("\n2️⃣  Lifetime one-time payment...")
    lifetime_product_id = find_existing_product("Spark Premium Lifetime", sk)
    if lifetime_product_id:
        print(f"   Found existing product: {lifetime_product_id}")
    else:
        product = stripe_request("POST", "products", {
            "name": "Spark Premium Lifetime",
            "description": "Spark Premium навсегда — один платёж, все возможности без ограничений",
        }, sk)
        lifetime_product_id = product["id"]
        print(f"   Created product: {lifetime_product_id}")

    lifetime_price_id = find_existing_price(lifetime_product_id, sk)
    if lifetime_price_id:
        print(f"   Found existing price: {lifetime_price_id}")
    else:
        price = stripe_request("POST", "prices", {
            "product": lifetime_product_id,
            "unit_amount": LIFETIME_PRICE_RUB,
            "currency": "rub",
            "nickname": "Lifetime RUB",
        }, sk)
        lifetime_price_id = price["id"]
        print(f"   Created price: {lifetime_price_id}")

    # ── 3. Webhook ───────────────────────────────────────────────────────────
    webhook_url = f"{APP_URL}/billing/webhook"
    print(f"\n3️⃣  Webhook → {webhook_url}")
    existing = find_existing_webhook(webhook_url, sk)
    if existing:
        webhook_id, webhook_secret = existing
        print(f"   Found existing webhook: {webhook_id}")
        if not webhook_secret:
            # Need to re-create to get secret
            stripe_request("DELETE", f"webhook_endpoints/{webhook_id}", None, sk)
            existing = None

    if not existing:
        webhook = stripe_request("POST", "webhook_endpoints", {
            "url": webhook_url,
            "enabled_events[]": WEBHOOK_EVENTS,
            "description": "Spark Dating billing events",
        }, sk)
        webhook_secret = webhook["secret"]
        print(f"   Created webhook: {webhook['id']}")
        print(f"   Signing secret: {webhook_secret[:20]}...")

    # ── 4. Get publishable key ───────────────────────────────────────────────
    # Derive publishable key prefix from secret key
    pk_prefix = "pk_live_" if is_live else "pk_test_"
    print(f"\n4️⃣  Publishable key...")
    print(f"   ⚠️  Please paste your publishable key ({pk_prefix}...):")
    pk = input("   > ").strip()
    if not pk.startswith(pk_prefix[:3]):
        print(f"   Warning: expected key starting with {pk_prefix}, got {pk[:10]}...")

    # ── 5. Set Railway env vars ──────────────────────────────────────────────
    print("\n5️⃣  Setting Railway environment variables...")
    railway_set_vars({
        "STRIPE_SECRET_KEY": sk,
        "STRIPE_PUBLISHABLE_KEY": pk,
        "STRIPE_PRICE_MONTHLY": monthly_price_id,
        "STRIPE_PRICE_LIFETIME": lifetime_price_id,
        "STRIPE_WEBHOOK_SECRET": webhook_secret,
    })

    print("\n✅ Done! Railway will redeploy automatically.")
    print("\nTest the flow:")
    print(f"  → {APP_URL}/premium")
    if not is_live:
        print("\nTest card: 4242 4242 4242 4242 | any future date | any CVC")
