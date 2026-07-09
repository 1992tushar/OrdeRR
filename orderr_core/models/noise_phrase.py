from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, DateTime
from orderr_core.database import Base

from orderr_core.constants import IST


class NoisePhrase(Base):
    __tablename__ = "noise_phrases"

    id         = Column(Integer, primary_key=True, index=True)
    raw_text   = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(IST))

    def __repr__(self):
        return f"<NoisePhrase raw_text={self.raw_text!r}>"

