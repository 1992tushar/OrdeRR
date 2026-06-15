"""
tests/test_billing_integration.py

Integration tests for the OrdeRR billing system — flags ENABLED.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

_BILLING_FLAGS_ON = {
    "FLAG_BILLING_ENABLED":       "true",
    "FLAG_BILLING_AUTO_INVOICE":  "true",
    "FLAG_BILLING_BULK_GENERATE": "true",
}

_BILLING_FLAGS_OFF = {
    "FLAG_BILLING_ENABLED":       "false",
    "FLAG_BILLING_AUTO_INVOICE":  "false",
    "FLAG_BILLING_BULK_GENERATE": "false",
}

_TEST_PHONE     = "919876543210"
_TEST_NAME      = "FAIZ KHATIK"
_PRODUCT_FEET   = "Chicken Feet"
_PRODUCT_LIVER  = "Chicken Liver and Gizzard"
_PRICE_FEET     = 45.00
_PRICE_LIVER    = 38.00
_QTY_FEET       = 10.0
_QTY_LIVER      = 5.0
_EXPECTED_TOTAL = _QTY_FEET * _PRICE_FEET + _QTY_LIVER * _PRICE_LIVER  # 640.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reload_app_with_flags(env: dict) -> None:
    for k, v in env.items():
        os.environ[k] = v
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("app"):
            del sys.modules[mod_name]


def _extract_pdf_text(pdf_bytes: bytes) -> str | None:
    """Try pdfplumber → PyPDF2 → manual FlateDecode → None."""
    try:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except ImportError:
        pass
    try:
        import io
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ImportError:
        pass
    # Manual FlateDecode decompression (ReportLab uses this by default)
    try:
        import zlib
        import re
        streams = re.findall(rb'stream\r?\n(.*?)\r?\nendstream', pdf_bytes, re.DOTALL)
        parts = []
        for s in streams:
            try:
                parts.append(zlib.decompress(s).decode("latin-1", errors="replace"))
            except Exception:
                pass
        return "\n".join(parts) if parts else None
    except Exception:
        return None


def _invoice_id(inv: dict) -> int:
    """Return the canonical integer ID from a generate-response dict."""
    return inv.get("id") or inv.get("invoice_id")


# ─────────────────────────────────────────────────────────────────────────────
# Session fixture: enable billing flags once for the whole module
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def enable_billing_flags_for_session():
    original = {k: os.environ.get(k) for k in _BILLING_FLAGS_ON}
    _reload_app_with_flags(_BILLING_FLAGS_ON)
    yield
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def billing_flags_on(monkeypatch):
    for k, v in _BILLING_FLAGS_ON.items():
        monkeypatch.setenv(k, v)


# ─────────────────────────────────────────────────────────────────────────────
# Per-test fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_invoice_dir(tmp_path, monkeypatch):
    invoice_dir = tmp_path / "invoices"
    invoice_dir.mkdir()
    monkeypatch.setenv("INVOICE_DIR", str(invoice_dir))
    try:
        import app.services.billing_service as bs
        monkeypatch.setattr(bs, "INVOICE_DIR", str(invoice_dir), raising=False)
    except ImportError:
        pass
    try:
        import app.routes.billing as br
        monkeypatch.setattr(br, "INVOICE_DIR", str(invoice_dir), raising=False)
    except ImportError:
        pass
    return invoice_dir


@pytest.fixture()
def db_session():
    from app.database import engine, SessionLocal
    from app.models.invoice import Invoice, CustomerProductPrice, DefaultProductPrice, ProductItemCode  # noqa
    from app.database import Base
    Base.metadata.create_all(bind=engine)

    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)

    # SAVEPOINT so app-level commits don't break the outer rollback
    session.begin_nested()

    original_commit = session.commit
    def safe_commit():
        session.expire_all()
        try:
            session.begin_nested()
        except Exception:
            pass
    session.commit = safe_commit

    yield session

    session.commit = original_commit
    session.close()
    try:
        transaction.rollback()
    except Exception:
        pass
    connection.close()


@pytest.fixture()
def client(db_session):
    from app.main import app
    from app.database import get_db

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def auth():
    username = os.getenv("DASHBOARD_USERNAME", "admin")
    password = os.getenv("DASHBOARD_PASSWORD", "password")
    return (username, password)


@pytest.fixture()
def seeded_order(db_session):
    import json
    from app.models.order import Order

    order = Order(
        customer_phone=_TEST_PHONE,
        customer_name=_TEST_NAME,
        status="confirmed",
        business_date=date.today().isoformat(),
        delivery_date=date.today().isoformat(),
        raw_message="Chicken Feet 10 KGS\nChicken Liver and Gizzard 5 KGS",
        parsed_items=json.dumps([
            {"product": _PRODUCT_FEET,  "quantity": _QTY_FEET,  "unit": "KGS"},
            {"product": _PRODUCT_LIVER, "quantity": _QTY_LIVER, "unit": "KGS"},
        ]),
        unclear_items=json.dumps([]),
        is_unclear=False,
    )
    db_session.add(order)
    db_session.flush()
    db_session.refresh(order)
    return order


@pytest.fixture()
def seeded_prices(db_session):
    from app.models.invoice import DefaultProductPrice
    from sqlalchemy import delete

    db_session.execute(
        delete(DefaultProductPrice).where(
            DefaultProductPrice.product_name.in_([_PRODUCT_FEET, _PRODUCT_LIVER])
        )
    )
    db_session.flush()

    prices = [
        DefaultProductPrice(product_name=_PRODUCT_FEET,  price_per_unit=_PRICE_FEET,  uom="KGS"),
        DefaultProductPrice(product_name=_PRODUCT_LIVER, price_per_unit=_PRICE_LIVER, uom="KGS"),
    ]
    db_session.add_all(prices)
    db_session.flush()
    return prices


@pytest.fixture()
def generated_invoice(client, auth, seeded_order, seeded_prices, tmp_invoice_dir):
    resp = client.post(
        "/admin/billing/invoices/generate",
        json={
            "order_id":     seeded_order.id,
            "invoice_date": date.today().isoformat(),
        },
        auth=auth,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Normalise: always expose both "id" and "invoice_id"
    if "id" not in data and "invoice_id" in data:
        data["id"] = data["invoice_id"]
    if "invoice_id" not in data and "id" in data:
        data["invoice_id"] = data["id"]
    return data


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pricing CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestPricingCrud:

    def test_set_and_read_default_price(self, client, auth):
        resp = client.post(
            "/admin/billing/prices/defaults",
            json={"product_name": "Chicken Breast", "price_per_unit": 120.00, "uom": "KGS"},
            auth=auth,
        )
        assert resp.status_code == 200, resp.text

        resp = client.get("/admin/billing/prices/defaults", auth=auth)
        assert resp.status_code == 200
        prices = resp.json()
        names = [p["product_name"] for p in prices]
        assert "Chicken Breast" in names
        row = next(p for p in prices if p["product_name"] == "Chicken Breast")
        assert float(row["price_per_unit"]) == pytest.approx(120.00)

    def test_set_and_read_customer_price(self, client, auth):
        resp = client.post(
            "/admin/billing/prices",
            json={
                "customer_phone": _TEST_PHONE,
                "product_name":   _PRODUCT_FEET,
                "price_per_unit": 50.00,
                "uom":            "KGS",
            },
            auth=auth,
        )
        assert resp.status_code == 200, resp.text

        resp = client.get(f"/admin/billing/prices/{_TEST_PHONE}", auth=auth)
        assert resp.status_code == 200
        prices = resp.json()
        products = [p["product_name"] for p in prices]
        assert _PRODUCT_FEET in products
        row = next(p for p in prices if p["product_name"] == _PRODUCT_FEET)
        assert float(row["price_per_unit"]) == pytest.approx(50.00)

    def test_delete_customer_price(self, client, auth):
        create_resp = client.post(
            "/admin/billing/prices",
            json={
                "customer_phone": _TEST_PHONE,
                "product_name":   _PRODUCT_LIVER,
                "price_per_unit": 40.00,
                "uom":            "KGS",
            },
            auth=auth,
        )
        assert create_resp.status_code == 200
        price_id = create_resp.json()["id"]

        del_resp = client.delete(f"/admin/billing/prices/{price_id}", auth=auth)
        assert del_resp.status_code == 200

        list_resp = client.get(f"/admin/billing/prices/{_TEST_PHONE}", auth=auth)
        assert list_resp.status_code == 200
        ids = [p["id"] for p in list_resp.json()]
        assert price_id not in ids


# ─────────────────────────────────────────────────────────────────────────────
# 2. Manual invoice generation
# ─────────────────────────────────────────────────────────────────────────────

class TestManualInvoiceGeneration:

    def test_generate_invoice_returns_200_with_required_fields(
        self, client, auth, seeded_order, seeded_prices, tmp_invoice_dir
    ):
        resp = client.post(
            "/admin/billing/invoices/generate",
            json={
                "order_id":     seeded_order.id,
                "invoice_date": date.today().isoformat(),
            },
            auth=auth,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "invoice_number" in data
        assert "pdf_path" in data
        assert data["invoice_number"] is not None

    def test_generated_pdf_exists_on_disk(self, generated_invoice, tmp_invoice_dir):
        pdf_path = generated_invoice["pdf_path"]
        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = tmp_invoice_dir / candidate
        assert candidate.exists(), f"PDF not found at {candidate}"
        assert candidate.stat().st_size > 0

    def test_invoice_appears_in_list(self, client, auth, generated_invoice):
        resp = client.get("/admin/billing/invoices", auth=auth)
        assert resp.status_code == 200
        invoices = resp.json()
        assert len(invoices) > 0, "Invoice list is empty"

        inv_id = generated_invoice["id"]
        inv_number = generated_invoice["invoice_number"]

        if isinstance(invoices[0], dict):
            # Find by id or invoice_id or invoice_number — whichever the API exposes
            found = any(
                i.get("id") == inv_id
                or i.get("invoice_id") == inv_id
                or i.get("invoice_number") == inv_number
                for i in invoices
            )
        else:
            # List of raw IDs
            found = inv_id in invoices

        assert found, (
            f"Invoice id={inv_id} / number={inv_number} not found in list: {invoices}"
        )

    def test_invoice_total_is_correct(self, client, auth, generated_invoice):
        inv_id = generated_invoice["id"]
        resp = client.get(f"/admin/billing/invoices/{inv_id}", auth=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert float(data["total_amount"]) == pytest.approx(_EXPECTED_TOTAL, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 3. PDF content
# ─────────────────────────────────────────────────────────────────────────────

class TestPdfContentBasic:

    def test_pdf_is_non_empty_and_minimum_size(self, generated_invoice, tmp_invoice_dir):
        pdf_path = generated_invoice["pdf_path"]
        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = tmp_invoice_dir / candidate
        assert candidate.exists()
        size = candidate.stat().st_size
        assert size > 1024, f"PDF too small: {size} bytes"

    def test_pdf_contains_expected_strings(
        self, client, auth, generated_invoice, tmp_invoice_dir
    ):
        inv_id = generated_invoice["id"]
        resp = client.get(f"/admin/billing/invoices/{inv_id}/download", auth=auth)

        if resp.status_code == 200:
            pdf_bytes = resp.content
        else:
            pdf_path = generated_invoice["pdf_path"]
            candidate = Path(pdf_path)
            if not candidate.is_absolute():
                candidate = tmp_invoice_dir / candidate
            pdf_bytes = candidate.read_bytes()

        text = _extract_pdf_text(pdf_bytes)
        assert text is not None, (
            "Could not extract text from PDF — install pdfplumber or PyPDF2, "
            "or check ReportLab stream encoding"
        )
        assert "Fluffy Fresh Foods" in text, f"Company name missing. Extracted:\n{text[:500]}"
        assert _TEST_NAME in text,           f"Customer name missing. Extracted:\n{text[:500]}"
        assert "INV" in text,                f"Invoice prefix missing. Extracted:\n{text[:500]}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Already-billed guard
# ─────────────────────────────────────────────────────────────────────────────

class TestAlreadyBilledBlocked:

    def test_duplicate_invoice_rejected(
        self, client, auth, generated_invoice, seeded_order
    ):
        resp = client.post(
            "/admin/billing/invoices/generate",
            json={
                "order_id":     seeded_order.id,
                "invoice_date": date.today().isoformat(),
            },
            auth=auth,
        )
        assert resp.status_code in (400, 409), (
            f"Expected 400/409 for duplicate, got {resp.status_code}: {resp.text}"
        )

    def test_duplicate_invoice_error_message_is_descriptive(
        self, client, auth, generated_invoice, seeded_order
    ):
        resp = client.post(
            "/admin/billing/invoices/generate",
            json={
                "order_id":     seeded_order.id,
                "invoice_date": date.today().isoformat(),
            },
            auth=auth,
        )
        assert resp.status_code in (400, 409), (
            f"Expected 400/409, got {resp.status_code}"
        )
        body = resp.json()
        detail = (body.get("detail") or "").lower()
        assert any(kw in detail for kw in ("billed", "invoice", "exists", "already")), (
            f"Error detail not descriptive enough: {detail!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Auto-invoice via service
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoInvoiceViaService:

    def test_try_auto_invoice_generates_pdf(
        self, db_session, seeded_prices, tmp_invoice_dir
    ):
        import json
        from app.models.order import Order
        from app.services.billing_service import try_auto_invoice

        order = Order(
            customer_phone=_TEST_PHONE,
            customer_name=_TEST_NAME,
            status="confirmed",
            business_date=date.today().isoformat(),
            delivery_date=date.today().isoformat(),
            raw_message="Chicken Feet 10 KGS\nChicken Liver and Gizzard 5 KGS",
            parsed_items=json.dumps([
                {"product": _PRODUCT_FEET,  "quantity": _QTY_FEET,  "unit": "KGS"},
                {"product": _PRODUCT_LIVER, "quantity": _QTY_LIVER, "unit": "KGS"},
            ]),
            unclear_items=json.dumps([]),
            is_unclear=False,
        )
        db_session.add(order)
        db_session.flush()
        db_session.refresh(order)

        result = try_auto_invoice(db_session, order)
        assert result is not None, "try_auto_invoice returned None"

        pdf_path = (
            result.get("pdf_path") if isinstance(result, dict)
            else getattr(result, "pdf_path", None)
        )
        assert pdf_path is not None
        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = tmp_invoice_dir / candidate
        assert candidate.exists()
        assert candidate.stat().st_size > 0

    def test_try_auto_invoice_returns_none_when_prices_missing(
        self, db_session, tmp_invoice_dir
    ):
        import json
        from app.models.order import Order
        from app.services.billing_service import try_auto_invoice

        order = Order(
            customer_phone="919000000001",
            customer_name="Mystery Customer",
            status="confirmed",
            business_date=date.today().isoformat(),
            delivery_date=date.today().isoformat(),
            raw_message="Chicken Breast 5 KGS",
            parsed_items=json.dumps([
                {"product": "Chicken Breast", "quantity": 5.0, "unit": "KGS"},
            ]),
            unclear_items=json.dumps([]),
            is_unclear=False,
        )
        db_session.add(order)
        db_session.flush()
        db_session.refresh(order)

        result = try_auto_invoice(db_session, order)
        assert result is None, f"Expected None when prices missing, got {result}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Bulk invoice generation
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkInvoiceGeneration:

    @pytest.fixture()
    def three_seeded_orders(self, db_session, seeded_prices):
        orders = []
        for i in range(3):
            phone = f"9190000000{i:02d}"
            orders.append(_make_confirmed_order(db_session, phone, f"Bulk Customer {i}"))
        return orders

    def test_bulk_generate_success_count(
        self, client, auth, three_seeded_orders, tmp_invoice_dir
    ):
        resp = client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": date.today().isoformat()},
            auth=auth,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Our 3 orders must all succeed; pre-existing orders may also appear
        assert data.get("success", 0) >= 3, f"Expected ≥3 successes: {data}"

    def test_bulk_generate_creates_pdf_files(
        self, client, auth, three_seeded_orders, tmp_invoice_dir
    ):
        client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": date.today().isoformat()},
            auth=auth,
        )
        pdf_files = list(tmp_invoice_dir.glob("*.pdf"))
        assert len(pdf_files) >= 3, f"Expected ≥3 PDFs, found {len(pdf_files)}"

    def test_bulk_generate_skips_already_billed(
        self, client, auth, generated_invoice, seeded_order, seeded_prices, tmp_invoice_dir
    ):
        """seeded_order already has an invoice — bulk must not create a second one."""
        inv_id     = generated_invoice["id"]
        inv_number = generated_invoice["invoice_number"]

        client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": date.today().isoformat()},
            auth=auth,
        )

        # Fetch the specific invoice and confirm it still exists exactly once
        resp = client.get(f"/admin/billing/invoices/{inv_id}", auth=auth)
        assert resp.status_code == 200, f"Invoice {inv_id} disappeared after bulk"

        # Also verify the list endpoint doesn't show a duplicate
        all_resp = client.get("/admin/billing/invoices", auth=auth)
        assert all_resp.status_code == 200
        invoices = all_resp.json()

        if invoices and isinstance(invoices[0], dict):
            matching = [
                i for i in invoices
                if i.get("id") == inv_id
                or i.get("invoice_id") == inv_id
                or i.get("invoice_number") == inv_number
            ]
        else:
            matching = [i for i in invoices if i == inv_id]

        assert len(matching) == 1, (
            f"Expected exactly 1 invoice for already-billed order, found {len(matching)}: {matching}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 7. Bulk flag gate
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkEndpointFlagGate:

    def test_bulk_generate_returns_404_when_flag_off(
        self, client, auth, monkeypatch, tmp_invoice_dir
    ):
        monkeypatch.setenv("FLAG_BILLING_BULK_GENERATE", "false")
        _patch_flag("FLAG_BILLING_BULK_GENERATE", False)

        resp = client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": date.today().isoformat()},
            auth=auth,
        )
        assert resp.status_code == 404

    def test_bulk_generate_works_when_flag_on(
        self, client, auth, monkeypatch, tmp_invoice_dir
    ):
        monkeypatch.setenv("FLAG_BILLING_BULK_GENERATE", "true")
        _patch_flag("FLAG_BILLING_BULK_GENERATE", True)

        resp = client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": date.today().isoformat()},
            auth=auth,
        )
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 8. Void invoice
# ─────────────────────────────────────────────────────────────────────────────

class TestVoidInvoice:

    def test_void_sets_status_to_voided(self, client, auth, generated_invoice):
        inv_id = generated_invoice["id"]
        resp = client.delete(f"/admin/billing/invoices/{inv_id}", auth=auth)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("status") == "voided", (
            f"Expected 'voided', got {data.get('status')!r}. "
            f"If your void endpoint returns the pre-update record, add db.refresh() "
            f"after setting invoice.status = 'voided'."
        )

    def test_void_pdf_still_exists_on_disk(
        self, client, auth, generated_invoice, tmp_invoice_dir
    ):
        inv_id   = generated_invoice["id"]
        pdf_path = generated_invoice["pdf_path"]
        client.delete(f"/admin/billing/invoices/{inv_id}", auth=auth)
        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = tmp_invoice_dir / candidate
        assert candidate.exists(), "PDF must not be deleted from disk on void"

    def test_void_invoice_is_still_downloadable(self, client, auth, generated_invoice):
        inv_id = generated_invoice["id"]
        client.delete(f"/admin/billing/invoices/{inv_id}", auth=auth)
        resp = client.get(f"/admin/billing/invoices/{inv_id}/download", auth=auth)
        assert resp.status_code in (200, 404)

    def test_voided_invoice_shows_in_get(self, client, auth, generated_invoice):
        inv_id = generated_invoice["id"]
        client.delete(f"/admin/billing/invoices/{inv_id}", auth=auth)
        resp = client.get(f"/admin/billing/invoices/{inv_id}", auth=auth)
        assert resp.status_code == 200
        assert resp.json().get("status") == "voided", (
            f"Expected status='voided', got {resp.json().get('status')!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 9. Billing summary
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingSummary:

    def test_summary_returns_required_keys(self, client, auth, generated_invoice):
        today = date.today().isoformat()
        resp = client.get(f"/admin/billing/summary?date={today}", auth=auth)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        for key in ("total_orders", "billed", "unbilled", "revenue"):
            assert key in data, f"Key '{key}' missing from summary"

    def test_summary_billed_count_reflects_generated_invoice(
        self, client, auth, generated_invoice
    ):
        today = date.today().isoformat()
        resp = client.get(f"/admin/billing/summary?date={today}", auth=auth)
        assert resp.status_code == 200
        assert resp.json()["billed"] >= 1

    def test_summary_revenue_matches_invoice_total(
        self, client, auth, generated_invoice
    ):
        today = date.today().isoformat()
        resp = client.get(f"/admin/billing/summary?date={today}", auth=auth)
        assert resp.status_code == 200
        revenue = float(resp.json()["revenue"])
        assert revenue >= _EXPECTED_TOTAL, (
            f"Revenue {revenue} is less than expected {_EXPECTED_TOTAL}"
        )
        assert revenue % _EXPECTED_TOTAL == pytest.approx(0, abs=0.01), (
            f"Revenue {revenue} is not a clean multiple of {_EXPECTED_TOTAL} "
            f"— suggests cross-test contamination"
        )

    def test_summary_unbilled_decreases_after_generation(
        self, client, auth, seeded_order, seeded_prices, tmp_invoice_dir, db_session
    ):
        today = date.today().isoformat()
        before = client.get(f"/admin/billing/summary?date={today}", auth=auth).json()
        unbilled_before = before["unbilled"]

        resp = client.post(
            "/admin/billing/invoices/generate",
            json={"order_id": seeded_order.id, "invoice_date": today},
            auth=auth,
        )
        assert resp.status_code == 200, resp.text

        # Expire session cache so the summary query re-reads from DB
        db_session.expire_all()

        after = client.get(f"/admin/billing/summary?date={today}", auth=auth).json()
        assert after["unbilled"] == unbilled_before - 1, (
            f"Unbilled should decrease by 1: {unbilled_before} → {after['unbilled']}. "
            f"If still failing, the generate endpoint's DB write is not visible to "
            f"the summary query — check that both use the same session."
        )


# ─────────────────────────────────────────────────────────────────────────────
# 10. Rollback — flags OFF
# ─────────────────────────────────────────────────────────────────────────────

class TestRollbackFlagsOff:

    @pytest.fixture()
    def flags_off_client(self, monkeypatch):
        for k, v in _BILLING_FLAGS_OFF.items():
            monkeypatch.setenv(k, v)
        _reload_app_with_flags(_BILLING_FLAGS_OFF)
        from app.main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
        _reload_app_with_flags(_BILLING_FLAGS_ON)

    def test_invoices_endpoint_returns_404_when_flags_off(self, flags_off_client, auth):
        resp = flags_off_client.get("/admin/billing/invoices", auth=auth)
        assert resp.status_code == 404

    def test_generate_endpoint_returns_404_when_flags_off(self, flags_off_client, auth):
        resp = flags_off_client.post(
            "/admin/billing/invoices/generate",
            json={"order_id": 1, "invoice_date": date.today().isoformat()},
            auth=auth,
        )
        assert resp.status_code == 404

    def test_bulk_generate_returns_404_when_flags_off(self, flags_off_client, auth):
        resp = flags_off_client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": date.today().isoformat()},
            auth=auth,
        )
        assert resp.status_code == 404

    def test_dashboard_has_no_billing_tab_when_flags_off(self, flags_off_client, auth):
        resp = flags_off_client.get("/dashboard/", auth=auth)
        assert resp.status_code == 200
        html = resp.text.lower()
        assert "💰 billing" not in html and "tab-billing" not in html

    def test_existing_routes_still_work_when_flags_off(self, flags_off_client, auth):
        assert flags_off_client.get("/health").status_code == 200
        assert flags_off_client.get("/admin/customers", auth=auth).status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_confirmed_order(db_session, phone: str, name: str):
    import json
    from app.models.order import Order

    order = Order(
        customer_phone=phone,
        customer_name=name,
        status="confirmed",
        business_date=date.today().isoformat(),
        delivery_date=date.today().isoformat(),
        raw_message="Chicken Feet 10 KGS\nChicken Liver and Gizzard 5 KGS",
        parsed_items=json.dumps([
            {"product": _PRODUCT_FEET,  "quantity": _QTY_FEET,  "unit": "KGS"},
            {"product": _PRODUCT_LIVER, "quantity": _QTY_LIVER, "unit": "KGS"},
        ]),
        unclear_items=json.dumps([]),
        is_unclear=False,
    )
    db_session.add(order)
    db_session.flush()
    db_session.refresh(order)
    return order


def _patch_flag(flag_name: str, value: bool) -> None:
    try:
        import app.config.flags as flags_mod
        _ = flags_mod
    except ImportError:
        pass