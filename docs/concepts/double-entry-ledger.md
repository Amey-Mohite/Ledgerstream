# The Double-Entry Ledger (immutable, append-only)

> **In one sentence:** record every money movement as **two balanced lines** — a
> debit and an equal credit — in an **append-only** log you never edit, so the
> books always balance and every change is auditable forever.

> 🧊 **In plain terms:** it's the 700-year-old accounting trick that money never
> appears or vanishes — it only *moves*. Every rupee that leaves one pocket lands
> in another. So you always write it down **twice**: once as "left here" (credit)
> and once as "arrived there" (debit). If the two sides don't add up, you know
> instantly something's wrong. And you write in **pen, never pencil** — mistakes
> are fixed by adding a new correcting line, never by erasing — so the history is
> tamper-evident.

---

## 1. Start with the problem: a single-entry table is dangerous

Imagine tracking money as one number you mutate:

```
   balance = 1000
   balance = balance + 500     # a payment came in
   balance = balance - 200     # a refund
```

Problems: you can't see *why* the balance is what it is (no history), a bug can set
it to any value with no trace, and there's no built-in check that money is
conserved. For a payments/fintech system that's unacceptable — you need to prove,
at any time, exactly how every cent got there.

**Double-entry** fixes all three: history, an integrity check, and auditability.

---

## 2. The core rule: every transaction has equal debits and credits

Money doesn't appear or disappear; it moves between **accounts**. So each
transaction is recorded as **≥2 lines** whose **debits equal credits**.

**Worked example** — a merchant captures a $5.00 (500¢) customer payment:

```
   Journal Entry: "PaymentCaptured for payment P1"
   ┌───────────────────────────────┬────────┬────────┐
   │ Account                       │ Debit  │ Credit │
   ├───────────────────────────────┼────────┼────────┤
   │ CASH            (asset)        │  500   │        │   money arrived
   │ MERCHANT_PAYABLE (liability)   │        │  500   │   now owed to the merchant
   └───────────────────────────────┴────────┴────────┘
                          totals:     500   =  500     ✓ balanced
```

If those two columns ever differ, the entry is rejected — that equality **is** the
integrity check. (Our code literally asserts `debits == credits` before saving.)

### Debit/credit isn't "minus/plus" — it's a side
A common confusion: debit ≠ "subtract". Debit and credit are just the **two
sides** of an entry. Whether a debit *increases* or *decreases* an account depends
on the account's **type**:

| Account type | Normal side | A debit… | A credit… |
|---|---|---|---|
| Asset (CASH) | Debit | increases it | decreases it |
| Liability (MERCHANT_PAYABLE) | Credit | decreases it | increases it |

So debiting CASH raises cash; crediting MERCHANT_PAYABLE raises what we owe the
merchant. Both went **up** by 500 — money moved, nothing was created.

---

## 3. Immutability: an append-only audit log

The second pillar: **you never UPDATE or DELETE a posted line.** The ledger is
append-only. A mistake is corrected by **posting a new reversing entry** (an equal
debit/credit in the opposite direction), leaving the original visible.

```
   wrong entry:      DEBIT CASH 500 / CREDIT PAYABLE 500
   correction:       DEBIT PAYABLE 500 / CREDIT CASH 500   (reverses it)
   net effect: zero, but BOTH lines remain in history
```

