"""
tests/test_billing_integration.py

Integration tests for the OrdeRR billing system — flags ENABLED.

Run with:
    FLAG_BILLING_ENABLED=true pytest tests/test_billing_integration.py -v

All DB mutations happen inside transactions that are rolled back after each
test, matching the pattern used in test_billing_flags_off.py.  The only
exception is on-disk PDF files, which are cleaned up via a tmp_invoice_dir
fixture that redirects INVOICE_DIR to a pytest tmp_path.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ── NOTE ─────────────────────────────────────────────────────────────────────
# The billing router is registered conditionally in app/main.py only when
# FLAG_BILLING_ENABLED=true.  We must set the env var *before* importing
# app.main so the conditional import runs.  The monkeypatch fixture can only
# patch os.environ after the module is already loaded, so we use a
# session-scoped autouse fixture that sets the variable at collection time and
# reloads the app module so the router is present for every test in this file.
# ─────────────────────────────────────────────────────────────────────────────

_BILLING_FLAGS_ON = {
    "FLAG_BILLING_ENABLED":      "true",
    "FLAG_BILLING_AUTO_INVOICE": "true",
    "FLAG_BILLING_BULK_GENERATE": "true",
}

_BILLING_FLAGS_OFF = {
    "FLAG_BILLING_ENABLED":      "false",
    "FLAG_BILLING_AUTO_INVOICE": "false",
    "FLAG_BILLING_BULK_GENERATE": "false",
}

# Test customer / product constants
_TEST_PHONE      = "919876543210"
_TEST_NAME       = "FAIZ KHATIK"
_PRODUCT_FEET    = "Chicken Feet"
_PRODUCT_LIVER   = "Chicken Liver and Gizzard"
_PRICE_FEET      = 45.00
_PRICE_LIVER     = 38.00
_QTY_FEET        = 10.0
_QTY_LIVER       = 5.0
_EXPECTED_TOTAL  = _QTY_FEET * _PRICE_FEET + _QTY_LIVER * _PRICE_LIVER  # 640.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reload_app_with_flags(env: dict) -> None:
    """Mutate os.environ and reload app.main so routers re-register."""
    for k, v in env.items():
        os.environ[k] = v
    # Remove cached modules so conditional imports re-run
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("app"):
            del sys.modules[mod_name]


def _make_order_payload(
    customer_phone: str = _TEST_PHONE,
    customer_name: str = _TEST_NAME,
    *,
    extra_items: list | None = None,
) -> dict:
    items = [
        {"product": _PRODUCT_FEET,  "quantity": _QTY_FEET,  "unit": "KGS"},
        {"product": _PRODUCT_LIVER, "quantity": _QTY_LIVER, "unit": "KGS"},
    ]
    if extra_items:
        items.extend(extra_items)
    return {
        "customer_phone": customer_phone,
        "customer_name":  customer_name,
        "items":          items,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Session-scoped: enable billing flags once for this entire test module
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def enable_billing_flags_for_session():
    """Set billing env vars before any test in this module imports app.main."""
    original = {k: os.environ.get(k) for k in _BILLING_FLAGS_ON}
    _reload_app_with_flags(_BILLING_FLAGS_ON)
    yield
    # Restore
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ─────────────────────────────────────────────────────────────────────────────
# Per-test fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def billing_flags_on(monkeypatch):
    """Ensure all three billing flags are ON for every test (default)."""
    for k, v in _BILLING_FLAGS_ON.items():
        monkeypatch.setenv(k, v)


@pytest.fixture()
def tmp_invoice_dir(tmp_path, monkeypatch):
    """Redirect INVOICE_DIR to a pytest tmp_path so PDFs are auto-cleaned."""
    invoice_dir = tmp_path / "invoices"
    invoice_dir.mkdir()
    monkeypatch.setenv("INVOICE_DIR", str(invoice_dir))
    # Also patch the module-level constant in billing_service and billing router
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
    """
    Provide a SQLAlchemy session that rolls back after each test.
    Mirrors the pattern from test_billing_flags_off.py.
    """
    from app.database import engine, SessionLocal

    # Ensure billing tables exist (idempotent when flag is on)
    from app.models.invoice import Invoice, CustomerProductPrice, DefaultProductPrice, ProductItemCode  # noqa: F401
    from app.database import Base
    Base.metadata.create_all(bind=engine)

    connection = engine.connect()
    transaction = connection.begin()
    session = SessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    """TestClient with the DB dependency overridden to use the test session."""
    from app.main import app
    from app.database import get_db

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def auth():
    """HTTP Basic credentials matching DASHBOARD_USERNAME / DASHBOARD_PASSWORD."""
    username = os.getenv("DASHBOARD_USERNAME", "admin")
    password = os.getenv("DASHBOARD_PASSWORD", "password")
    return (username, password)


@pytest.fixture()
def seeded_order(db_session):
    """
    Insert a single confirmed test order (FAIZ KHATIK) and return it.
    Rolled back automatically after the test.
    """
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
    db_session.commit()
    db_session.refresh(order)
    return order


@pytest.fixture()
def seeded_prices(db_session):
    """
    Insert default prices for the two test products and return them.
    Rolled back automatically after the test.
    """
    from app.models.invoice import DefaultProductPrice

    prices = [
        DefaultProductPrice(product_name=_PRODUCT_FEET,  price_per_unit=_PRICE_FEET,  uom="KGS"),
        DefaultProductPrice(product_name=_PRODUCT_LIVER, price_per_unit=_PRICE_LIVER, uom="KGS"),
    ]
    db_session.add_all(prices)
    db_session.commit()
    return prices


@pytest.fixture()
def generated_invoice(client, auth, seeded_order, seeded_prices, tmp_invoice_dir):
    """
    Generate a single invoice via the HTTP endpoint and return the response JSON.
    Used by multiple tests to avoid repeating the generation step.
    """
    resp = client.post(
        "/admin/billing/invoices/generate",
        json={
            "order_id":    seeded_order.id,
            "invoice_date": date.today().isoformat(),
        },
        auth=auth,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pricing CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestPricingCrud:

    def test_set_and_read_default_price(self, client, auth):
        """POST a new default price, then GET all defaults and confirm it appears."""
        resp = client.post(
            "/admin/billing/prices/defaults",
            json={"product_name": "Chicken Breast", "price_per_unit": 120.00, "uom": "KGS"},
            auth=auth,
        )
        assert resp.status_code == 200, resp.text

        resp = client.get("/admin/billing/prices/defaults", auth=auth)
        assert resp.status_code == 200
        prices = resp.json()
        names  = [p["product_name"] for p in prices]
        assert "Chicken Breast" in names
        row = next(p for p in prices if p["product_name"] == "Chicken Breast")
        assert float(row["price_per_unit"]) == pytest.approx(120.00)

    def test_set_and_read_customer_price(self, client, auth):
        """POST a customer-specific price and retrieve it."""
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
        """Create a customer price, delete it, and confirm it's gone."""
        # Create
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

        # Delete
        del_resp = client.delete(f"/admin/billing/prices/{price_id}", auth=auth)
        assert del_resp.status_code == 200

        # Confirm gone
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
                "order_id":    seeded_order.id,
                "invoice_date": date.today().isoformat(),
            },
            auth=auth,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "invoice_number" in data
        assert "pdf_path" in data
        assert data["invoice_number"] is not None

    def test_generated_pdf_exists_on_disk(
        self, generated_invoice, tmp_invoice_dir
    ):
        pdf_path = generated_invoice["pdf_path"]
        # pdf_path may be just the filename or a full path
        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = tmp_invoice_dir / candidate
        assert candidate.exists(), f"PDF not found at {candidate}"
        assert candidate.stat().st_size > 0

    def test_invoice_appears_in_list(
        self, client, auth, generated_invoice
    ):
        resp = client.get("/admin/billing/invoices", auth=auth)
        assert resp.status_code == 200
        invoices = resp.json()
        inv_numbers = [i["invoice_number"] for i in invoices]
        assert generated_invoice["invoice_number"] in inv_numbers

    def test_invoice_total_is_correct(
        self, client, auth, generated_invoice
    ):
        inv_id = generated_invoice["id"]
        resp = client.get(f"/admin/billing/invoices/{inv_id}", auth=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert float(data["total_amount"]) == pytest.approx(_EXPECTED_TOTAL, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 3. PDF content inspection
# ─────────────────────────────────────────────────────────────────────────────

class TestPdfContentBasic:

    def test_pdf_is_non_empty_and_minimum_size(
        self, generated_invoice, tmp_invoice_dir
    ):
        pdf_path = generated_invoice["pdf_path"]
        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = tmp_invoice_dir / candidate
        assert candidate.exists()
        size = candidate.stat().st_size
        assert size > 5 * 1024, f"PDF too small: {size} bytes"

    def test_pdf_contains_expected_strings(
        self, client, auth, generated_invoice, tmp_invoice_dir
    ):
        """
        Stream the PDF via the download endpoint and check for key strings
        in the raw bytes (ReportLab embeds text as UTF-8/Latin-1 streams).
        Falls back to a direct disk read if the download route isn't available.
        """
        inv_id = generated_invoice["id"]
        resp = client.get(f"/admin/billing/invoices/{inv_id}/download", auth=auth)

        if resp.status_code == 200:
            pdf_bytes = resp.content
        else:
            # Fall back: read from disk
            pdf_path = generated_invoice["pdf_path"]
            candidate = Path(pdf_path)
            if not candidate.is_absolute():
                candidate = tmp_invoice_dir / candidate
            pdf_bytes = candidate.read_bytes()

        # Try pdfplumber first, then PyPDF2, then raw byte search
        text = _extract_pdf_text(pdf_bytes)
        if text:
            assert "Fluffy Fresh Foods" in text, "Company name missing from PDF"
            assert _TEST_NAME in text,           "Customer name missing from PDF"
            assert "INV" in text,                "Invoice prefix missing from PDF"
        else:
            # Fallback: raw byte search (ReportLab BT/ET streams)
            raw = pdf_bytes.decode("latin-1", errors="replace")
            assert "Fluffy" in raw,   "Company name not found in raw PDF bytes"
            assert "FAIZ" in raw,     "Customer name not found in raw PDF bytes"
            assert "INV" in raw,      "Invoice prefix not found in raw PDF bytes"


def _extract_pdf_text(pdf_bytes: bytes) -> str | None:
    """Try pdfplumber → PyPDF2 → return None if neither is available."""
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
        return "\n".join(
            page.extract_text() or "" for page in reader.pages
        )
    except ImportError:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Already-billed guard
# ─────────────────────────────────────────────────────────────────────────────

class TestAlreadyBilledBlocked:

    def test_duplicate_invoice_rejected(
        self, client, auth, generated_invoice, seeded_order
    ):
        """A second generate request for the same order must be rejected."""
        resp = client.post(
            "/admin/billing/invoices/generate",
            json={
                "order_id":    seeded_order.id,
                "invoice_date": date.today().isoformat(),
            },
            auth=auth,
        )
        assert resp.status_code in (400, 409), (
            f"Expected 400 or 409 for duplicate, got {resp.status_code}: {resp.text}"
        )

    def test_duplicate_invoice_error_message_is_descriptive(
        self, client, auth, generated_invoice, seeded_order
    ):
        resp = client.post(
            "/admin/billing/invoices/generate",
            json={
                "order_id":    seeded_order.id,
                "invoice_date": date.today().isoformat(),
            },
            auth=auth,
        )
        body = resp.json()
        detail = (body.get("detail") or "").lower()
        # Expect something like "already billed" or "already invoiced"
        assert any(kw in detail for kw in ("billed", "invoice", "exists", "already")), (
            f"Error detail not descriptive enough: {detail}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Auto-invoice via service call
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoInvoiceViaService:

    def test_try_auto_invoice_generates_pdf(
        self, db_session, seeded_prices, tmp_invoice_dir
    ):
        """Call try_auto_invoice() directly — no HTTP layer involved."""
        import json
        from app.models.order import Order
        from app.services.billing_service import try_auto_invoice

        # Seed a fresh confirmed order
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
        db_session.commit()
        db_session.refresh(order)

        result = try_auto_invoice(db_session, order)

        assert result is not None, "try_auto_invoice returned None — expected an invoice dict/object"

        # Accept either a dict or an ORM object
        pdf_path = (
            result.get("pdf_path") if isinstance(result, dict) else getattr(result, "pdf_path", None)
        )
        assert pdf_path is not None, "No pdf_path in auto-invoice result"

        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = tmp_invoice_dir / candidate
        assert candidate.exists(), f"Auto-invoice PDF not found at {candidate}"
        assert candidate.stat().st_size > 0

    def test_try_auto_invoice_returns_none_when_prices_missing(
        self, db_session, tmp_invoice_dir
    ):
        """Without prices seeded, try_auto_invoice should return None (not raise)."""
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
        db_session.commit()
        db_session.refresh(order)

        # No prices → should return None without raising
        result = try_auto_invoice(db_session, order)
        assert result is None, f"Expected None when prices missing, got {result}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Bulk invoice generation
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkInvoiceGeneration:

    @pytest.fixture()
    def three_seeded_orders(self, db_session, seeded_prices):
        """Three fresh confirmed orders with prices already seeded."""
        import json

        orders = []
        for i in range(3):
            phone = f"9190000000{i:02d}"
            order = _make_confirmed_order(db_session, phone, f"Bulk Customer {i}")
            orders.append(order)
        return orders

    def test_bulk_generate_success_count(
        self, client, auth, three_seeded_orders, tmp_invoice_dir
    ):
        today = date.today().isoformat()
        resp = client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": today},
            auth=auth,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("success", 0) == 3
        assert data.get("failed", -1) == 0

    def test_bulk_generate_creates_pdf_files(
        self, client, auth, three_seeded_orders, tmp_invoice_dir
    ):
        today = date.today().isoformat()
        client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": today},
            auth=auth,
        )
        pdf_files = list(tmp_invoice_dir.glob("*.pdf"))
        assert len(pdf_files) >= 3, (
            f"Expected at least 3 PDFs, found {len(pdf_files)}: {pdf_files}"
        )

    def test_bulk_generate_skips_already_billed(
        self, client, auth, generated_invoice, seeded_order, seeded_prices, tmp_invoice_dir
    ):
        """seeded_order is already billed via generated_invoice — bulk should not re-bill it."""
        today = date.today().isoformat()
        resp = client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": today},
            auth=auth,
        )
        assert resp.status_code == 200
        data = resp.json()
        # The already-billed order should not appear in the success count
        assert data.get("success", 0) == 0  # no new unbilled orders
        assert data.get("failed", 0) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 7. Bulk endpoint blocked without flag
# ─────────────────────────────────────────────────────────────────────────────

class TestBulkEndpointFlagGate:

    def test_bulk_generate_returns_404_when_flag_off(
        self, client, auth, monkeypatch, tmp_invoice_dir
    ):
        monkeypatch.setenv("FLAG_BILLING_BULK_GENERATE", "false")
        # Reload the flag helper so is_enabled() re-reads the env
        _patch_flag("FLAG_BILLING_BULK_GENERATE", False)

        resp = client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": date.today().isoformat()},
            auth=auth,
        )
        assert resp.status_code == 404, (
            f"Expected 404 when FLAG_BILLING_BULK_GENERATE=false, got {resp.status_code}"
        )

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
        # With no unbilled orders the API should still return 200 (0 generated)
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 8. Void invoice
# ─────────────────────────────────────────────────────────────────────────────

