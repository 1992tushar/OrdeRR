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

from orderr_core.dates import get_current_business_date

from orderr_core.models.customer import Customer
from orderr_core.models.customer_receipt import CustomerReceipt
from orderr_core.models.outstanding_snapshot import OutstandingSnapshot
from orderr_core.models.import_log import ImportLog
from orderr_core.models.vasy_invoice import VasyInvoice, VasyInvoiceItem
from orderr_core.services.customer_service import (
    _to_amount, normalize_phone, import_customers_from_xlsx,
)
from orderr_core.services.template_parser import ERP_ITEMS


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
        if ("receipt_no" in colmap or ("closing" in colmap and "party" in colmap)
                or ("voucher" in colmap and "party" in colmap)):
            # header row found (heuristic: a distinctive column is present)
            return r_idx, colmap
    raise ValueError("Could not locate the header row in the export.")


def _erp_code_to_name():
    """{Vasy erp_code → erp display name} from the SKU catalog."""
    out = {}
    for _name, item in ERP_ITEMS.items():
        code = item.get("erp_code")
        if code:
            out[str(code).strip()] = item.get("erp_name")
    return out


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


# ── P2-3 outstanding import (customer refresh + daily snapshot) ─────────────

def import_outstanding(db: Session, file_bytes: bytes, snapshot_date: date = None,
                       source_file: str = None) -> dict:
    """Import a Vasy customer-outstanding export.

    Two layers:
      1. Customer master + `customer.outstanding` refresh (+ phone backfill,
         create-if-missing) — delegated to the existing, tested
         import_customers_from_xlsx so there is ONE customer-upsert path.
      2. A daily OutstandingSnapshot per party (opening/debit/credit/closing),
         upserted on (party_key, snapshot_date) so a same-day re-import updates
         rather than duplicates — this is what gives balances a history.

    Customer match is phone-first (Contact No. last-10) then normalized name.
    Unmatched parties still get a snapshot with customer_id NULL.
    """
    snapshot_date = snapshot_date or get_current_business_date()

    # ── layer 1: customer master + outstanding refresh (commits internally) ──
    cust_summary = import_customers_from_xlsx(db, file_bytes)

    # ── layer 2: snapshots ──
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception as e:
        raise ValueError(f"Could not read the outstanding Excel file: {e}")
    ws = wb.active

    labels = {
        "party": ("party name", "party", "name", "customer name"),
        "contact": ("contact no.", "contact no", "contact", "phone", "mobile no.", "mobile"),
        "opening": ("opening balance", "opening"),
        "debit": ("debit",),
        "credit": ("credit",),
        "closing": ("closing", "closing balance", "outstanding", "balance"),
    }
    header_idx, col = _find_header(ws, labels)
    if "party" not in col or "closing" not in col:
        raise ValueError("Outstanding export must have 'Party Name' and 'Closing' columns.")

    by_phone, by_name = _build_customer_lookup(db)  # after layer 1 → includes new customers

    existing = {s.party_key: s for s in db.query(OutstandingSnapshot)
                .filter(OutstandingSnapshot.snapshot_date == snapshot_date).all()}

    created = updated = unmatched = 0
    seen = set()

    def cell(row, key):
        i = col.get(key)
        return row[i] if (i is not None and i < len(row)) else None

    for row in ws.iter_rows(min_row=header_idx + 2, values_only=True):
        if not row:
            continue
        party = str(cell(row, "party") or "").strip()
        if not party:
            continue
        key = normalize_name(party)
        if not key or key in seen:
            continue
        seen.add(key)

        contact = cell(row, "contact")
        contact = str(contact).strip() if contact not in (None, "") else None
        opening = _to_amount(cell(row, "opening"))
        debit = _to_amount(cell(row, "debit"))
        credit = _to_amount(cell(row, "credit"))
        closing = _to_amount(cell(row, "closing"))

        # phone-first, then name
        cust_id = None
        p10 = _phone_last10(contact)
        if p10:
            cust_id = by_phone.get(p10)
        if cust_id is None:
            cust_id = by_name.get(key)
        if cust_id is None:
            unmatched += 1

        snap = existing.get(key)
        if snap is None:
            db.add(OutstandingSnapshot(
                customer_id=cust_id, party_name=party, party_key=key,
                contact_no=contact, opening_balance=opening, debit=debit,
                credit=credit, closing=closing, snapshot_date=snapshot_date,
            ))
            created += 1
        else:
            snap.customer_id = cust_id
            snap.party_name = party
            snap.contact_no = contact
            snap.opening_balance = opening
            snap.debit = debit
            snap.credit = credit
            snap.closing = closing
            updated += 1

    db.add(ImportLog(entity="outstanding", source_file=source_file,
                     rows_total=created + updated, created=created,
                     updated=updated, unmatched=unmatched,
                     notes=f"snapshot_date={snapshot_date.isoformat()}; "
                           f"customers +{cust_summary.get('created',0)}/"
                           f"~{cust_summary.get('updated',0)}"))
    db.commit()
    wb.close()

    return {
        "entity": "outstanding",
        "snapshot_date": snapshot_date.isoformat(),
        "rows": created + updated,
        "created": created,
        "updated": updated,
        "unmatched": unmatched,
        "matched": created + updated - unmatched,
        "customers": cust_summary,
    }


