from sqlalchemy import Column, Float, Index, Integer, String

from app.database import Base


class GermanLocation(Base):
    __tablename__ = "german_locations"

    id = Column(Integer, primary_key=True)
    geonames_id = Column(Integer, unique=True, nullable=True)
    name = Column(String(200), nullable=False)
    name_ascii = Column(String(200), nullable=True)
    bundesland = Column(String(100), nullable=False)
    landkreis = Column(String(200), nullable=True)
    location_type = Column(String(10), server_default="PPL")
    population = Column(Integer, server_default="0")
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)

    __table_args__ = (
        Index("ix_german_loc_name_ascii", "name_ascii"),
        Index("ix_german_loc_lat_lon", "lat", "lon"),
        Index("ix_german_loc_bundesland", "bundesland"),
    )
