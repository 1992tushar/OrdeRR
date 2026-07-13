# Registers & Reminders — Requirements

**Status:** APPROVED with owner answers 2026-07-13 (standalone from Vasy; notify
via digest AND WhatsApp; owner to seed dates/old notes). Written 2026-07-13, nothing built.
**Sibling specs:** `ANALYTICS_REQUIREMENTS.md` (complete), `FIVE_DAY_CLOSE_REQUIREMENTS.md` (built).

---

## 1. Purpose & premise

Three gaps, one common failure mode: **things that live only in someone's head
(or a WhatsApp scroll) and get forgotten.**

| # | Gap | Owner's example |
|---|---|---|
| A | **Sundry purchases** — small non-trade buying | cleaning material, carry bags, pens, paper… |
| B | **Critical notes** — one-off facts with money or consequences attached | 01 Apr: salesperson took ₹20,000 from a customer, said he'd return it — nobody chased, everybody forgot |
| C | **Important dates** — renewals & recurring maintenance | insurance, vehicle servicing, licenses, AMCs… |

**Product promise: the system never forgets.** Every entry can carry a
follow-up date and stays visibly OPEN — nagging on the dashboard and in the
daily digest — until a human explicitly closes it. A reminder that fires once
and disappears is as good as no reminder; these must repeat until resolved.

### Guiding principles

1. **Vasy stays the money source-of-truth.** None of these registers is a
   second ledger. A critical note about ₹20,000 is a *memory with a nag*, not
   an AR entry; the actual money movement still gets recorded in Vasy when it
   happens. The sundries register is an *item-level detail layer* Vasy doesn't
   have (`vasy_expenses` is header-level — Expense No. / party / total — with
   no item, qty or rate).
2. **Capture must be effortless.** The owner should be able to add an entry
   from his phone in under 30 seconds, or it won't happen. Mobile-first forms,
   almost every field optional.
3. **One attention feed.** All three features share a single "needs attention"
   surface (§6) sorted by urgency — not three separate places to check.

---

## 2. Feature A — Sundries purchase register

### Questions it must answer
- What did we pay for carry bags last time, and from whom? (price memory —
  catches a vendor quietly raising rates)
- When did we last buy X? Are we probably due again?
- What do sundries cost us per month, by category?

### Data model (proposed)

`sundry_items` — the catalogue (grows organically as entries are made):
`id, name, category (cleaning / packaging / stationery / kitchen / other),
unit (pkt/kg/pcs/…), typical_gap_days (nullable — auto-learned, see nudge), is_active`

`sundry_purchases` — one row per buy:
`id, item_id, purchase_date, qty, rate, amount, vendor (free text),
paid_via (cash / bank / other), note, created_at`

### Screens
- **Register list** — per item: last price, last vendor, last bought, average
  buying cadence, month-to-date spend; "probably due" flag.
- **Add entry** — pick/create item, amount mandatory, everything else optional.
- **Item drill-down** — full price/vendor history (the price-memory payoff).
- **Monthly view** — spend by category, month over month.

### Reorder nudge (advisory only — this is NOT stock tracking)
If an item has been bought at a fairly regular cadence (≥3 purchases, learned
`typical_gap_days`) and the last purchase is older than cadence + 25% grace,
flag "probably due" in the register and the attention feed. No stock counts,
no consumption math — cadence memory only.

### Boundary with Vasy — DECIDED (owner, 2026-07-13)
**Standalone register — no sync, no link, no reconciliation to Vasy.** The
register is OrdeRR's own record of sundry buying (item detail + price memory);
whatever the owner does or doesn't enter in Vasy is out of scope. It holds no
balances, so there is nothing to double-count.

---

## 3. Feature B — Critical notes (the don't-forget ledger)

The ₹20,000 example dissected — a note needs to hold:
**who** (salesperson AND/OR customer), **what** (free text), **how much**
(optional amount), **when it happened** (event date), **when to chase**
(follow-up date), **status**.

### Data model (proposed)

`critical_notes`:
`id, note (text, mandatory), amount (nullable), customer_id (nullable FK),
employee_or_salesperson (nullable — link or free text), event_date,
follow_up_date (nullable), priority (normal / high),
status (open / resolved / dropped), resolution_note, created_at, resolved_at`

### Behaviour — the whole point is the nag
- **Open notes never disappear.** Listed on the Reminders screen always; once
  `follow_up_date` passes they turn **overdue** (red) and enter the attention
  feed + daily manager digest *every day* until actioned.
