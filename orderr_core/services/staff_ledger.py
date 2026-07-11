"""
Staff Ledger salary logic — ported 1:1 from the standalone emp-manager
(Node/Express) app's server.js.

Pay convention: the company holds ~10 days of salary as security, so each
employee is paid 10 days after their monthly joining-day cycle closes. The pay
day is therefore (joining day + 10), and the first payday is that day in the
month AFTER they join (joined 10 Jun → first pay 20 Jul; joined 11 May → 21 Jun).
A payday pays for the joining-day cycle it closes (20 Jul pays for 10 Jun–9 Jul).
Net payable = gross salary − per-day leave deduction for that cycle − advance
recovery.
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
from orderr_core.models.late_mark import LateMark

from orderr_core import config
from orderr_core.constants import IST


def today_ist() -> date:
    return datetime.now(IST).date()


def current_year_range(today: Optional[date] = None) -> tuple[str, str]:
    y = (today or today_ist()).year
    return f"{y}-01-01", f"{y}-12-31"


SECURITY_HOLD_DAYS = 10   # salary held ~10 days as security → paid this many
                          # days after each joining-day cycle closes.


def _months_after(y: int, m: int, n: int) -> tuple[int, int]:
    """(year, month) that is `n` months after (y, m). `n` may be negative."""
    idx = (m - 1) + n
    return y + idx // 12, idx % 12 + 1


def _anchor(y: int, m: int, join_day: int) -> date:
    """The employee's joining-day within month (y, m), clamped to the month's
    length (join day 31 → 30 Apr / 28 Feb)."""
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(join_day, last))


def _payday_for_anchor(anchor: date) -> date:
    """A cycle that closes on `anchor` is paid SECURITY_HOLD_DAYS later."""
    return anchor + timedelta(days=SECURITY_HOLD_DAYS)


def first_pay_date(join_date_str: Optional[str]) -> Optional[date]:
    """First payday = (joining day + 10 days) in the month AFTER the join month.
    e.g. joined 10 Jun → 20 Jul; joined 11 May → 21 Jun."""
    if not join_date_str:
        return None
    try:
        jd = date.fromisoformat(join_date_str[:10])
    except ValueError:
        return None
    ay, am = _months_after(jd.year, jd.month, 1)
    return _payday_for_anchor(_anchor(ay, am, jd.day))


def pay_cycle(join_date_str: str, on_or_after: date) -> tuple[date, date, date, int]:
    """
    The employee's next payday on/after `on_or_after` (never before their first
    payday), and the joining-day cycle that payday pays for.

    Returns (pay_date, covered_start, covered_end, covered_days).

    Paydays sit SECURITY_HOLD_DAYS after each joining-day anchor. The payday that
    closes on anchor(M) pays for the cycle [anchor(M-1), anchor(M) − 1 day].
    """
    jd = date.fromisoformat(join_date_str[:10])
    target = max(on_or_after, first_pay_date(join_date_str))

    ay, am = _months_after(jd.year, jd.month, 1)   # first payable anchor month
    anchor = _anchor(ay, am, jd.day)
    for _ in range(1200):                           # bounded; a few iters in practice
        pay = _payday_for_anchor(anchor)
        if pay >= target:
            py, pm = _months_after(ay, am, -1)
            prev = _anchor(py, pm, jd.day)
            return pay, prev, anchor - timedelta(days=1), (anchor - prev).days
        ay, am = _months_after(ay, am, 1)
        anchor = _anchor(ay, am, jd.day)
    # Unreachable for realistic dates; keeps the return type total.
    py, pm = _months_after(ay, am, -1)
    prev = _anchor(py, pm, jd.day)
    return _payday_for_anchor(anchor), prev, anchor - timedelta(days=1), (anchor - prev).days


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


def _late_marks(db: Session, employee_id: int, start: str, end: str) -> int:
    """Count late marks in [start, end] for this employee. Each one levies a flat
    fine (config.LATE_MARK_FINE), unlike leaves which deduct a per-day rate."""
    return int(
        db.query(func.count(LateMark.id))
        .filter(
            LateMark.employee_id == employee_id,
            LateMark.date >= start,
            LateMark.date <= end,
        )
        .scalar()
        or 0
    )


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
    today = today_ist()

    # Chargeable (unpaid) vs complementary (paid) leave — complementary is
    # recorded and shown but never deducted from salary.
    used_leave = _leave_days(db, employee.id, start, end, paid=False)
    comp_leave = _leave_days(db, employee.id, start, end, paid=True)
    used_late  = _late_marks(db, employee.id, start, end)   # YTD late-mark count
    total_adv, total_repaid = _advance_totals(db, employee.id)
    outstanding = total_adv - total_repaid

    # Pay date = joining day + 10 days, on the employee's own joining-day cycle.
    fpd = first_pay_date(employee.join_date)
    if fpd:
        employee_pay_date, cov_start, cov_end, days_in_month = pay_cycle(
            employee.join_date, today
        )
        salary_due = today >= fpd   # owed once the first payday has arrived
    else:
        # No / invalid joining date — can't schedule a payday. Fall back to the
        # current calendar month for the breakdown and mark it not due.
        employee_pay_date = None
        cov_start = date(today.year, today.month, 1)
        cov_end   = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        salary_due = False

    cov_start_s, cov_end_s = cov_start.isoformat(), cov_end.isoformat()

    cov_leave_days = _leave_days(db, employee.id, cov_start_s, cov_end_s, paid=False)
    cov_comp_days  = _leave_days(db, employee.id, cov_start_s, cov_end_s, paid=True)
    cov_late_marks = _late_marks(db, employee.id, cov_start_s, cov_end_s)
    per_day_rate = (employee.monthly_salary / days_in_month) if days_in_month else 0
    leave_deduction = cov_leave_days * per_day_rate
    # Each late mark is a flat fine (not a per-day deduction).
    late_fine = cov_late_marks * config.LATE_MARK_FINE
    # Salary payable for the covered cycle is gross minus leave and late-mark
    # fines. Advances are NOT force-deducted in full — they're recovered by
    # variable monthly repayments the accountant records (recovered_this_month).
    salary_payable = max(0.0, employee.monthly_salary - leave_deduction - late_fine)

    # Leave accruing in the CURRENT cycle (the one after the covered cycle) that
    # will land on a future payslip — only shown once we're past cov_end.
    if employee_pay_date and today > cov_end:
        nxt_pay, accr_start, accr_end, _ = pay_cycle(
            employee.join_date, employee_pay_date + timedelta(days=1)
        )
        win_end = min(today, accr_end)
        tm_charge = _leave_days(db, employee.id, accr_start.isoformat(), win_end.isoformat(), paid=False)
        tm_comp   = _leave_days(db, employee.id, accr_start.isoformat(), win_end.isoformat(), paid=True)
        tm_late   = _late_marks(db, employee.id, accr_start.isoformat(), win_end.isoformat())
        tm_applies_on = nxt_pay
        current_month_label = f"{accr_start.strftime('%d %b')} – {accr_end.strftime('%d %b')}"
        show_current_month = True
    else:
        tm_charge = tm_comp = 0.0
        tm_late = 0
        tm_applies_on = employee_pay_date or today
        current_month_label = today.strftime("%B")
        show_current_month = False

    # Advance recovered this pay cycle = repayments dated in the current calendar
    # month (the payday and the accountant's recovery entry share a month).
    cm_start = date(today.year, today.month, 1)
    cm_end   = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    recovered_this_month = _repaid_in_range(db, employee.id, cm_start.isoformat(), cm_end.isoformat())
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
        "late_marks_used":    used_late,
        "outstanding_advance": outstanding,
        "next_pay_date":      employee_pay_date.isoformat() if employee_pay_date else None,
        "first_pay_date":     fpd.isoformat() if fpd else None,
        "salary_due":         salary_due,
        "leave_deduction":    leave_deduction,
        "late_fine":          late_fine,
        "pay_amount":         salary_payable,
        "recovered_this_month": recovered_this_month,
        "take_home":            take_home,
        "current_month_label":              current_month_label,
        "current_month_leave_days":         tm_charge,
        "current_month_complementary_days": tm_comp,
        "current_month_late_marks":         tm_late,
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
            "late_marks_deducted":      cov_late_marks,
            "per_late_fine":            config.LATE_MARK_FINE,
            "late_fine_amount":         late_fine,
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
    used_late  = _late_marks(db, employee.id, start, end)
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
        "late_marks_used":     used_late,
        "late_fine_total":     used_late * config.LATE_MARK_FINE,
        "total_advances":      total_adv,
        "total_repaid":        total_repaid,
        "outstanding_advance": total_adv - total_repaid,
    }
