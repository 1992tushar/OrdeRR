from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, DateTime
from app.database import Base

IST = timezone(timedelta(hours=5, minutes=30))


class NoisePhrase(Base):
    __tablename__ = "noise_phrases"

    id         = Column(Integer, primary_key=True, index=True)
    raw_text   = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(IST))

    def __repr__(self):
        return f"<NoisePhrase raw_text={self.raw_text!r}>"