# ── P2-15 Vasy sales-invoice import (authoritative revenue) ────────────────

def import_sales_invoices(db: Session, file_bytes: bytes, source_file: str = None) -> dict:
    """Import a Vasy sales-invoice export (line-item level) into
    VasyInvoice + VasyInvoiceItem.

    Rows are grouped by Voucher No; the invoice header total = Σ line net.
    Upsert on voucher_no and REPLACE its lines (clean idempotency — the export
    always carries the full invoice). Join party → customer phone-first
    (Mobile No. where present) then normalized name; unmatched → NULL.
    """
    try:
        # NOT read_only: the Vasy sales export declares a bad sheet dimension,
        # which makes openpyxl's read-only iterator truncate rows to 1 column.
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        raise ValueError(f"Could not read the sales-invoice Excel file: {e}")
    ws = wb.active

    labels = {
        "voucher": ("voucher no", "voucher no.", "voucher number", "invoice no", "invoice no."),
        "date": ("date", "invoice date"),
        "party": ("party name", "party", "customer name", "name"),
        "mobile": ("mobile no.", "mobile no", "mobile", "contact no.", "phone"),
        "category": ("category name", "category"),
        "item_code": ("item code", "item", "code"),
        "qty": ("qty", "quantity"),
        "net": ("net amount", "amount", "net"),
        "branch": ("branch",),
        "address": ("address",),
    }
    header_idx, col = _find_header(ws, labels)
    if "voucher" not in col or "party" not in col:
        raise ValueError("Sales-invoice export must have 'Voucher No' and 'Party Name' columns.")

    by_phone, by_name = _build_customer_lookup(db)
    erp = _erp_code_to_name()

    def cell(row, key):
        i = col.get(key)
        return row[i] if (i is not None and i < len(row)) else None

    # group lines by voucher_no
    invoices = {}
    for row in ws.iter_rows(min_row=header_idx + 2, values_only=True):
        if not row:
            continue
        vno = cell(row, "voucher")
        vno = str(vno).strip() if vno is not None else ""
        if not vno:
            continue
        inv = invoices.get(vno)
        if inv is None:
            party = str(cell(row, "party") or "").strip()
            mobile = cell(row, "mobile")
            inv = invoices[vno] = {
                "date": _parse_date(cell(row, "date")),
                "party": party,
                "mobile": str(mobile).strip() if mobile not in (None, "", "null", "-") else None,
                "branch": str(cell(row, "branch") or "").strip() or None,
                "address": str(cell(row, "address") or "").strip() or None,
                "lines": [],
            }
        code = str(cell(row, "item_code") or "").strip()
        qty = _to_amount(cell(row, "qty"))
        net = _to_amount(cell(row, "net"))
        inv["lines"].append({
            "item_code": code or None,
            "erp_name": erp.get(code) or (str(cell(row, "category") or "").strip() or None),
            "category": str(cell(row, "category") or "").strip() or None,
            "qty": qty, "net": net,
        })
    wb.close()

    existing = {v.voucher_no: v for v in db.query(VasyInvoice).all()}
    created = updated = unmatched = 0
    total_amount = 0.0

    for vno, inv in invoices.items():
        total = sum(float(l["net"]) for l in inv["lines"])
        total_amount += total
        key = normalize_name(inv["party"])
        cust_id = None
        p10 = _phone_last10(inv["mobile"])
        if p10:
            cust_id = by_phone.get(p10)
        if cust_id is None:
            cust_id = by_name.get(key)
        if cust_id is None:
            unmatched += 1

        rec = existing.get(vno)
        if rec is None:
            rec = VasyInvoice(voucher_no=vno)
            db.add(rec)
            created += 1
        else:
            rec.items.clear()   # replace lines (cascade delete-orphan)
            updated += 1
        rec.invoice_date = inv["date"]
        rec.party_name = inv["party"]
        rec.party_key = key
        rec.customer_id = cust_id
        rec.total = round(total, 2)
        rec.item_count = len(inv["lines"])
        rec.branch = inv["branch"]
        rec.address = inv["address"]
        for l in inv["lines"]:
            rec.items.append(VasyInvoiceItem(
                item_code=l["item_code"], erp_name=l["erp_name"], category=l["category"],
                qty=l["qty"], net_amount=l["net"],
            ))

    db.add(ImportLog(entity="sales_invoices", source_file=source_file,
                     rows_total=created + updated, created=created,
                     updated=updated, unmatched=unmatched,
                     notes=f"lines={sum(len(i['lines']) for i in invoices.values())}; "
                           f"total={round(total_amount, 2)}"))
    db.commit()

    return {
        "entity": "sales_invoices",
        "rows": created + updated,
        "invoices": created + updated,
        "created": created,
        "updated": updated,
        "unmatched": unmatched,
        "matched": created + updated - unmatched,
        "total_fmt": _fmt_inr_local(total_amount),
    }


def _fmt_inr_local(amount):
    from orderr_core.services.analytics_service import fmt_inr
    return fmt_inr(amount)
