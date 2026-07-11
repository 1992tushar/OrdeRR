# 5-Day Close & Audit — Requirements

**Status:** BUILT — P1+P2+P3 implemented & verified against real orderr.db
(full-app TestClient boot), left uncommitted for review 2026-07-12. Written 2026-07-11.
Files: `services/close_service.py`, `models/close_period.py`,
`templates/dashboard_analytics_close.html` (+route in `routes/dashboard.py`,
link in `_analytics_subnav.html`). Screen: Analytics → 🧮 5-Day Close.
**Sibling spec:** `ANALYTICS_REQUIREMENTS.md` (analytics initiative, complete).
**Related artifact:** manual worksheet published as the interim bridge (fillable/printable HTML).

---

## 1. Purpose & premise

Every ~5 days the business runs a mini period-close covering **sales, purchases,
expenses, money received and money paid**. Source operations happen on WhatsApp
across several groups (2 order groups — all-area + Lonavala; an expenses group; a
purchase group per supplier; an accounts group).

**Premise that makes automation possible:** the data already lands in structured
form.

- **Orders / sales** flow into OrdeRR automatically via the WhatsApp webhook
  (`inbound_messages` → parsed → `orders` → `invoices`).
- **Purchases, expenses, receipts, payments, AR/AP** are all entered into **Vasy**
  (confirmed by owner, 2026-07-11) and mirrored into OrdeRR by the Vasy import
  framework (`import_logs`, `vasy_*`, `customer_receipts`, `outstanding_snapshots`).

Therefore the close can be **almost entirely self-computed**. The **only** number a
human must key is the **physically counted cash in hand**.

### Guiding principle
The close proves **three identities**. If all three tie, the books are clean; a gap
is exactly the size of what's missing and points at the stream to investigate.
**Cash is the master check** — every other stream flows through it.

---

## 2. Hard boundary (what can and cannot be automated)

| Stream | Structured source | Self-audit? |
|---|---|---|
| Sales / orders | `orders`, `invoices` (WhatsApp webhook) | ✅ |
| Debtors / collections | `outstanding_snapshots`, `customer_receipts` (Vasy) | ✅ |
| Purchases / creditors | `vasy_purchases`, `vasy_supplier_bills` (Vasy) | ✅ |
| Expenses | `vasy_expenses` (Vasy) | ✅ |
| Money paid out | `vasy_payments` (Vasy) | ✅ |
| **Cash in hand / staff float** | none — physical | ❌ manual count |

**"Automatic" = automatic *after* the Vasy import.** OrdeRR does not pull from Vasy
live; the owner exports and imports. The close is only as current as the last
import → §6 staleness gate is mandatory.

---

## 3. The three tie-outs

All amounts filtered to the close window `[from, to]` on the **event date**
(delivery / receipt / bill / payment date), never the message/import date.

### A · Debtors (AR)
```
Opening debtors + Sales billed − Collections = Expected closing debtors
                                              → compare to actual closing
```
- **Opening / Closing**: `outstanding_snapshots.opening_balance` / `.closing`
  aggregated across parties. Use the snapshot at/just-before `from` for opening and
  at/just-after `to` for closing. Note the snapshot already stores `debit` (charges)
  and `credit` (payments) for its own period — use for cross-check, but the close
  window may not align to a snapshot period, so also compute movement independently.
- **Sales billed (this window)**: Σ `invoices.total` where `business_date ∈ window`
  and `status != 'void'`. *(OrdeRR-side — see §5 source-of-truth.)*
- **Collections**: Σ `customer_receipts.amount` where `receipt_date ∈ window`.
- **Tie check**: `closing − (opening + sales − collections) ≈ 0`.

### B · Creditors (AP)
```
Opening creditors + Purchases − Supplier payments = Expected closing creditors
```
- **Closing creditors** (preferred, true AP): Σ `vasy_supplier_bills.due` for bills
  open as of `to`. `vasy_supplier_bills` carries `due_date`/`status` so this is real,
  not a proxy.