- **Resolving requires a resolution note** ("recovered ₹20,000 on 12 Jul, in
  cash, deposited") — the audit trail of what actually happened. `dropped`
  (written off / no longer relevant) also requires a note.
- **Snooze** = push the follow-up date, one tap (+7d / +30d / pick date).
- **Customer-linked notes surface in context**: shown on that customer's
  analytics profile page, and (P2) as a warning chip when posting an order for
  that customer — same pattern as the existing credit gate.

### Explicit non-goal
A note with an amount does **not** touch AR, receivables, or any analytics
number. If the ₹20,000 should reduce a customer's balance, that correction
happens in Vasy; the note just makes sure someone remembers to do it.

---

## 4. Feature C — Important dates (renewals & recurring)

### Data model (proposed)

`important_dates`:
`id, title, category (insurance / vehicle service / license / AMC /
subscription / other), due_date, recurrence (none / every N days / monthly /
quarterly / yearly), advance_rule (anniversary | from_done — see below),
lead_days (default 15), linked_to (free text — which vehicle/policy/asset),
amount_estimate (nullable), note, status (active / paused),
last_done_on, created_at`

### Behaviour
- **Lead window**: item enters the attention feed `lead_days` before `due_date`
  ("Vehicle insurance due in 12 days"), turns overdue-red after it, and nags
  daily until marked done.
- **Mark done** records `last_done_on` and, for recurring items, computes the
  next `due_date` by the item's `advance_rule`:
  - `anniversary` — fixed schedule regardless of when you acted (insurance,
    license renewals: the policy date doesn't move because you paid late).
  - `from_done` — next due counts from the day you did it (vehicle servicing:
    next service is N days/km after the *actual* service).
- **Paused** items keep their history but never nag (sold vehicle, lapsed AMC).

### Seed list to confirm with owner (§9.6)
Vehicle insurance & PUC per vehicle · vehicle servicing per vehicle · FSSAI
license · Shop Act license · weighing-scale calibration · fire/other NOCs ·
AMCs (freezer, RO, software) · subscriptions (Vasy? WhatsApp API?).

---

## 5. Screens & navigation

One new screen — **📌 Reminders** (`/dashboard/reminders`) — with three tabs:
**Attention** (default, the merged feed §6) · **Sundries** · **Notes** ·
**Dates**. This is *operations*, not analytics, so it lives off the main
dashboard (a card/link like Staff Ledger), not the analytics subnav — but the
customer-note surfacing (§3) reaches into the analytics customer profile.

Main dashboard gets a compact **attention strip**: "⚠ 2 overdue notes ·
insurance due in 5 days · carry bags probably due" → links to the screen.

---

## 6. Shared attention feed & notifications

One computed feed (no extra table) merging, in urgency order:
1. **Overdue** critical notes and important dates (oldest overdue first);
2. **Due-soon** important dates (inside lead window);
3. **Probably-due** sundry reorder nudges (lowest priority, advisory).

Delivery channels — DECIDED (owner, 2026-07-13): **both**.
- **P1:** Reminders screen + main-dashboard attention strip.
- **P2:** a section in the existing **09:00 manager analytics digest**
  (`manager_digest_job` already scheduled — extend, don't duplicate), **and** a
  dedicated WhatsApp message to the owner listing overdue/due-soon items
  (sent only when the feed is non-empty — no empty-feed spam).

---

## 7. Capture channels

- **P1:** dashboard forms, mobile-first (owner + manager use phones).
- **P3 (optional, confirm §9.3):** WhatsApp capture — e.g. a message starting
  `note:` in the accounts group becomes a critical note via the existing
  webhook/parser infra. Powerful but parsing risk; explicitly out of P1.

---

## 8. What this is NOT (scope fences)

- ❌ Not inventory/stock management (no quantities on hand, no consumption).
- ❌ Not a second expense ledger — Vasy remains money source-of-truth.
- ❌ Not a task manager for daily operations — only *forgettable* items with
  a date or money attached.
- ❌ Not staff-advance tracking — the Staff Ledger already owns that; a
  critical note may *reference* an employee but holds no balance.

---

## 9. Open questions — owner answers 2026-07-13

- [x] **9.1 Sundries ↔ Vasy** — **DECIDED: don't sync to Vasy.** Standalone
      register, no link/reconciliation (§2).
- [ ] **9.2 Who writes/resolves** — owner only, or manager too? (Auth is
      currently disabled app-wide; anything more granular is a bigger job.
      Default until told otherwise: anyone with dashboard access.)
- [ ] **9.3 WhatsApp capture** — proposed P3, optional; confirm at P3 time.
- [ ] **9.4 Advance rule defaults** — anniversary vs from-done per category
      (proposed: insurance/licenses = anniversary, servicing = from-done;
      editable per item, so defaults are non-blocking).
- [x] **9.5 Notification channel** — **DECIDED: both** — digest section + a
      dedicated WhatsApp message to the owner (§6).
- [x] **9.6 / 9.7 Seeding** — **DECIDED: yes** — owner will provide the real
      important-dates list (vehicles, policies, licenses) and back-enter old
      forgotten notes (e.g. the ₹20,000) when the screen ships. Collect at P1
      handover.

---

## 10. Build phasing (proposed)

- **P1 — Registers + screen:** three tables, Reminders screen with 4 tabs,
  full add/edit/resolve/snooze flows, attention feed, dashboard strip.
  All reminder logic is *pull* (visible when you look) — no notifications yet.
- **P2 — The nag:** attention section in the daily manager digest; overdue
  escalation; customer-note chip on the analytics customer profile and the
  order-posting warning (credit-gate pattern).
- **P3 — Polish & capture:** WhatsApp `note:` capture; sundries
  reorder-cadence learning refinements; CSV export of all three registers.
