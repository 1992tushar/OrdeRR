"""
Staff Ledger routes — salary advance / leave / half-day management.
Ported from the standalone emp-manager (Node/Express) app.

Absolute paths: the page at /staff, JSON API under /staff/api/*.
All endpoints require the same HTTP Basic auth as the main dashboard.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from orderr_core.database import get_db
from orderr_core.auth import require_auth
from orderr_core.models.employee import Employee
from orderr_core.models.advance import Advance
from orderr_core.models.advance_repayment import AdvanceRepayment
from orderr_core.models.leave import Leave
from orderr_core.services import staff_ledger

router = APIRouter()
templates = Jinja2Templates(directory="orderr_core/templates")


def _emp(e: Employee) -> dict:
    return {
        "id": e.id, "name": e.name, "code": e.code, "department": e.department,
        "phone": e.phone, "join_date": e.join_date, "monthly_salary": e.monthly_salary,
        "annual_leave_quota": e.annual_leave_quota, "active": e.active,
    }


def _adv(a: Advance, employee_name: Optional[str] = None, repayments: Optional[list] = None) -> dict:
    d = {
        "id": a.id, "employee_id": a.employee_id, "date": a.date, "amount": a.amount,
        "reason": a.reason, "repaid_amount": a.repaid_amount, "notes": a.notes,
        "repayments": repayments or [],
    }
    if employee_name is not None:
        d["employee_name"] = employee_name
    return d


def _lv(l: Leave, employee_name: Optional[str] = None) -> dict:
    d = {"id": l.id, "employee_id": l.employee_id, "date": l.date,
         "type": l.type, "paid": bool(l.paid), "reason": l.reason}
    if employee_name is not None:
        d["employee_name"] = employee_name
    return d


class EmployeeIn(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    department: Optional[str] = None
    phone: Optional[str] = None
    join_date: Optional[str] = None
    monthly_salary: Optional[float] = 0
    annual_leave_quota: Optional[float] = 12


class AdvanceIn(BaseModel):
    employee_id: Optional[int] = None
    date: Optional[str] = None
    amount: Optional[float] = None
    reason: Optional[str] = None
    notes: Optional[str] = None


class RepayIn(BaseModel):
    amount: Optional[float] = None
    date: Optional[str] = None   # recovery date; defaults to today (IST)


class LeaveIn(BaseModel):
    employee_id: Optional[int] = None
    date: Optional[str] = None
    type: Optional[str] = None
    paid: Optional[bool] = False   # complementary leave — no salary deduction
    reason: Optional[str] = None


@router.get("/staff", response_class=HTMLResponse)
def staff_page(request: Request, username: str = Depends(require_auth)):
    return templates.TemplateResponse(request, "staff.html", {})


@router.get("/staff/api/employees")
def list_employees(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    rows = db.query(Employee).filter(Employee.active == True).order_by(Employee.name).all()  # noqa: E712
    return [_emp(e) for e in rows]


@router.get("/staff/api/employees/{emp_id}")
def get_employee(emp_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(status_code=404, detail="Employee not found")
    return _emp(e)


@router.post("/staff/api/employees", status_code=201)
def create_employee(body: EmployeeIn, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    if not body.name:
        raise HTTPException(status_code=400, detail="Name is required")
    e = Employee(
        name=body.name, code=body.code or None, department=body.department or None,
        phone=body.phone or None, join_date=body.join_date or None,
        monthly_salary=body.monthly_salary or 0,
        annual_leave_quota=body.annual_leave_quota if body.annual_leave_quota is not None else 12,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return _emp(e)


@router.put("/staff/api/employees/{emp_id}")
def update_employee(emp_id: int, body: EmployeeIn, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(status_code=404, detail="Employee not found")
    e.name = body.name
    e.code = body.code or None
    e.department = body.department or None
    e.phone = body.phone or None
    e.join_date = body.join_date or None
    e.monthly_salary = body.monthly_salary or 0
    e.annual_leave_quota = body.annual_leave_quota if body.annual_leave_quota is not None else 12
    db.commit()
    db.refresh(e)
    return _emp(e)


@router.delete("/staff/api/employees/{emp_id}")
def delete_employee(emp_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    e = db.get(Employee, emp_id)
    if e:
        e.active = False
        db.commit()
    return {"ok": True}


@router.get("/staff/api/advances")
def list_advances(employee_id: Optional[int] = None, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    q = db.query(Advance, Employee.name).join(Employee, Employee.id == Advance.employee_id)
    if employee_id:
        q = q.filter(Advance.employee_id == employee_id)
    rows = q.order_by(Advance.date.desc()).all()

    # Batch-load repayment history for these advances (one query, no N+1).
    reps_by_adv: dict[int, list] = {}
    adv_ids = [a.id for a, _ in rows]
    if adv_ids:
        reps = (
            db.query(AdvanceRepayment)
            .filter(AdvanceRepayment.advance_id.in_(adv_ids))
            .order_by(AdvanceRepayment.date.desc(), AdvanceRepayment.id.desc())
            .all()
        )
        for r in reps:
            reps_by_adv.setdefault(r.advance_id, []).append({"date": r.date, "amount": r.amount})

    return [_adv(a, name, reps_by_adv.get(a.id, [])) for a, name in rows]


@router.post("/staff/api/advances", status_code=201)
def create_advance(body: AdvanceIn, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    if not body.employee_id or not body.date or not body.amount:
        raise HTTPException(status_code=400, detail="employee_id, date and amount are required")
    a = Advance(employee_id=body.employee_id, date=body.date, amount=body.amount,
                reason=body.reason or None, notes=body.notes or None)
    db.add(a)
    db.commit()
    db.refresh(a)
    return _adv(a)


@router.post("/staff/api/advances/{adv_id}/repay")
def repay_advance(adv_id: int, body: RepayIn, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    if not body.amount or body.amount <= 0:
        raise HTTPException(status_code=400, detail="A positive amount is required")
    a = db.get(Advance, adv_id)
    if not a:
        raise HTTPException(status_code=404, detail="Advance not found")

    outstanding = a.amount - a.repaid_amount
    if outstanding <= 0:
        raise HTTPException(status_code=400, detail="This advance is already fully repaid")

    # Apply at most the outstanding balance; record the actual amount applied.
    applied = min(float(body.amount), outstanding)
    rep_date = (body.date or staff_ledger.today_ist().isoformat())[:10]

    a.repaid_amount = a.repaid_amount + applied
    db.add(AdvanceRepayment(
        advance_id=a.id, employee_id=a.employee_id, date=rep_date, amount=applied,
    ))
    db.commit()
    db.refresh(a)

    reps = (
        db.query(AdvanceRepayment)
        .filter(AdvanceRepayment.advance_id == a.id)
        .order_by(AdvanceRepayment.date.desc(), AdvanceRepayment.id.desc())
        .all()
    )
    return _adv(a, repayments=[{"date": r.date, "amount": r.amount} for r in reps])


@router.delete("/staff/api/advances/{adv_id}")
def delete_advance(adv_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    a = db.get(Advance, adv_id)
    if a:
        # Remove the repayment history first (FK references advances.id).
        db.query(AdvanceRepayment).filter(AdvanceRepayment.advance_id == adv_id).delete()
        db.delete(a)
        db.commit()
    return {"ok": True}


@router.get("/staff/api/leaves")
def list_leaves(employee_id: Optional[int] = None, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    q = db.query(Leave, Employee.name).join(Employee, Employee.id == Leave.employee_id)
    if employee_id:
        q = q.filter(Leave.employee_id == employee_id)
    return [_lv(l, name) for l, name in q.order_by(Leave.date.desc()).all()]


@router.post("/staff/api/leaves", status_code=201)
def create_leave(body: LeaveIn, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    if not body.employee_id or not body.date or not body.type:
        raise HTTPException(status_code=400, detail="employee_id, date and type are required")
    if body.type not in ("full", "half"):
        raise HTTPException(status_code=400, detail="type must be 'full' or 'half'")
    l = Leave(employee_id=body.employee_id, date=body.date, type=body.type,
              paid=bool(body.paid), reason=body.reason or None)
    db.add(l)
    db.commit()
    db.refresh(l)
    return _lv(l)


@router.delete("/staff/api/leaves/{leave_id}")
def delete_leave(leave_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    l = db.get(Leave, leave_id)
    if l:
        db.delete(l)
        db.commit()
    return {"ok": True}


@router.get("/staff/api/summary/{emp_id}")
def summary(emp_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(status_code=404, detail="Employee not found")
    return staff_ledger.single_summary(db, e)


@router.get("/staff/api/dashboard")
def dashboard(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    employees = db.query(Employee).filter(Employee.active == True).order_by(Employee.name).all()  # noqa: E712
    return [staff_ledger.employee_summary(db, e) for e in employees]
