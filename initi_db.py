# init_db.py  — run once to create all tables from scratch
import os
os.environ.setdefault("DATABASE_URL", "sqlite:///orderr.db")

# ── Patch JSONB → JSON for SQLite compatibility ────────────────────────────────
import sqlalchemy.dialects.postgresql as pg
from sqlalchemy import JSON
pg.JSONB = JSON
pg.base.JSONB = JSON
# ─────────────────────────────────────────────────────────────────────────────

from app.database import engine, Base

# Import every model so Base knows about all tables
from app.models.order import Order
from app.models.customer import Customer
from app.models.salesperson import Salesperson
from app.models.invoice import Invoice, CustomerProductPrice, DefaultProductPrice, ProductItemCode
from app.models.inbound_message import InboundMessage
from app.models.customer_product_alias import CustomerProductAlias
from app.models.unclear_item_alias import UnclearItemAlias
from app.models.noise_phrase import NoisePhrase

Base.metadata.create_all(bind=engine)
print("✅ All tables created successfully.")

# Print what was created
from sqlalchemy import inspect
inspector = inspect(engine)
for table in inspector.get_table_names():
    cols = [c["name"] for c in inspector.get_columns(table)]
    print(f"  📋 {table}: {', '.join(cols)}")