# OrdeRR — Automated Test Suite

## Structure

```
tests/
├── conftest.py                  ← Shared fixtures (DB, mocks, test customers)
├── test_parser_unit.py          ← Unit tests for template_parser.py (50+ tests)
├── test_customer_service.py     ← Unit tests for customer_service.py
├── test_validate_name.py        ← Unit tests for validate_restaurant_name()
├── test_onboarding.py           ← Integration tests for onboarding flow
├── test_order_flow.py           ← Integration tests for order/cancel/repeat/replace
└── test_pending_orders.py       ← Integration tests for pending_orders.py
```

## Setup

Install dependencies:
```bash
pip install pytest pytest-mock --break-system-packages
```

Place the `tests/` folder in your project root:
```
OrdeRR/
├── app/
├── tests/         ← here
└── requirements.txt
```

## Running Tests

Run all tests:
```bash
pytest tests/ -v
```

Run specific file:
```bash
pytest tests/test_parser_unit.py -v
```

Run specific test:
```bash
pytest tests/test_parser_unit.py::TestMatchProduct::test_cc_curry_cut -v
```

Run with coverage:
```bash
pip install pytest-cov --break-system-packages
pytest tests/ --cov=app --cov-report=term-missing
```

## Key Design Decisions

- **In-memory SQLite** — no Render DB needed, each test gets a fresh DB
- **WhatsApp mocked** — `send_whatsapp_message` patched in all integration tests,
  no real messages sent during testing
- **Function-scoped DB fixture** — every test starts with a clean slate
- **No .env needed** — test env vars set in conftest.py

## Test Coverage by File

| File | Tests | Covers |
|---|---|---|
| test_parser_unit.py | ~50 | All 15 products, shortcodes, Hindi/Marathi, edge cases |
| test_customer_service.py | 10 | Phone normalisation, lookup, create |
| test_validate_name.py | 18 | Valid/invalid restaurant names |
| test_onboarding.py | 12 | New customer, name validation, registration |
| test_order_flow.py | 16 | Place, cancel, repeat, replace flows |
| test_pending_orders.py | 8 | Pending logic, grouping, edge cases |
