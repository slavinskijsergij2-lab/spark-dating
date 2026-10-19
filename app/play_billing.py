"""Google Play Developer API client for verifying purchases made in the Android TWA.

Configured via env:
  PLAY_PACKAGE_NAME          — Android package, e.g. club.spark_dating.twa
  PLAY_SERVICE_ACCOUNT_JSON  — service-account key JSON (raw or base64) with access
                               to the app in Play Console (Financial data → View)
  PLAY_SKU_MONTHLY           — subscription product ID
  PLAY_SKU_LIFETIME          — one-time (in-app) product ID
"""
import base64
import json
import logging
import os
import time
from datetime import datetime
from urllib.parse import quote

import httpx
import jwt

PACKAGE_NAME = os.getenv("PLAY_PACKAGE_NAME", "")
SKU_MONTHLY = os.getenv("PLAY_SKU_MONTHLY", "")
SKU_LIFETIME = os.getenv("PLAY_SKU_LIFETIME", "")

_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"

# A cancelled subscription stays usable until its expiry; on-hold/paused/expired don't.
ENTITLED_STATES = {
    "SUBSCRIPTION_STATE_ACTIVE",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
    "SUBSCRIPTION_STATE_CANCELED",
}


def _load_service_account() -> dict | None:
    raw = os.getenv("PLAY_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    try:
        if not raw.startswith("{"):
            raw = base64.b64decode(raw).decode()
        return json.loads(raw)
    except Exception as e:
        logging.error("play_billing: bad PLAY_SERVICE_ACCOUNT_JSON: %s", e)
        return None


_service_account = _load_service_account()

play_enabled = bool(PACKAGE_NAME and _service_account and (SKU_MONTHLY or SKU_LIFETIME))


class PlayError(Exception):
    pass


class PurchaseNotFound(PlayError):
    """Google doesn't know this token (invalid, or for another package)."""


_token_cache: dict = {"token": None, "exp": 0.0}


async def _access_token() -> str:
    if _token_cache["token"] and _token_cache["exp"] > time.time() + 60:
        return _token_cache["token"]
    if not _service_account:
        raise PlayError("Play billing not configured")
    now = int(time.time())
    token_uri = _service_account.get("token_uri", "https://oauth2.googleapis.com/token")
    assertion = jwt.encode(
        {"iss": _service_account["client_email"], "scope": _SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600},
        _service_account["private_key"],
        algorithm="RS256",
        headers={"kid": _service_account.get("private_key_id", "")},
    )
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(token_uri, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        })
    if r.status_code != 200:
        raise PlayError(f"token exchange failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["exp"] = time.time() + int(data.get("expires_in", 3600))
    return _token_cache["token"]


async def _call(method: str, path: str) -> dict:
    token = await _access_token()
    url = f"{_API}/{quote(PACKAGE_NAME)}/{path}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.request(method, url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code in (400, 404, 410):
        raise PurchaseNotFound(f"purchase not found: {r.status_code}")
    if r.status_code >= 300:
        raise PlayError(f"Play API {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


async def get_subscription(purchase_token: str) -> dict:
    return await _call("GET", f"purchases/subscriptionsv2/tokens/{quote(purchase_token, safe='')}")


async def acknowledge_subscription(product_id: str, purchase_token: str) -> None:
    await _call("POST", f"purchases/subscriptions/{quote(product_id, safe='')}/tokens/{quote(purchase_token, safe='')}:acknowledge")


async def get_product(product_id: str, purchase_token: str) -> dict:
    return await _call("GET", f"purchases/products/{quote(product_id, safe='')}/tokens/{quote(purchase_token, safe='')}")


async def acknowledge_product(product_id: str, purchase_token: str) -> None:
    await _call("POST", f"purchases/products/{quote(product_id, safe='')}/tokens/{quote(purchase_token, safe='')}:acknowledge")


def subscription_expiry(sub: dict) -> datetime | None:
    """Latest line-item expiry as a naive UTC datetime (the DB stores naive UTC)."""
    expiries = []
    for item in sub.get("lineItems") or []:
        ts = item.get("expiryTime")
        if ts:
            # RFC3339 with nanoseconds, e.g. 2026-11-19T10:00:00.123456789Z
            ts = ts.rstrip("Z").split(".")[0]
            expiries.append(datetime.fromisoformat(ts))
    return max(expiries) if expiries else None


def subscription_product_ids(sub: dict) -> set[str]:
    return {item.get("productId") for item in sub.get("lineItems") or [] if item.get("productId")}
