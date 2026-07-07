"""
Staff Ledger salary logic — ported 1:1 from the standalone emp-manager
(Node/Express) app's server.js.

Pay convention: salary is paid on the 10th of each month and covers the
*previous* calendar month. An employee's first payday is the 10th of the
month after they join. Net payable = gross salary − per-day leave deduction
for the covered month − outstanding advance.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from orderr_core.models.employee import Employee
from orderr_core.models.advance import Advance
from orderr_core.models.advance_repayment import AdvanceRepayment
from orderr_core.models.leave import Leave

IST = timezone(timedelta(hours=5, minutes=30))


def today_ist() -> date:
    return datetime.now(IST).date()


def current_year_range(today: Optional[date] = None) -> tuple[str, str]:
    y = (today or today_ist()).year
    return f"{y}-01-01", f"{y}-12-31"


def _add_month_keep_day10(d: date) -> date:
    """Return the 10th of the month after `d`'s month."""
    if d.month == 12:
        return date(d.year + 1, 1, 10)
    return date(d.year, d.month + 1, 10)


def next_salary_day(now: Optional[date] = None) -> date:
    """
    The next upcoming (or today's) payday: the 10th of this month if we
    haven't passed it, otherwise the 10th of next month.
    """
    d = now or today_ist()
    candidate = date(d.year, d.month, 10)
    if d.day > 10:
        candidate = _add_month_keep_day10(candidate)
    return candidate


def first_pay_date(join_date_str: Optional[str]) -> Optional[date]:
    """First payday = the 10th of the month AFTER the join month."""
    if not join_date_str:
        return None
    try:
        jd = date.fromisoformat(join_date_str[:10])
    except ValueError:
        return None
    return _add_month_keep_day10(jd)


def covered_month_range(pay_date: date) -> tuple[date, date, int]:
    """A payday on the 10th of month M covers the whole of month M-1."""
    if pay_date.month == 1:
        cy, cm = pay_date.year - 1, 12
    else:
        cy, cm = pay_date.year, pay_date.month - 1
    days = calendar.monthrange(cy, cm)[1]
    return date(cy, cm, 1), date(cy, cm, days), days


def _leave_days(
    db: Session,
    employee_id: int,
    start: str,
    end: str,
    paid: Optional[bool] = None,
) -> float:
    """
    Sum leave days in [start, end]: full = 1, half = 0.5.
    paid=None  → all leaves; paid=False → chargeable only; paid=True → complementary only.
    """
    q = db.query(Leave.type, func.count().label("cnt")).filter(
        Leave.employee_id == employee_id,
        Leave.date >= start,
        Leave.date <= end,
    )
    if paid is not None:
        q = q.filter(Leave.paid == paid)
    total = 0.0
    for typ, cnt in q.group_by(Leave.type).all():
        total += cnt * 0.5 if typ == "half" else cnt
    return total


def _repaid_in_range(db: Session, employee_id: int, start: str, end: str) -> float:
    """Sum of advance repayments recorded in [start, end] for this employee."""
    total = (
        db.query(func.coalesce(func.sum(AdvanceRepayment.amount), 0))
        .filter(
            AdvanceRepayment.employee_id == employee_id,
            AdvanceRepayment.date >= start,
            AdvanceRepayment.date <= end,
        )
        .scalar()
    )
    return float(total or 0)


def _advance_totals(db: Session, employee_id: int) -> tuple[float, float]:
    total_adv, total_repaid = (
        db.query(
            func.coalesce(func.sum(Advance.amount), 0),
            func.coalesce(func.sum(Advance.repaid_amount), 0),
        )
        .filter(Advance.employee_id == employee_id)
        .one()
    )
    return float(total_adv), float(total_repaid)


