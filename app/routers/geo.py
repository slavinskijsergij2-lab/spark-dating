from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.geo import GermanLocation

router = APIRouter(prefix="/geo", tags=["geo"])


@router.get("/autocomplete")
async def autocomplete(
    q: str = Query(..., min_length=2, max_length=50),
    db: AsyncSession = Depends(get_db),
):
    """Return up to 8 German cities/towns matching the query prefix.

    Searches by display name AND ASCII name (handles ä→a, ö→o, ü→u substitutions).
    """
    q_clean = q.strip()
    # Simple transliteration for users typing without umlauts: ö→o ü→u ä→a
    _tr = str.maketrans("äöüÄÖÜ", "aouAOU")
    q_simple = q_clean.translate(_tr).replace("ß", "ss").lower()
    result = await db.execute(
        select(GermanLocation)
        .where(
            or_(
                GermanLocation.name.ilike(f"{q_clean}%"),
                GermanLocation.name_ascii.ilike(f"{q_clean}%"),
                GermanLocation.name_simple.ilike(f"{q_simple}%"),
            )
        )
        .order_by(GermanLocation.population.desc())
        .limit(8)
    )
    locations = result.scalars().all()
    return [
        {
            "id": loc.id,
            "name": loc.name,
            "bundesland": loc.bundesland,
            "landkreis": loc.landkreis or "",
            "lat": loc.lat,
            "lon": loc.lon,
        }
        for loc in locations
    ]


@router.get("/bundeslaender")
async def list_bundeslaender(db: AsyncSession = Depends(get_db)):
    """Return all unique Bundesländer names (for dropdown filters)."""
    result = await db.execute(
        select(GermanLocation.bundesland)
        .distinct()
        .order_by(GermanLocation.bundesland)
    )
    return [row[0] for row in result.fetchall()]