Why: it's a **tamper-evident audit trail**. Regulators, auditors, and you can
replay exactly what happened and when. Editing history is how fraud hides; an
append-only ledger makes that impossible. (This is the same instinct behind the
[saga](saga-pattern.md)'s compensating actions — undo by adding, not erasing.)

> **Balances are derived, not stored.** An account's balance is `Σ debits − Σ
> credits` over its lines — computed on read. There's no mutable "balance" field to
> corrupt; the immutable lines are the single source of truth.

---

## 4. The real code in this project

**Models** (`ledger/models.py`): `Account`, `JournalEntry` (with a **UNIQUE
`event_id`** for idempotency), and `LedgerLine` (a debit or credit).

**Posting** (`ledger/services.py`) — from a consumed `PaymentCaptured` event:

```python
@transaction.atomic
def post_payment_captured(event) -> bool:
    if JournalEntry.objects.filter(event_id=event["event_id"]).exists():
        return False                              # idempotent: already posted

    cash    = _get_account(tenant, "CASH", Account.Type.ASSET)
    payable = _get_account(tenant, "MERCHANT_PAYABLE", Account.Type.LIABILITY)

    entry = JournalEntry.objects.create(event_id=event["event_id"], ...)  # UNIQUE event_id
    LedgerLine.objects.create(entry=entry, account=cash,    direction="DEBIT",  amount_minor=amt, ...)
    LedgerLine.objects.create(entry=entry, account=payable, direction="CREDIT", amount_minor=amt, ...)
    _assert_balanced(entry)                       # debits == credits, or raise
    return True
```

Verified end-to-end: consuming two `PaymentCaptured` events produced two balanced
journal entries; the balances API returns CASH `+500` and MERCHANT_PAYABLE `−500`
for the tenant, derived by summing the lines.

---

## 5. Relationship to event sourcing (don't conflate)

- A double-entry ledger is **append-only** and derives state (balances) from
  events (entries) — very much *in the spirit* of **event sourcing**.
- But event sourcing is a general architectural pattern (store all state changes
  as events, rebuild any state by replay). A ledger is a *domain-specific*
  append-only model with the balancing invariant. Ours is fed *by* events (Kafka
  `PaymentCaptured`) and is itself append-only — a natural, lightweight blend, not
  full event sourcing of the whole system.

---

## 6. Consistency & idempotency (ties to the rest of the system)

- **Strong consistency on the ledger:** entries are written in one Postgres
  transaction with the balancing check; balances are always exact (CP — see
  [CAP/PACELC](cap-and-pacelc.md)).
- **Idempotent posting:** the source event may be redelivered (Kafka is
  at-least-once), so `event_id` is UNIQUE → the same event posts exactly once.
  Without this, a redelivery would **double the money** — a catastrophic bug.

---

## 7. Interview questions you should be able to answer

- *What is double-entry bookkeeping and why use it in software?* → Every
  transaction is ≥2 lines with equal debits and credits; gives history, a built-in
  integrity check (must balance), and auditability. Money is conserved.
- *Is a debit a subtraction?* → No — it's one side of an entry; whether it raises
  or lowers depends on the account type (assets debit-normal, liabilities
  credit-normal).
- *Why immutable / append-only?* → Tamper-evident audit trail; corrections are new
  reversing entries, never edits — you can always replay the true history.
- *How do you compute a balance?* → Derive it: `Σ debits − Σ credits` over an
  account's lines. No mutable balance field to corrupt.
- *How do you prevent double-counting when the source event is redelivered?* → An
  idempotency key on the entry (UNIQUE `event_id`) so the same event posts once.
- *Ledger vs event sourcing?* → Related (append-only, derived state) but a ledger
  is a domain model with a balancing invariant, not a general event-sourcing
  framework.

---

## 8. How Ledgerstream uses it

The **Ledger service** consumes `PaymentCaptured` from Kafka and posts an immutable
double-entry journal (DEBIT CASH / CREDIT MERCHANT_PAYABLE), idempotently (UNIQUE
`event_id`), inside one Postgres transaction with a `debits == credits` assertion.
Balances and history are **derived** from the immutable lines and served by a
tenant-scoped read API. This is Tier 1 — the correctness core of a payments
platform. Corrections (Phase 3 saga rejections) will be **reversing entries**,
never deletes.

---

*Related: [Idempotency](idempotency.md) · [Outbox](outbox-pattern.md) (how the
event got here) · [Saga](saga-pattern.md) (reversing entries as compensation) ·
[CAP/PACELC](cap-and-pacelc.md) (strong consistency on balances).*