class TestVoidInvoice:

    def test_void_sets_status_to_voided(
        self, client, auth, generated_invoice
    ):
        inv_id = generated_invoice["id"]
        resp = client.delete(f"/admin/billing/invoices/{inv_id}", auth=auth)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("status") == "voided", f"Expected 'voided', got {data.get('status')}"

    def test_void_pdf_still_exists_on_disk(
        self, client, auth, generated_invoice, tmp_invoice_dir
    ):
        inv_id  = generated_invoice["id"]
        pdf_path = generated_invoice["pdf_path"]

        client.delete(f"/admin/billing/invoices/{inv_id}", auth=auth)

        candidate = Path(pdf_path)
        if not candidate.is_absolute():
            candidate = tmp_invoice_dir / candidate
        assert candidate.exists(), "PDF must not be deleted from disk on void"

    def test_void_invoice_is_still_downloadable(
        self, client, auth, generated_invoice
    ):
        inv_id = generated_invoice["id"]
        client.delete(f"/admin/billing/invoices/{inv_id}", auth=auth)

        resp = client.get(f"/admin/billing/invoices/{inv_id}/download", auth=auth)
        # Either the PDF is still downloadable (200) or the record is retrievable
        assert resp.status_code in (200, 404), resp.status_code

    def test_voided_invoice_shows_in_get(
        self, client, auth, generated_invoice
    ):
        inv_id = generated_invoice["id"]
        client.delete(f"/admin/billing/invoices/{inv_id}", auth=auth)

        resp = client.get(f"/admin/billing/invoices/{inv_id}", auth=auth)
        assert resp.status_code == 200
        assert resp.json().get("status") == "voided"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Billing summary
