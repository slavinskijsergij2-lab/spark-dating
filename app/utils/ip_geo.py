import logging
import httpx

_log = logging.getLogger(__name__)

async def get_geo_from_ip(ip: str) -> dict:
    """Return {city, country, lat, lon} from IP. Returns {} on failure."""
    if not ip or ip in ("127.0.0.1", "::1", "testclient"):
        return {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"http://ip-api.com/json/{ip}?fields=status,city,country,lat,lon&lang=ru")
            data = r.json()
            if data.get("status") == "success":
                return {
                    "city": data.get("city", ""),
                    "country": data.get("country", ""),
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                }
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
