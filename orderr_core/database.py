from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import os

# DATABASE_URL from environment (load_dotenv called once in main.py)
# Locally: sqlite:///orderr.db
# Render:  postgresql://orderr_db_user:...
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orderr.db")

# Render PostgreSQL URLs start with "postgres://" (old format) —
# SQLAlchemy requires "postgresql://". Fix it automatically.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# connect_args only needed for SQLite — PostgreSQL doesn't support it
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,   # auto-reconnect if connection drops
        pool_size=5,          # max 5 connections — fine for free tier
        max_overflow=2        # 2 extra connections under burst load
    )

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# SQLAlchemy 2.0 style base class
class Base(DeclarativeBase):
    pass


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
