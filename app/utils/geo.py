import math
from typing import Tuple

_EARTH_RADIUS_KM = 6371.0

# Progressive radius steps for fallback search
SEARCH_STEPS_KM = [0, 15, 50, 150]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points (lat/lon in degrees) in km."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def bounding_box(lat: float, lon: float, radius_km: float) -> Tuple[float, float, float, float]:
    """Square bounding box around a point: returns (lat_min, lat_max, lon_min, lon_max).

    Slightly over-selects — callers should refine with haversine_km.
    """
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * math.cos(math.radians(lat)) + 1e-9)
    return lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta
