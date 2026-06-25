"""
Load German geodata from GeoNames into the german_locations table.

Usage:
    python scripts/load_geonames.py

The script downloads DE.zip from GeoNames (free, ~7 MB), parses the TSV,
and upserts into german_locations. Safe to re-run — uses ON CONFLICT DO UPDATE.

Environment:
    DATABASE_URL  — PostgreSQL DSN (e.g. postgresql+asyncpg://...)
                    Falls back to .env file if python-dotenv is installed.
"""
import asyncio
import csv
import io
import os
import sys
import zipfile

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# ── Config ────────────────────────────────────────────────────────────────────

GEONAMES_URL = "https://download.geonames.org/export/dump/DE.zip"

# Only keep these GeoNames feature codes (exclude tiny hamlets / administrative)
WANTED_CODES = {
    "PPLA",   # state capital (Berlin, München, Hamburg...)
    "PPLA2",  # large city
    "PPLA3",  # mid-size city
    "PPL",    # populated place
    "PPLX",   # neighbourhood / section of populated place
}

# GeoNames admin1 code → German Bundesland name
_BUNDESLAND_MAP = {
    "01": "Schleswig-Holstein",
    "02": "Hamburg",
    "03": "Niedersachsen",
    "04": "Bremen",
    "05": "Nordrhein-Westfalen",
    "06": "Hessen",
    "07": "Rheinland-Pfalz",
    "08": "Baden-Württemberg",
    "09": "Bayern",
    "10": "Saarland",
    "11": "Berlin",
    "12": "Brandenburg",
    "13": "Mecklenburg-Vorpommern",
    "14": "Sachsen",
    "15": "Sachsen-Anhalt",
    "16": "Thüringen",
}


def _admin1_name(code: str) -> str:
    return _BUNDESLAND_MAP.get(code, code or "Deutschland")


# ── ASCII transliteration for search without umlauts ─────────────────────────

_UMLAUT_MAP = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "Ä": "A", "Ö": "O", "Ü": "U", "ß": "ss"})


def _to_ascii(s: str) -> str:
    return s.translate(_UMLAUT_MAP)


# ── Database ──────────────────────────────────────────────────────────────────

def _get_db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            url = os.getenv("DATABASE_URL")
        except ImportError:
            pass
    if not url:
        print("ERROR: DATABASE_URL not set.", file=sys.stderr)
        sys.exit(1)
    # asyncpg driver required
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("Downloading GeoNames DE.zip …")
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        resp = await client.get(GEONAMES_URL)
        resp.raise_for_status()
        raw = resp.content

    print(f"Downloaded {len(raw) / 1024 / 1024:.1f} MB. Parsing …")
    rows = _parse_geonames(raw)
    print(f"Found {len(rows):,} locations. Inserting into DB …")

    engine = create_async_engine(_get_db_url(), echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        await _upsert(session, rows)
        await session.commit()

    await engine.dispose()
    print(f"Done. {len(rows):,} locations loaded.")


def _parse_geonames(raw: bytes) -> list[dict]:
    rows = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        with z.open("DE.txt") as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t")
            for cols in reader:
                if len(cols) < 15:
                    continue
                # GeoNames column layout (0-indexed):
                # 0  geonameid
                # 1  name
                # 2  asciiname
                # 4  latitude
                # 5  longitude
                # 7  feature_class
                # 8  feature_code
                # 10 country_code
                # 11 admin1_code  (Bundesland)
                # 12 admin2_code  (Landkreis)
                # 14 population
                feature_code = cols[8]
                if feature_code not in WANTED_CODES:
                    continue
                try:
                    lat = float(cols[4])
                    lon = float(cols[5])
                    pop = int(cols[14]) if cols[14] else 0
                except ValueError:
                    continue

                name = cols[1]
                rows.append({
                    "geonames_id": int(cols[0]),
                    "name": name,
                    "name_ascii": _to_ascii(cols[2] or name),
                    "bundesland": _admin1_name(cols[11]),
                    "landkreis": cols[12] or None,
                    "location_type": feature_code,
                    "population": pop,
                    "lat": lat,
                    "lon": lon,
                })
    return rows


async def _upsert(session: AsyncSession, rows: list[dict], batch_size: int = 500):
    for i in range(0, len(rows), batch_size):
        batch = rows[i: i + batch_size]
        await session.execute(
            text("""
                INSERT INTO german_locations
                    (geonames_id, name, name_ascii, bundesland, landkreis,
                     location_type, population, lat, lon)
                VALUES
                    (:geonames_id, :name, :name_ascii, :bundesland, :landkreis,
                     :location_type, :population, :lat, :lon)
                ON CONFLICT (geonames_id) DO UPDATE SET
                    name          = EXCLUDED.name,
                    name_ascii    = EXCLUDED.name_ascii,
                    bundesland    = EXCLUDED.bundesland,
                    landkreis     = EXCLUDED.landkreis,
                    location_type = EXCLUDED.location_type,
                    population    = EXCLUDED.population,
                    lat           = EXCLUDED.lat,
                    lon           = EXCLUDED.lon
            """),
            batch,
        )
        print(f"  {min(i + batch_size, len(rows)):,} / {len(rows):,}", end="\r")
    print()


if __name__ == "__main__":
    asyncio.run(main())
