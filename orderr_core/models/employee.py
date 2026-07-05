"""
Employee model — ported from the standalone emp-manager (Staff Ledger) app.
Owns the `employees` table; shares OrdeRR's Base/metadata.
"""
from sqlalchemy import Integer, String, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from orderr_core.database import Base


class Employee(Base):
    __tablename__ = "employees"

    id:                 Mapped[int]   = mapped_column(Integer, primary_key=True)
    name:               Mapped[str]   = mapped_column(String, nullable=False)
    code:               Mapped[str]   = mapped_column(String, nullable=True)
    department:         Mapped[str]   = mapped_column(String, nullable=True)
    phone:              Mapped[str]   = mapped_column(String, nullable=True)
    # Stored as an ISO 'YYYY-MM-DD' string to mirror the original app's
    # lexicographic BETWEEN date comparisons exactly.
    join_date:          Mapped[str]   = mapped_column(String, nullable=True)
    monthly_salary:     Mapped[float] = mapped_column(Float, default=0, nullable=False)
    annual_leave_quota: Mapped[float] = mapped_column(Float, default=12, nullable=False)
    # Soft-delete flag so historical advances/leaves stay intact.
    active:             Mapped[bool]  = mapped_column(Boolean, default=True, nullable=False)