def employee_summary(db: Session, employee: Employee) -> dict:
    """Per-employee dashboard row incl. net payable and a full breakdown."""
    start, end = current_year_range()
    next_pay = next_salary_day()

    # Chargeable (unpaid) vs complementary (paid) leave — complementary is
    # recorded and shown but never deducted from salary.
    used_leave = _leave_days(db, employee.id, start, end, paid=False)
    comp_leave = _leave_days(db, employee.id, start, end, paid=True)
    total_adv, total_repaid = _advance_totals(db, employee.id)
    outstanding = total_adv - total_repaid

    fpd = first_pay_date(employee.join_date)
    salary_due = bool(fpd) and fpd <= next_pay

    # Each payday covers the month before it, so the covered month must be
    # derived from THIS employee's own next payday (new joiners pay later).
    employee_pay_date = fpd if (fpd and fpd > next_pay) else next_pay
    cov_start, cov_end, days_in_month = covered_month_range(employee_pay_date)
    cov_start_s, cov_end_s = cov_start.isoformat(), cov_end.isoformat()

    cov_leave_days = _leave_days(db, employee.id, cov_start_s, cov_end_s, paid=False)
    cov_comp_days  = _leave_days(db, employee.id, cov_start_s, cov_end_s, paid=True)
    per_day_rate = (employee.monthly_salary / days_in_month) if days_in_month else 0
    leave_deduction = cov_leave_days * per_day_rate
    # Salary payable for the covered month is gross minus leave only. Advances
    # are NOT force-deducted in full — they're recovered by variable monthly
    # repayments the accountant records (see recovered_this_month below).
    salary_payable = max(0.0, employee.monthly_salary - leave_deduction)

    # Current-month window (today's calendar month) — used both for upcoming
    # leave info and for the advance amount recovered this pay cycle.
    today = today_ist()
    tm_start = date(today.year, today.month, 1)
    tm_end   = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    tm_charge = _leave_days(db, employee.id, tm_start.isoformat(), tm_end.isoformat(), paid=False)
    tm_comp   = _leave_days(db, employee.id, tm_start.isoformat(), tm_end.isoformat(), paid=True)
    tm_applies_on = _add_month_keep_day10(date(today.year, today.month, 10))
    # "current month leave" line only matters when it isn't the covered month.
    show_current_month = cov_end < tm_start

    # Advance recovered this pay cycle = repayments dated in the current month
    # (salaries are paid on the 10th, so the payday and its recovery share a month).
    recovered_this_month = _repaid_in_range(db, employee.id, tm_start.isoformat(), tm_end.isoformat())
    take_home = max(0.0, salary_payable - recovered_this_month)

    return {
        "id":                 employee.id,
        "name":               employee.name,
        "code":               employee.code,
        "department":         employee.department,
        "phone":              employee.phone,
        "join_date":          employee.join_date,
        "monthly_salary":     employee.monthly_salary,
        "annual_leave_quota": employee.annual_leave_quota,
        "leave_used":         used_leave,
        "complementary_used": comp_leave,
        "leave_remaining":    max(0.0, employee.annual_leave_quota - used_leave),
        "outstanding_advance": outstanding,
        "next_pay_date":      employee_pay_date.isoformat(),
        "first_pay_date":     fpd.isoformat() if fpd else None,
        "salary_due":         salary_due,
        "leave_deduction":    leave_deduction,
        "pay_amount":         salary_payable,
        "recovered_this_month": recovered_this_month,
        "take_home":            take_home,
        "current_month_label":              today.strftime("%B"),
        "current_month_leave_days":         tm_charge,
        "current_month_complementary_days": tm_comp,
        "current_month_applies_on":         tm_applies_on.isoformat(),
        "show_current_month":               show_current_month,
        "breakdown": {
            "gross_salary":             employee.monthly_salary,
            "covered_period_start":     cov_start_s,
            "covered_period_end":       cov_end_s,
            "days_in_covered_month":    days_in_month,
            "per_day_rate":             per_day_rate,
            "leave_days_deducted":      cov_leave_days,
            "complementary_leave_days": cov_comp_days,
            "leave_deduction_amount":   leave_deduction,
            "salary_payable":           salary_payable,
            "advance_outstanding":      outstanding,
            "recovered_this_month":     recovered_this_month,
            "take_home":                take_home,
            "net_payable":              salary_payable,
        },
    }


def single_summary(db: Session, employee: Employee) -> dict:
    """The GET /summary/{id} shape (year-to-date leave + advance totals)."""
    start, end = current_year_range()
    used_leave = _leave_days(db, employee.id, start, end, paid=False)
    comp_leave = _leave_days(db, employee.id, start, end, paid=True)
    total_adv, total_repaid = _advance_totals(db, employee.id)
    return {
        "employee": {
            "id": employee.id, "name": employee.name, "code": employee.code,
            "department": employee.department, "phone": employee.phone,
            "join_date": employee.join_date, "monthly_salary": employee.monthly_salary,
            "annual_leave_quota": employee.annual_leave_quota,
        },
        "leave_quota":         employee.annual_leave_quota,
        "leave_used":          used_leave,
        "complementary_used":  comp_leave,
        "leave_remaining":     max(0.0, employee.annual_leave_quota - used_leave),
        "total_advances":      total_adv,
        "total_repaid":        total_repaid,
        "outstanding_advance": total_adv - total_repaid,
    }
