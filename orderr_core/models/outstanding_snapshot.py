"""
outstanding_snapshots — read-only daily mirror of a party's AR balance in Vasy.

One row per party per snapshot_date, so balances accrue a history (the scalar
`customer.outstanding` has none). Vasy = source of truth.

Modelled to the REAL outstanding export columns (2026-07-10):
    # · Party Name · Contact No. · Location · Opening Balance · Debit · Credit · Closing
`closing` is the current receivable (what `customer.outstanding` is refreshed
to). Opening/Debit/Credit are kept so period movement is available without
diffing two snapshots. `party_key` is the normalized name — the upsert key
(with snapshot_date) so a same-day re-import updates rather than duplicates,
and unmatched parties (customer_id NULL) are still deduped.
"""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Numeric, Date, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class OutstandingSnapshot(Base):
    __tablename__ = "outstanding_snapshots"
    __table_args__ = (
        UniqueConstraint("party_key", "snapshot_date", name="uq_outstanding_party_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    customer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("customers.id"), nullable=True, index=True       # NULL = unmatched party
    )
    party_name: Mapped[str] = mapped_column(String, nullable=False)           # raw from Vasy
    party_key: Mapped[str] = mapped_column(String, nullable=False, index=True)  # normalized name
    contact_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # raw phone from file (may be blank)
    opening_balance: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    debit: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    credit: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    closing: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)  # current AR balance
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
