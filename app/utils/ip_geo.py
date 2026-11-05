import ipaddress
import logging
import time

import httpx

_log = logging.getLogger(__name__)

# ip-api.com free tier allows 45 requests/min per server, so cache results per IP.
_CACHE_TTL = 6 * 3600
_CACHE_MAX = 5000
_cache: dict[str, tuple[float, dict]] = {}


async def get_geo_from_ip(ip: str) -> dict:
    """Return {city, country, lat, lon} from IP. Returns {} on failure."""
    try:
        ip = str(ipaddress.ip_address(ip))
    except ValueError:
        return {}
    if ipaddress.ip_address(ip).is_private or ipaddress.ip_address(ip).is_loopback:
        return {}
    now = time.monotonic()
    hit = _cache.get(ip)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"http://ip-api.com/json/{ip}?fields=status,city,country,lat,lon&lang=ru")
            data = r.json()
            if data.get("status") == "success":
                result = {
                    "city": data.get("city", ""),
                    "country": data.get("country", ""),
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                }
                if len(_cache) >= _CACHE_MAX:
                    _cache.clear()
                _cache[ip] = (now, result)
                return result
    except Exception as exc:
        _log.debug("ip_geo: failed for %s: %s", ip, exc)
    return {}


async def reverse_geocode(lat: float, lon: float) -> str:
    """Convert GPS coordinates to city name using nominatim."""
    try:
        async with httpx.AsyncClient(timeout=4.0, headers={"User-Agent": "spark-dating/1.0"}) as client:
            r = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "accept-language": "ru"},
            )
            data = r.json()
            addr = data.get("address", {})
            return addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or ""
    except Exception:
        return ""