- **Purchases (this window)**: Σ `vasy_purchases.total` where `bill_date ∈ window`.
- **Supplier payments**: Σ `vasy_payments.amount` where `payment_date ∈ window`
  (exclude any payment identifiable as an expense head if separable; else treat all
  money-out here and let the cash tie-out catch mis-splits).
- **Opening creditors**: closing AP as of `from` (same query, earlier date) — or the
  prior close's stored closing (§4 sign-off).

### C · Cash — the master check
```
Opening cash + Cash collections − Cash supplier pay − Cash expenses − Drawings
             = Expected cash in hand → compare to COUNTED cash (manual)
```
- **Cash collections**: Σ `customer_receipts.amount` where `mode = 'cash'` and
  `receipt_date ∈ window`.
- **Cash payments out**: Σ `vasy_payments.amount` where `mode = 'cash'` and
  `payment_date ∈ window`.
- **Cash expenses**: from `vasy_expenses.paid` where `expense_date ∈ window` *if* the
  expense was cash-settled — Vasy expense export has no mode column, so **flag this
  as a modelling gap**: either (a) treat all `vasy_payments mode=cash` as the single
  cash-out figure and don't double-count expenses that were paid via a payment
  voucher, or (b) confirm with owner how cash expenses are recorded. Resolve before
  build.
- **Opening cash**: prior close's stored closing counted cash (§4).
- **Counted cash + Drawings/other**: the two manual inputs.
- **Tie check**: `counted − expected ≈ 0`. This is the lie detector.

---

## 4. Screen behaviour (the flow)

**Interaction model (confirmed by owner 2026-07-11):** one "Start 5-Day Close" button
→ freshness check → asks only a few questions (confirm window, counted cash,
drawings) → everything else auto-computes → owner reviews the flagged exceptions →
sign off. Not "click and walk away"; the machine does the number-crunching and
problem-finding, the human counts cash and judges exceptions.

1. **Pick window** — from/to dates. Default: day after last close → today.
2. **Freshness gate** (§6) — show per-entity last import; block/​warn if stale.
3. **Auto-compute** the three tie-outs from the queries above; render each with a
   live gap (green = ties, red = off-by-exactly-the-gap). Mirror the worksheet.
4. **Exceptions list** (§7) — auto-generated; the owner reviews *only* these.
5. **Manual inputs** — counted cash, drawings/other-out, opening cash on first-ever
   run. Everything else is read-only computed.
6. **Sign-off** — persist a `ClosePeriod` row: window, the three closing balances
   (debtors, creditors, cash), gaps at sign-off, and carried-forward exceptions.
   Next window's opening auto-fills from this. Idempotent per window.

New table (proposed): `close_periods` — `id, from_date, to_date, closing_debtors,
closing_creditors, closing_cash_counted, cash_gap, ar_gap, ap_gap, exceptions_json,
signed_by, signed_at`. Read-only history, like snapshots.

---

## 5. Sales source-of-truth decision (must settle before build)

Two independent "sales" figures exist:
- **OrdeRR invoices** (`invoices.total`, from parsed WhatsApp orders), and
- **Vasy** billing, reflected in `outstanding_snapshots.debit` and/or the Vasy
  sales-invoice export (`vasy_invoice` / `vasy_sales_item`).

**Decision:** OrdeRR invoices drive the **operational exceptions** (delivered-not-
billed, etc. — §7). Vasy drives the **financial AR tie-out** (§3A). The close then
**reconciles the two** — `Σ OrdeRR invoices in window` vs `Vasy AR debit / Vasy sales
in window` — and surfaces the difference as its own audit line. Agreement is the
proof both systems captured the same sales; divergence is a real finding (an order
billed in OrdeRR but not in Vasy, or vice-versa). Do **not** sum both into one total.

---

## 6. Freshness gate (critical)

Because the close depends on imported Vasy data, before computing show, per entity
(`receipts`, `outstanding`, `purchases`, `supplier_bills`, `expenses`, `payments`):
- last `import_logs.imported_at` for that entity, and
- whether it covers through `to` (best-effort: newest event date present ≥ `to`).

