"""
admin.py
--------
Admin API routes — all protected by Basic Auth.

Salesperson management:
  GET    /admin/salespersons                 list all
  POST   /admin/salespersons                 create
  PUT    /admin/salespersons/{id}            update
  DELETE /admin/salespersons/{id}            deactivate (soft delete)

Customer management:
  GET    /admin/customers                    list all with assignment status
  GET    /admin/customers/unassigned         customers without salesperson
  POST   /admin/customers/{id}/assign        set area + salesperson
  PUT    /admin/customers/{id}/status        set is_active / is_daily_order_customer

Pending orders:
  GET    /admin/pending                      current pending customers (manual check)
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import get_db
from app.models.customer import Customer
from app.models.salesperson import Salesperson
from app.services.customer_service import normalize_phone
from app.services.pending_orders import get_pending_customers, get_delivery_date_for_now

router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class SalespersonCreate(BaseModel):
    name: str
    phone: str


class SalespersonUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    active: Optional[bool] = None


class CustomerAssign(BaseModel):
    area: Optional[str] = None
    salesperson_id: Optional[int] = None


class CustomerStatus(BaseModel):
    is_active: Optional[bool] = None
    is_daily_order_customer: Optional[bool] = None


# ── Salesperson routes ────────────────────────────────────────────────────────

@router.get("/salespersons")
def list_salespersons(
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """List all salespersons with their customer count."""

    salespersons = db.query(Salesperson).order_by(Salesperson.name).all()

    result = []
    for sp in salespersons:
        customer_count = (
            db.query(Customer)
            .filter(Customer.salesperson_id == sp.id)
            .count()
        )
        result.append({
            "id": sp.id,
            "name": sp.name,
            "phone": sp.phone,
            "active": sp.active,
            "customer_count": customer_count,
            "created_at": sp.created_at.isoformat() if sp.created_at else None
        })

    return {"salespersons": result, "total": len(result)}


@router.post("/salespersons")
def create_salesperson(
    payload: SalespersonCreate,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """Create a new salesperson."""

    normalized = normalize_phone(payload.phone)

    # Check duplicate
    existing = db.query(Salesperson).filter(
        Salesperson.phone == normalized
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Salesperson with phone {normalized} already exists"
        )

    sp = Salesperson(
        name=payload.name.strip(),
        phone=normalized,
        active=True
    )

    db.add(sp)
    db.commit()
    db.refresh(sp)

    return {
        "status": "created",
        "salesperson": {
            "id": sp.id,
            "name": sp.name,
            "phone": sp.phone,
            "active": sp.active
        }
    }


@router.put("/salespersons/{salesperson_id}")
def update_salesperson(
    salesperson_id: int,
    payload: SalespersonUpdate,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """Update salesperson name, phone, or active status."""

    sp = db.query(Salesperson).filter(Salesperson.id == salesperson_id).first()

    if not sp:
        raise HTTPException(status_code=404, detail="Salesperson not found")

    if payload.name is not None:
        sp.name = payload.name.strip()

    if payload.phone is not None:
        sp.phone = normalize_phone(payload.phone)

    if payload.active is not None:
        sp.active = payload.active

    db.commit()
    db.refresh(sp)

    return {
        "status": "updated",
        "salesperson": {
            "id": sp.id,
            "name": sp.name,
            "phone": sp.phone,
            "active": sp.active
        }
    }


@router.delete("/salespersons/{salesperson_id}")
def deactivate_salesperson(
    salesperson_id: int,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """
    Soft-delete: marks salesperson as inactive.
    Their customers remain assigned but won't receive notifications
    until reassigned to an active salesperson.
    """

    sp = db.query(Salesperson).filter(Salesperson.id == salesperson_id).first()

    if not sp:
        raise HTTPException(status_code=404, detail="Salesperson not found")

    sp.active = False
    db.commit()

    # Count affected customers
    affected = db.query(Customer).filter(
        Customer.salesperson_id == salesperson_id
    ).count()

    return {
        "status": "deactivated",
        "salesperson_id": salesperson_id,
        "affected_customers": affected,
        "note": "Customers remain assigned. Reassign them to receive notifications."
    }


# ── Customer routes ───────────────────────────────────────────────────────────

@router.get("/customers")
def list_customers(
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """List all onboarded customers with their assignment status."""

    customers = (
        db.query(Customer)
        .filter(Customer.onboarding_status == "active")
        .order_by(Customer.restaurant_name)
        .all()
    )

    result = []
    for c in customers:
        sp_name = None
        if c.salesperson_id:
            sp = db.query(Salesperson).filter(Salesperson.id == c.salesperson_id).first()
            sp_name = sp.name if sp else None

        result.append({
            "id": c.id,
            "restaurant_name": c.restaurant_name,
            "phone_number": c.phone_number,
            "area": c.area,
            "salesperson_id": c.salesperson_id,
            "salesperson_name": sp_name,
            "is_active": c.is_active,
            "is_daily_order_customer": c.is_daily_order_customer,
            "created_at": c.created_at.isoformat() if c.created_at else None
        })

    return {"customers": result, "total": len(result)}


@router.get("/customers/unassigned")
def list_unassigned_customers(
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """
    Returns onboarded customers that have no salesperson assigned.
    Use this after new customer onboardings to assign them.
    """

    customers = (
        db.query(Customer)
        .filter(
            Customer.onboarding_status == "active",
            Customer.salesperson_id == None
        )
        .order_by(Customer.created_at.desc())
        .all()
    )

    result = [
        {
            "id": c.id,
            "restaurant_name": c.restaurant_name,
            "phone_number": c.phone_number,
            "area": c.area,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat() if c.created_at else None
        }
        for c in customers
    ]

    return {"customers": result, "total": len(result)}


@router.post("/customers/{customer_id}/assign")
def assign_customer(
    customer_id: int,
    payload: CustomerAssign,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """
    Assign area and/or salesperson to a customer.
    Can be called multiple times to update the assignment.
    """

    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if payload.area is not None:
        customer.area = payload.area.strip()

    if payload.salesperson_id is not None:
        # Validate salesperson exists
        sp = db.query(Salesperson).filter(
            Salesperson.id == payload.salesperson_id
        ).first()

        if not sp:
            raise HTTPException(
                status_code=404,
                detail=f"Salesperson id={payload.salesperson_id} not found"
            )

        customer.salesperson_id = payload.salesperson_id

    db.commit()
    db.refresh(customer)

    sp_name = None
    if customer.salesperson_id:
        sp = db.query(Salesperson).filter(
            Salesperson.id == customer.salesperson_id
        ).first()
        sp_name = sp.name if sp else None

    return {
        "status": "assigned",
        "customer": {
            "id": customer.id,
            "restaurant_name": customer.restaurant_name,
            "area": customer.area,
            "salesperson_id": customer.salesperson_id,
            "salesperson_name": sp_name
        }
    }


@router.put("/customers/{customer_id}/status")
def update_customer_status(
    customer_id: int,
    payload: CustomerStatus,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """
    Update a customer's active/notification status.

    is_active = false          → customer switched to another vendor;
                                 stops ALL reminders and notifications
    is_daily_order_customer    → customer orders irregularly;
    = false                      stops daily pending checks but keeps them active
    """

    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if payload.is_active is not None:
        customer.is_active = payload.is_active

    if payload.is_daily_order_customer is not None:
        customer.is_daily_order_customer = payload.is_daily_order_customer

    db.commit()
    db.refresh(customer)

    return {
        "status": "updated",
        "customer": {
            "id": customer.id,
            "restaurant_name": customer.restaurant_name,
            "phone_number": customer.phone_number,
            "is_active": customer.is_active,
            "is_daily_order_customer": customer.is_daily_order_customer
        }
    }


# ── Pending orders (manual check) ────────────────────────────────────────────

@router.get("/pending")
def get_pending_now(
    delivery_date: Optional[str] = None,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth)
):
    """
    Returns current pending customers grouped by salesperson.
    Optional query param: delivery_date=YYYY-MM-DD
    Defaults to today's delivery date based on current IST time.
    """

    if delivery_date:
        try:
            target_date = date.fromisoformat(delivery_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use YYYY-MM-DD"
            )
    else:
        target_date = get_delivery_date_for_now()

    grouped = get_pending_customers(db, target_date)

    result = []
    total_pending = 0

    for sp_id, customers in grouped.items():
        sp_name = "Unassigned"
        sp_phone = None

        if sp_id is not None:
            sp = db.query(Salesperson).filter(Salesperson.id == sp_id).first()
            sp_name = sp.name if sp else f"Unknown (id={sp_id})"
            sp_phone = sp.phone if sp else None

        result.append({
            "salesperson_id": sp_id,
            "salesperson_name": sp_name,
            "salesperson_phone": sp_phone,
            "pending_count": len(customers),
            "customers": [
                {
                    "id": c.id,
                    "restaurant_name": c.restaurant_name,
                    "phone_number": c.phone_number,
                    "area": c.area
                }
                for c in customers
            ]
        })

        total_pending += len(customers)

    return {
        "delivery_date": target_date.isoformat(),
        "total_pending": total_pending,
        "groups": result
    }
