# scripts/local — Local-only test scripts

These scripts run against your **local** database only and are never deployed.
Run them from the project root with your `.env` loaded.

## seed_test_order.py

Creates a confirmed test order for FAIZ KHATIK (Chicken Feet 10 KGS + Chicken Liver and Gizzard 5 KGS):

```bash
python scripts/local/seed_test_order.py
# override defaults:
python scripts/local/seed_test_order.py --customer-name "Hotel Delicious" --phone 919999999999
```

## test_auto_invoice.py

Triggers the auto-invoice pipeline for an existing order ID.
Use `--set-prices` to seed product prices before invoicing (required for a clean first run):

```bash
python scripts/local/test_auto_invoice.py --order-id 42 --set-prices
# skip price seeding if prices are already in the DB:
python scripts/local/test_auto_invoice.py --order-id 42
```

## Typical end-to-end flow

```bash
python scripts/local/seed_test_order.py                          # prints: Seeded order id=42
python scripts/local/test_auto_invoice.py --order-id 42 --set-prices
```