If any entity's latest import predates `to`, **warn prominently** ("Expenses last
imported 3 days ago — figures may be incomplete; import the latest Vasy export before
signing off"). Never present a stale close as authoritative.

---

## 7. Auto-generated exceptions (the payoff)

Replaces "rebuild totals from chat" with "review ~10 flagged items." Each query
scoped to the window.

1. **Delivered but not invoiced** — `orders.status='delivered'`,
   `is_cancelled=false`, no matching `invoices.order_id`. *(Revenue leak.)*
2. **Ordered but not delivered** — `orders` in window whose status never reached
   `delivered`/`cancelled` (stuck: received/confirmed/packed). *(Pending/lost.)*
3. **Unparsed / unclear** — `orders.is_unclear=true`, plus `rate_unclear`,
   `ocr_unmatched`, and `inbound_messages.processing_status` failed/stuck in window.
   *(Orders that never made it into a total.)*
4. **Invoice with no delivery** — `invoices` in window whose `order_id` is missing/
   not `delivered`. *(Billed something not delivered.)*
5. **Unattributed money** — `customer_receipts.customer_id IS NULL` (and outstanding
   parties with `customer_id NULL`) in window. *(Cash-customer/walk-in receipts to
   confirm.)*
6. **AR / AP / cash gaps** — any of the three tie-outs not ≈ 0.
7. **Sales mismatch** — OrdeRR-vs-Vasy sales difference from §5.
8. **Unpaid expenses / overdue bills** — `vasy_expenses.unpaid > 0`,
   `vasy_supplier_bills.status='overdue'` due within/at window.

---

## 8. WhatsApp guardrails (informational, shown on screen)

Carried from the manual worksheet; these are the human traps the data can't fully
police:
1. Two order groups → merge before counting; kill all-area/Lonavala overlap.
2. Untyped orders (photo/voice/verbal) never reach a total — see exception #3.
3. Message date ≠ event date — always audit on delivery/payment date.
4. Staff cash float — track as its own bucket; it's neither yours nor a debtor.
5. Edited/deleted WhatsApp messages — recorded record is truth, chat is only source.

---

## 9. Open items to resolve before build

- [ ] **Cash-expense modelling** (§3C) — how are cash expenses recorded in Vasy; avoid
      double-counting against `vasy_payments`.
- [ ] **Snapshot alignment** (§3A) — close windows won't align to Vasy snapshot
      periods; confirm opening/closing snapshot selection rule.
- [ ] **Sales source-of-truth** (§5) — confirm OrdeRR-drives-ops / Vasy-drives-AR.
- [ ] **Window date types** — `orders.business_date`/`delivery_date` are `YYYY-MM-DD`
      strings; `invoices.business_date` and all `vasy_*` dates are `Date`. Normalize
      in the query layer.
- [ ] **Fixed supplier list** — owner to provide (drives a "supplier not seen this
      window" check and the worksheet tick-list).

---

## 10. Build phasing (proposed)

- **P0 — Interim:** manual worksheet (done) bridges until the screen ships.
- **P1 — Read-only close:** ✅ DONE — window picker + freshness gate + three
  tie-outs + sales recon + exceptions list, from existing tables.
- **P2 — Sign-off & history:** ✅ DONE — `close_periods` table, POST sign-off
  (server-recomputed, PRG), opening cash auto-fills from prior close, default
  window starts day after last close, "Recent closes" history.
- **P3 — Polish:** ✅ DONE — printable close report (print CSS + button + filed
  header, signed windows reload stored cash), drill-down links from exceptions to
  the relevant analytics pages, and a derived supplier-activity check (usual
  suppliers with no purchase this window) pending the configurable fixed list.

**Remaining (not built):** configurable fixed-supplier list (currently derived
from 30-day history); cash-expense modelling confirmation (§9); true historical
opening-AP tie (needs an AP snapshot — supplier_bills stores only current due).
