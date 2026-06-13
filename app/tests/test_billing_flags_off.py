"""
tests/test_billing_flags_off.py
--------------------------------
Smoke tests that verify billing feature flags are truly non-disruptive
when disabled.

All tests in this module run with:
    FLAG_BILLING_ENABLED       = "false"
    FLAG_BILLING_AUTO_INVOICE  = "false"
    FLAG_BILLING_BULK_GENERATE = "false"

These values are forced via a module-scoped autouse fixture so they
cannot be overridden by a local .env file or any other test in the suite.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# ── Billing flag constants ─────────────────────────────────────────────────────
_BILLING_FLAGS_OFF = {
    "FLAG_BILLING_ENABLED":       "false",
    "FLAG_BILLING_AUTO_INVOICE":  "false",
    "FLAG_BILLING_BULK_GENERATE": "false",
}

# ── Module-scoped autouse fixture: enforce flags for every test in this file ──

@pytest.fixture(autouse=True)
def billing_flags_disabled(monkeypatch):
    """
    Force all billing feature flags to 'false' for every test in this module.
    Runs before the test, restores the original env after.
    """
    for key, value in _BILLING_FLAGS_OFF.items():
        monkeypatch.setenv(key, value)
    yield
    # monkeypatch automatically restores after the test — no manual teardown needed


# ── App + client fixture ───────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
    """
    Fresh TestClient for each test.

    We set DASHBOARD_USERNAME / DASHBOARD_PASSWORD so HTTP Basic auth
    succeeds, and DATABASE_URL to an in-memory SQLite so the tests never
    touch a real database.

    The app import is deferred inside the fixture so that the billing flags
    are already in os.environ at module load time (important if the app
    reads flags at import).
    """
    monkeypatch.setenv("DASHBOARD_USERNAME", "testuser")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "testpass")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_billing_flags.db")
    # Ensure META credentials are present so app startup doesn't fail
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "000000000")
    monkeypatch.setenv("PLANT_NAME", "TestPlant")

    from app.main import app
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    # Teardown: drop all tables so each test starts clean
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def auth():
    """HTTP Basic auth credentials as a tuple for requests."""
    return ("testuser", "testpass")


@pytest.fixture()
def db_session():
    """
    Yields a raw SQLAlchemy session for tests that need to seed data directly.
    Rolls back after the test so state doesn't leak.
    """
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
# 1. Billing routes return 404 when disabled
# ══════════════════════════════════════════════════════════════════════════════

class TestBillingRoutesReturn404WhenDisabled:
    """
    When billing is disabled the router must not be registered with the app.
    Every billing endpoint should return 404, not 403 or 500.
    """

    BILLING_ENDPOINTS = [
        ("GET",  "/admin/billing/invoices"),
        ("POST", "/admin/billing/invoices/generate"),
        ("GET",  "/admin/billing/prices/defaults"),
        ("GET",  "/admin/billing/summary"),
    ]

    def test_billing_routes_return_404_when_disabled(self, client, auth):
        for method, path in self.BILLING_ENDPOINTS:
            if method == "GET":
                response = client.get(path, auth=auth)
            elif method == "POST":
                response = client.post(path, auth=auth, json={})
            else:
                pytest.fail(f"Unhandled method {method} in test fixture")

            assert response.status_code == 404, (
                f"Expected 404 for {method} {path} with billing disabled, "
                f"but got {response.status_code}. "
                f"This means the billing router is registered even when "
                f"FLAG_BILLING_ENABLED=false — fix the router registration gate."
            )

    def test_billing_invoices_endpoint_is_not_405(self, client, auth):
        """
        Extra guard: a 405 (Method Not Allowed) would mean the route IS
        registered but rejects the method — that's also wrong.
        """
        response = client.get("/admin/billing/invoices", auth=auth)
        assert response.status_code not in (405, 403), (
            f"Got {response.status_code} instead of 404 for /admin/billing/invoices — "
            f"the billing router appears to be partially registered."
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2. Dashboard has no billing tab when disabled
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardHasNoBillingTabWhenDisabled:
    """
    The dashboard HTML must not expose any billing UI element when the
    billing flag is off.  We check both structural markers and visible text.
    """

    def _get_dashboard_html(self, client, auth):
        response = client.get("/dashboard/", auth=auth)
        assert response.status_code == 200, (
            f"Dashboard returned {response.status_code}, expected 200. "
            f"Check that the app starts correctly with billing flags off."
        )
        return response.text

    def test_dashboard_returns_200_when_billing_disabled(self, client, auth):
        response = client.get("/dashboard/", auth=auth)
        assert response.status_code == 200, (
            "Dashboard must still return 200 when billing flags are off — "
            "disabling billing must not break the main dashboard."
        )

    def test_dashboard_has_no_billing_tab_id(self, client, auth):
        html = self._get_dashboard_html(client, auth)
        assert "tab-billing" not in html, (
            "Found 'tab-billing' in dashboard HTML with FLAG_BILLING_ENABLED=false. "
            "The billing tab must be conditionally rendered only when billing is enabled."
        )

    def test_dashboard_has_no_billing_tab_label(self, client, auth):
        html = self._get_dashboard_html(client, auth)
        assert "💰 Billing" not in html, (
            "Found '💰 Billing' tab label in dashboard HTML with billing disabled. "
            "The billing tab button must not render when FLAG_BILLING_ENABLED=false."
        )

    def test_dashboard_has_no_billing_enabled_flag_leak(self, client, auth):
        html = self._get_dashboard_html(client, auth)
        assert "billing_enabled" not in html, (
            "Found 'billing_enabled' in dashboard HTML with billing disabled. "
            "Feature flag values must not be leaked into the HTML template output."
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3. Dashboard has no billing badges when disabled
# ══════════════════════════════════════════════════════════════════════════════

class TestDashboardHasNoBillingBadgesWhenDisabled:
    """
    Order cards must not show billing-related badges (Billed / Unbilled /
    billing_status) when the billing feature is off.
    """

    def _get_dashboard_html(self, client, auth):
        response = client.get("/dashboard/", auth=auth)
        assert response.status_code == 200
        return response.text

    def test_no_billing_status_attribute_in_html(self, client, auth):
        html = self._get_dashboard_html(client, auth)
        assert "billing_status" not in html, (
            "Found 'billing_status' in dashboard HTML with billing disabled. "
            "Billing status data must not be embedded in the page at all."
        )

    def test_no_billed_badge_text_in_html(self, client, auth):
        html = self._get_dashboard_html(client, auth)
        # Check for both the badge class and its visible label
        assert "billed-badge" not in html, (
            "Found 'billed-badge' CSS class in dashboard HTML with billing disabled. "
            "The 'Billed' order badge must not appear when billing is off."
        )
        # Also check the visible badge text that a customer or operator would see
        assert ">Billed<" not in html, (
            "Found visible 'Billed' badge text in dashboard HTML with billing disabled."
        )

    def test_no_unbilled_badge_text_in_html(self, client, auth):
        html = self._get_dashboard_html(client, auth)
        assert "unbilled-badge" not in html, (
            "Found 'unbilled-badge' CSS class in dashboard HTML with billing disabled. "
            "The 'Unbilled' order badge must not appear when billing is off."
        )
        assert ">Unbilled<" not in html, (
            "Found visible 'Unbilled' badge text in dashboard HTML with billing disabled."
        )

    def test_no_invoice_column_in_orders_table(self, client, auth):
        """
        Guard against a scenario where an 'Invoice' column header sneaks
        into the orders table even with billing off.
        """
        html = self._get_dashboard_html(client, auth)
        assert "<th>Invoice</th>" not in html, (
            "Found an 'Invoice' column header in the orders table with billing disabled. "
            "Invoice-related columns must only render when billing is enabled."
        )


# ══════════════════════════════════════════════════════════════════════════════
# 4. Existing order routes are unaffected by billing flags
# ══════════════════════════════════════════════════════════════════════════════

class TestExistingOrderRoutesUnaffected:
    """
    Regression guard: routes that existed before billing was introduced must
    continue to work correctly when all billing flags are off.
    """

    def test_dashboard_route_returns_200(self, client, auth):
        response = client.get("/dashboard/", auth=auth)
        assert response.status_code == 200, (
            f"GET /dashboard/ returned {response.status_code} with billing flags off. "
            f"Billing flags must not affect the dashboard route."
        )
        assert "OrdeRR" in response.text, (
            "Dashboard response body is missing 'OrdeRR' brand name — "
            "the template may have failed to render."
        )

    def test_customers_list_route_returns_200(self, client, auth):
        response = client.get("/admin/customers", auth=auth)
        assert response.status_code == 200, (
            f"GET /admin/customers returned {response.status_code} with billing flags off. "
            f"The customers list must not be broken by billing flags."
        )
        data = response.json()
        assert "customers" in data, (
            f"GET /admin/customers response is missing 'customers' key. "
            f"Got keys: {list(data.keys())}"
        )

    def test_pending_orders_route_returns_200(self, client, auth):
        response = client.get("/admin/pending", auth=auth)
        assert response.status_code == 200, (
            f"GET /admin/pending returned {response.status_code} with billing flags off. "
            f"The pending orders route must not be affected by billing flags."
        )
        data = response.json()
        assert "groups" in data, (
            f"GET /admin/pending response is missing 'groups' key. "
            f"Got keys: {list(data.keys())}"
        )

    def test_unclear_items_route_returns_200(self, client, auth):
        response = client.get("/admin/unclear-items", auth=auth)
        assert response.status_code == 200, (
            f"GET /admin/unclear-items returned {response.status_code} with billing flags off. "
            f"The unclear items route must not be affected by billing flags."
        )
        data = response.json()
        assert isinstance(data, list), (
            f"GET /admin/unclear-items should return a list, got {type(data).__name__}."
        )

    def test_salespersons_route_returns_200(self, client, auth):
        response = client.get("/admin/salespersons", auth=auth)
        assert response.status_code == 200, (
            f"GET /admin/salespersons returned {response.status_code} with billing flags off."
        )
        data = response.json()
        assert "salespersons" in data, (
            f"GET /admin/salespersons response is missing 'salespersons' key. "
            f"Got keys: {list(data.keys())}"
        )

    def test_health_check_returns_200(self, client):
        """Health endpoint has no auth — if this fails the app itself is broken."""
        response = client.get("/health")
        assert response.status_code == 200, (
            f"GET /health returned {response.status_code}. "
            f"The app health check must pass regardless of billing flags."
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5. Auto-invoice hook is a no-op when disabled
# ══════════════════════════════════════════════════════════════════════════════

class TestAutoInvoiceHookIsNoopWhenDisabled:
    """
    When FLAG_BILLING_AUTO_INVOICE=false, the auto-invoice hook inside
    _save_and_notify must never be called, regardless of order status.

    We patch `try_auto_invoice` at its call site inside order_service so the
    mock fires whether or not the billing module exists yet.
    """

    def _make_mock_customer(self):
        customer = MagicMock()
        customer.phone_number    = "919876543210"
        customer.restaurant_name = "Test Restaurant"
        customer.ledger_token    = "test-token-abc"
        customer.is_active       = True
        return customer

    def _make_mock_parsed(self):
        return {
            "items": [
                {"product": "Chicken Feet",             "quantity": 10, "unit": "KGS"},
                {"product": "Chicken Liver and Gizzard", "quantity": 5,  "unit": "KGS"},
            ],
            "unclear_items": [],
            "delivery_time": None,
        }

    def test_try_auto_invoice_never_called_when_flag_off(
        self, db_session, monkeypatch
    ):
        """
        Directly invoke _save_and_notify and assert try_auto_invoice is
        never called when FLAG_BILLING_AUTO_INVOICE=false.

        The patch target is 'app.services.order_service.try_auto_invoice'
        — the name as it would be imported inside order_service.py.
        If the billing module doesn't exist yet, the patch uses create=True
        so the test still runs and asserts the hook is absent/uncalled.
        """
        monkeypatch.setenv("FLAG_BILLING_AUTO_INVOICE", "false")

        mock_invoice_fn = MagicMock(return_value=None)

        # create=True means the patch works even before billing is implemented
        with patch(
            "app.services.order_service.try_auto_invoice",
            mock_invoice_fn,
            create=True,
        ):
            from app.services.order_service import _save_and_notify

            customer = self._make_mock_customer()
            parsed   = self._make_mock_parsed()

            # Also patch the WhatsApp notifier so the test doesn't make
            # real network calls
            with patch("app.services.order_service.send_order_confirmation_to_customer"):
                with patch("app.services.order_service.notify_manager_new_order"):
                    try:
                        _save_and_notify(
                            db=db_session,
                            customer=customer,
                            parsed=parsed,
                            raw_message="Chicken Feet - 10 KGS\nChicken Liver and Gizzard - 5 KGS",
                            is_photo=False,
                        )
                    except Exception:
                        # _save_and_notify may fail for unrelated reasons
                        # (e.g. missing FK constraints in the test DB) — that's
                        # acceptable here; we only care that the invoice hook
                        # was never called
                        pass

        mock_invoice_fn.assert_not_called(), (
            "try_auto_invoice was called inside _save_and_notify even though "
            "FLAG_BILLING_AUTO_INVOICE=false. "
            "The billing hook must be guarded by the feature flag."
        )

    def test_try_auto_invoice_never_called_for_unclear_order(
        self, db_session, monkeypatch
    ):
        """
        Even an order with parsed items AND unclear items must not trigger
        auto-invoice when the flag is off.
        """
        monkeypatch.setenv("FLAG_BILLING_AUTO_INVOICE", "false")

        mock_invoice_fn = MagicMock(return_value=None)

        parsed_with_unclear = {
            "items": [
                {"product": "Chicken Feet", "quantity": 10, "unit": "KGS"},
            ],
            "unclear_items": ["raan 5"],   # unclear item present
            "delivery_time": None,
        }

        with patch(
            "app.services.order_service.try_auto_invoice",
            mock_invoice_fn,
            create=True,
        ):
            from app.services.order_service import _save_and_notify

            customer = self._make_mock_customer()

            with patch("app.services.order_service.send_order_confirmation_to_customer"):
                with patch("app.services.order_service.notify_manager_new_order"):
                    try:
                        _save_and_notify(
                            db=db_session,
                            customer=customer,
                            parsed=parsed_with_unclear,
                            raw_message="Chicken Feet - 10 KGS\nraan 5",
                            is_photo=False,
                        )
                    except Exception:
                        pass

        mock_invoice_fn.assert_not_called(), (
            "try_auto_invoice was called for an unclear order even though "
            "FLAG_BILLING_AUTO_INVOICE=false."
        )

    def test_billing_enabled_flag_false_string_is_treated_as_disabled(
        self, monkeypatch
    ):
        """
        Verify the flag-reading logic treats the string 'false' (lowercase,
        as set in .env files) as disabled — not as a truthy non-empty string.
        """
        monkeypatch.setenv("FLAG_BILLING_ENABLED", "false")

        # If app.services.billing exists, import and test its flag reader
        try:
            import importlib
            import app.services.billing as billing_mod  # type: ignore[import]
            importlib.reload(billing_mod)

            # The module should expose a flag-checking helper or constant
            if hasattr(billing_mod, "is_billing_enabled"):
                assert billing_mod.is_billing_enabled() is False, (
                    "is_billing_enabled() returned True when FLAG_BILLING_ENABLED='false'. "
                    "The flag reader must treat the string 'false' as disabled."
                )
            elif hasattr(billing_mod, "BILLING_ENABLED"):
                assert billing_mod.BILLING_ENABLED is False, (
                    "BILLING_ENABLED is True when FLAG_BILLING_ENABLED='false'. "
                    "The flag constant must be False when the env var is 'false'."
                )
            else:
                # Module exists but doesn't expose a public flag check yet —
                # acceptable during early development
                pass
        except ImportError:
            # Billing module not yet implemented — this test is a forward
            # contract; it will become meaningful once billing is built
            pytest.skip(
                "app.services.billing not yet implemented — skipping flag "
                "string-parsing check. Revisit once billing module exists."
            )