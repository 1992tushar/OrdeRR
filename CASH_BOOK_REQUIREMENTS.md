# Cash Book — Requirements

**Status:** DRAFT for owner review — written 2026-07-13, nothing built.
**Sibling specs:** `FIVE_DAY_CLOSE_REQUIREMENTS.md` (built — cash is its master
check), `REGISTERS_REMINDERS_REQUIREMENTS.md` (built), `ANALYTICS_REQUIREMENTS.md`.

---

## 1. Purpose & premise

The owner wants **one place where every cash movement appears** — cash
received, cash expenses, cash supplier payments, drawings — like a bank
passbook but for the cash drawer, with a running balance per day.

The 5-Day Close already proves cash *per period* (opening + cash in − cash out
= counted). The Cash Book is the **daily lens on the same numbers**: when the
close finds a gap, the day pages show exactly which day the drawer diverged.
Vasy remains the money source-of-truth; the Cash Book is a *view* over the
imported mirrors plus a handful of manual lines Vasy never sees.

---

## 2. Key data findings (verified 2026-07-13, local mirror)

1. **The payments export already covers expense payments.** 476 of 500
   `vasy_expenses` rows have a party+date+amount twin in `vasy_payments`
   (BHARAI, DESIEL, CNG, Ice…) — Vasy books an expense's payment as a payment
   voucher, and the payment export carries `mode` (467/500 = cash, 32 online,
   1 cheque). **Consequence: `vasy_payments` mode='cash' is the single
   cash-out source.** Do NOT also add expense rows — that double-counts.
2. **The Expense Register report has a hidden "Payment Data" column**
   (Columns → Payment Data), and its export carries it — sample verified:
   header `Payment Data`, values `bank : 300` / `Cash : 1200`; header row 3,
   date DD/MM/YYYY, trailing Total row. This gives *per-expense* mode — the
   right role is **cross-check** (P2 §6): find expense cash with no matching
   payment voucher (a drawer leak the single-source model would miss).
3. **Cash sales / walk-ins**: the receipt report (/receipt) carries mode
   cash/bank (owner confirmed) and is already imported into
   `customer_receipts.mode`. A walk-in paying cash lands there (unmatched
   party ⇒ `customer_id NULL`) — cash-in is fully covered by receipts.

---

## 3. Ledger composition

Each day page is built from:

| Line source | Direction | From |
|---|---|---|
| Cash receipts (incl. walk-ins) | IN | `customer_receipts` where mode='cash', by receipt_date |
| Cash payments (suppliers + expenses) | OUT | `vasy_payments` where mode='cash', by payment_date |
| Manual lines | IN/OUT | new `cash_entries` table (below) |

**Manual line types** (`cash_entries`): `drawing` (owner took cash),
`bank_deposit` (cash moved to bank), `float_given` / `float_returned` (staff
float), `opening_set` (first-ever opening), `adjustment` (correction, note
mandatory), `other`. Columns: `id, entry_date, direction (in/out), type,
amount, note, created_at`. Manual entries are the ONLY writes this feature
makes — everything else is read-only computed.

**Opening balance**: the last signed 5-Day Close's `closing_cash_counted`
(that's the last physically-verified number), then day-by-day roll-forward.
Before any close exists: one `opening_set` manual entry.

**Running balance**: `closing(d) = closing(d−1) + in(d) − out(d)`, computed
from the anchor forward. Non-cash modes (online/cheque/bank) are ignored —
this is the drawer, not the bank.

---

## 4. Screen — 💵 Cash Book

Analytics subnav (money family, next to 🧮 5-Day Close):
`/dashboard/analytics/cashbook`.

- **Day page** (default = today): opening → chronological lines (receipts
  named by customer/party, payments by vendor, manual lines tagged) → totals
  in/out → closing. Date navigation like the orders dashboard.
- **Month strip**: per-day in / out / closing sparkrow; click a day to open it.
- **Add manual line** — inline form (type, amount, note); mobile-first.
- **Spot count** (P2): "I counted ₹X in the drawer now" → variance vs computed
  closing, shown on the day; a red variance is the early version of the
  close-day lie detector.
- **Freshness gate** (mandatory, same as close): show last import per entity
  (receipts, payments); warn when the page's day is newer than the imports —
  never present a stale drawer as authoritative.

---

## 5. 5-Day Close integration

- Close reads the same sources, so figures agree by construction.
- Cash Book **anchors on** each signed close (counted cash), and the close
  screen links to the day pages of its window ("which day went wrong").
- The close's flagged cash-expense modelling gap (§9 of its spec) is resolved
  by finding #1: cash-out = payment vouchers, single source, no double-count.

---

## 6. P2 — Expense Register cross-check import

New import format (optional upload on the Imports page): Expense Register
**with Payment Data column** (§2.2 sample). Parser: header row 3, DD/MM/YYYY
dates, `Payment Data` segments `mode : amount` (case-insensitive; tolerate
multiple comma-separated segments for split payments), skip trailing Total
row. Extends `vasy_expenses` with `cash_paid` / `noncash_paid` (migration for
existing DBs).

Payoff — two exception lists on the Cash Book screen:
1. **Expense cash without a payment voucher** — expense says `Cash : X` but no
   matching cash payment exists → drawer money left without a voucher.
2. **Mode mismatch** — expense says bank, matching payment voucher says cash
   (or vice-versa) → one of the two entries is wrong in Vasy.

Matching key: party+date+amount first, then party+date with amount tolerance;
unmatched shown, never guessed.

---

## 7. Scope fences

- ❌ Not a ledger — no balances live here; Vasy is truth, this is a view +
  manual non-Vasy lines only.
- ❌ Sundries register lines are NOT added to the Cash Book (they may also be
  Vasy expenses → double-count risk; owner keeps sundries standalone).
- ❌ No bank-side tracking — drawer only. (Bank statement recon already
  exists in the 5-Day Close via `bank_transactions`.)

---

## 8. Open questions before build

- [ ] **8.1 The 24 unmatched expenses** — expenses with no payment-voucher twin:
      partially paid / unpaid / data entry gap? Verify on real data at P2; P1 is
      unaffected (payments are the source either way).
- [ ] **8.2 Split payments** — what does Payment Data show for an expense paid
      part-cash part-bank? Parser will tolerate multiple segments; confirm with
      a real example when one occurs.
- [x] **8.3 Drawings today** — **DECIDED (owner, 2026-07-13): not recorded
      anywhere.** Manual `drawing` lines are therefore safe (no double-count)
      and become the only record of drawings.
- [ ] **8.4 Opening cash** — the physically counted drawer amount on go-live
      day (one number, entered once).

---

## 9. Build phasing (proposed)

- **P1 — The book:** `cash_entries` table + Cash Book screen (day pages,
  month strip, manual lines, running balance anchored on last signed close,
  freshness gate). Uses ONLY existing imports — no importer changes.
- **P2 — Trust:** spot-count with variance; Expense Register cross-check
  import (§6) + the two exception lists; close-screen links to day pages.
- **P3 — Polish:** digest line ("expected drawer tonight: ₹X"), CSV export,
  walk-in/unmatched-party labelling.
