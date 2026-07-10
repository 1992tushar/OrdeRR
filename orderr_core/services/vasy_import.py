"""
Vasy → OrdeRR import framework (Phase 2).

Vasy ERP is the source of truth for money; these importers mirror its Excel
exports into read-only OrdeRR tables (CustomerReceipt, OutstandingSnapshot) and
record an ImportLog per run. Idempotent: re-importing the same/overlapping file
upserts rather than duplicates.

Join to OrdeRR customers is phone-first (last-10 digits, where the export has a
phone) then normalized name — matching the reality confirmed against the real
2026-07-10 exports. Unmatched rows are recorded with customer_id = NULL
(unattributed bucket), never dropped.

Reuses: customer_service._to_amount / normalize_phone, and
customer_service.import_customers_from_xlsx for the customer/outstanding upsert
(so there is ONE customer-upsert code path).
"""
import io
import re
from datetime import date, datetime

import openpyxl
from sqlalchemy.orm import Session

from orderr_core.models.customer import Customer
from orderr_core.models.customer_receipt import CustomerReceipt
from orderr_core.models.outstanding_snapshot import OutstandingSnapshot
from orderr_core.models.import_log import ImportLog
from orderr_core.services.customer_service import (
    _to_amount, normalize_phone, import_customers_from_xlsx,
)


# ── shared helpers ──────────────────────────────────────────────────────────

def normalize_name(s) -> str:
    """Party-name join key: uppercase, strip every non-alphanumeric char.
    Robust to spacing / punctuation / case drift between exports."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _phone_last10(s) -> str:
    """Last 10 digits of a phone string ('91-9850410033' → '9850410033')."""
    digits = re.sub(r"\D", "", str(s or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def _parse_date(v):
    """Parse a Vasy date cell: 'DD/MM/YYYY' string or a real date/datetime."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _find_header(ws, required_labels, max_scan=15):
    """Locate the header row and map wanted columns by label (resilient to
    column re-ordering / title rows). `required_labels` maps a logical key to a
    list of accepted lowercased header texts. Returns (header_row_index_0based,
    {key: col_idx}). Raises ValueError if a required column is missing."""
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=max_scan, values_only=True)):
        labels = [str(c).strip().lower() if c is not None else "" for c in row]
        colmap = {}
        for key, accepted in required_labels.items():
            for i, lbl in enumerate(labels):
                if lbl in accepted:
                    colmap[key] = i
                    break
        if "receipt_no" in colmap or ("closing" in colmap and "party" in colmap):
            # header row found (heuristic: a distinctive column is present)
            return r_idx, colmap
    raise ValueError("Could not locate the header row in the export.")


def _build_customer_lookup(db: Session):
    """Return (by_phone, by_name) dicts → customer_id, from the customers table."""
    by_phone, by_name = {}, {}
    for c in db.query(Customer.id, Customer.phone_number, Customer.restaurant_name).all():
        if c.phone_number:
            p = _phone_last10(c.phone_number)
            if p:
                by_phone.setdefault(p, c.id)
        if c.restaurant_name:
            by_name.setdefault(normalize_name(c.restaurant_name), c.id)
    return by_phone, by_name


# ── P2-2 receipts import ────────────────────────────────────────────────────

def import_receipts(db: Session, file_bytes: bytes, source_file: str = None) -> dict:
    """Import a Vasy receipt export into CustomerReceipt.

    Upsert on Receipt No.; join party → customer by normalized name (receipts
    carry no phone). Unmatched → customer_id NULL (unattributed). Idempotent.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception as e:
        raise ValueError(f"Could not read the receipt Excel file: {e}")
    ws = wb.active

    labels = {
        "receipt_no": ("receipt no.", "receipt no", "receipt number", "receipt"),
        "party": ("party name", "party", "name", "customer name"),
        "mode": ("mode", "payment mode"),
        "date": ("date", "receipt date"),
        "amount": ("amount", "amount (inr)", "received amount"),
        "status": ("status",),
        "created_by": ("created by",),
    }
    header_idx, col = _find_header(ws, labels)
    if "receipt_no" not in col or "party" not in col:
        raise ValueError("Receipt export must have 'Receipt No.' and 'Party Name' columns.")

    _, by_name = _build_customer_lookup(db)

    # preload existing receipts for idempotent upsert
    existing = {r.receipt_no: r for r in db.query(CustomerReceipt).all()}

    created = updated = unmatched = 0
    seen = set()

    def cell(row, key):
        i = col.get(key)
        return row[i] if (i is not None and i < len(row)) else None

    for row in ws.iter_rows(min_row=header_idx + 2, values_only=True):
        if not row:
            continue
        rno = cell(row, "receipt_no")
        rno = str(rno).strip() if rno is not None else ""
        if not rno:
            continue
        if rno in seen:
            continue  # duplicate row within the file
        seen.add(rno)

        party = str(cell(row, "party") or "").strip()
        mode = str(cell(row, "mode") or "").strip().lower() or None
        amount = _to_amount(cell(row, "amount"))
        rdate = _parse_date(cell(row, "date"))
        status = str(cell(row, "status") or "").strip().lower() or None
        created_by = str(cell(row, "created_by") or "").strip() or None

        cust_id = by_name.get(normalize_name(party))
        if cust_id is None:
            unmatched += 1

        rec = existing.get(rno)
        if rec is None:
            db.add(CustomerReceipt(
                receipt_no=rno, party_name=party, customer_id=cust_id,
                mode=mode, amount=amount, receipt_date=rdate,
                status=status, created_by=created_by,
            ))
            created += 1
        else:
            rec.party_name = party
            rec.customer_id = cust_id
            rec.mode = mode
            rec.amount = amount
            rec.receipt_date = rdate
            rec.status = status
            rec.created_by = created_by
            updated += 1

    log = ImportLog(entity="receipts", source_file=source_file,
                    rows_total=created + updated, created=created,
                    updated=updated, unmatched=unmatched)
    db.add(log)
    db.commit()
    wb.close()

    return {
        "entity": "receipts",
        "rows": created + updated,
        "created": created,
        "updated": updated,
        "unmatched": unmatched,
        "matched": created + updated - unmatched,
    }