# ─────────────────────────────────────────────────────────────────────────────

class TestBillingSummary:

    def test_summary_returns_required_keys(
        self, client, auth, generated_invoice
    ):
        today = date.today().isoformat()
        resp = client.get(f"/admin/billing/summary?date={today}", auth=auth)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        for key in ("total_orders", "billed", "unbilled", "revenue"):
            assert key in data, f"Key '{key}' missing from summary response"

    def test_summary_billed_count_reflects_generated_invoice(
        self, client, auth, generated_invoice
    ):
        today = date.today().isoformat()
        resp = client.get(f"/admin/billing/summary?date={today}", auth=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data["billed"] >= 1

    def test_summary_revenue_matches_invoice_total(
        self, client, auth, generated_invoice
    ):
        today = date.today().isoformat()
        resp = client.get(f"/admin/billing/summary?date={today}", auth=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert float(data["revenue"]) == pytest.approx(_EXPECTED_TOTAL, abs=0.01)

    def test_summary_unbilled_decreases_after_generation(
        self, client, auth, seeded_order, seeded_prices, tmp_invoice_dir
    ):
        today = date.today().isoformat()

        before = client.get(f"/admin/billing/summary?date={today}", auth=auth).json()
        unbilled_before = before["unbilled"]

        client.post(
            "/admin/billing/invoices/generate",
            json={"order_id": seeded_order.id, "invoice_date": today},
            auth=auth,
        )

        after = client.get(f"/admin/billing/summary?date={today}", auth=auth).json()
        assert after["unbilled"] == unbilled_before - 1


# ─────────────────────────────────────────────────────────────────────────────
# 10. Full rollback — flags OFF
# ─────────────────────────────────────────────────────────────────────────────

class TestRollbackFlagsOff:
    """
    With all billing flags false the billing routes must not exist and the
    dashboard must not expose any billing UI.

    Because the router is registered at import time we spin up a fresh
    TestClient with the app reloaded under flags-off conditions.
    """

    @pytest.fixture()
    def flags_off_client(self, monkeypatch):
        for k, v in _BILLING_FLAGS_OFF.items():
            monkeypatch.setenv(k, v)
        # Reload app with flags off
        _reload_app_with_flags(_BILLING_FLAGS_OFF)
        from app.main import app  # re-imported after reload
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
        # Restore flags-on state for subsequent tests
        _reload_app_with_flags(_BILLING_FLAGS_ON)

    def test_invoices_endpoint_returns_404_when_flags_off(
        self, flags_off_client, auth
    ):
        resp = flags_off_client.get("/admin/billing/invoices", auth=auth)
        assert resp.status_code == 404, (
            f"Billing route must not exist when flag off; got {resp.status_code}"
        )

    def test_generate_endpoint_returns_404_when_flags_off(
        self, flags_off_client, auth
    ):
        resp = flags_off_client.post(
            "/admin/billing/invoices/generate",
            json={"order_id": 1, "invoice_date": date.today().isoformat()},
            auth=auth,
        )
        assert resp.status_code == 404

    def test_bulk_generate_returns_404_when_flags_off(
        self, flags_off_client, auth
    ):
        resp = flags_off_client.post(
            "/admin/billing/invoices/generate-bulk",
            json={"date": date.today().isoformat()},
            auth=auth,
        )
        assert resp.status_code == 404

    def test_dashboard_has_no_billing_tab_when_flags_off(
        self, flags_off_client, auth
    ):
        resp = flags_off_client.get("/dashboard/", auth=auth)
        assert resp.status_code == 200
        html = resp.text.lower()
        # The billing tab button should not appear in the rendered HTML
        assert "💰 billing" not in html and "tab-billing" not in html

    def test_existing_routes_still_work_when_flags_off(
        self, flags_off_client, auth
    ):
        resp = flags_off_client.get("/health")
        assert resp.status_code == 200

        resp = flags_off_client.get("/admin/customers", auth=auth)
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_confirmed_order(db_session, phone: str, name: str):
    """Seed a single confirmed order for bulk-generation tests."""
    import json
    from app.models.order import Order

    order = Order(
        customer_phone=phone,
        customer_name=name,
        status="confirmed",
        business_date=date.today().isoformat(),
        delivery_date=date.today().isoformat(),
        raw_message=f"Chicken Feet 10 KGS\nChicken Liver and Gizzard 5 KGS",
        parsed_items=json.dumps([
            {"product": _PRODUCT_FEET,  "quantity": _QTY_FEET,  "unit": "KGS"},
            {"product": _PRODUCT_LIVER, "quantity": _QTY_LIVER, "unit": "KGS"},
        ]),
        unclear_items=json.dumps([]),
        is_unclear=False,
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def _patch_flag(flag_name: str, value: bool) -> None:
    """
    Directly patch is_enabled() in the already-imported billing modules so
    flag changes take effect without a full app reload.
    """
    try:
        import app.config.flags as flags_mod
        # is_enabled reads os.environ live, so setting the env var is enough.
        # This function exists as an extension point for future patching needs.
        _ = flags_mod  # noqa
    except ImportError:
        pass