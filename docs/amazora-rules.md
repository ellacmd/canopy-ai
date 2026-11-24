# Amazora return rules — approved v1.0

Approved by the project owner on 24 November 2026. Application policy version: `amazora-returns-v1.0`.
These rules govern the fictional Amazora dataset and simulated support workflows.

| Decision | Approved behavior |
|---|---|
| Currency | USD, using fixed-precision decimal arithmetic |
| Return window | Standard returns are eligible through the entire 30th UTC calendar day after recorded delivery; day 31 is outside the window |
| Payment | Pending, failed, or already refunded orders are ineligible |
| Order state | Cancelled/returned orders are ineligible; undelivered orders require human review |
| Standard return | Paid, delivered, returnable items within the window, with available quantity and paid value |
| Gift cards / excluded items | Human review; no automatic change-of-mind refund |
| Damage / wrong item / other exception | Human review, including outside-window claims; never claim evidence was verified |
| Missing/conflicting information | Human review, including missing/future delivery dates and unsupported currency |
| Amount | Requested quantities multiplied by actual net unit amounts paid, bounded by remaining order value |
| Discounts, tax, shipping | Item paid amounts must reconcile exactly with the order total; otherwise human review, without guessed allocations |
| Partial returns | Whole item quantities can be requested independently, up to purchased quantities minus existing reservations |
| Customer confirmation | Explicit `confirmed: true` and the current policy version are required before submission |
| Physical return | Receipt of a physical return is not required to submit a simulated request; submission is not approval or completed payment |
| Duplicate submissions | Same customer/key/body returns the original record; changed body with the same key returns 409; a fresh key cannot reserve the same quantity twice |
| Audit | Persist policy version, deterministic decision, selected quantities, exact amount, verified actor, and server-recorded confirmation time |
| Actual payment | None. Every created refund request is marked `simulated: true` and `submitted` |

Eligibility checks do not create requests or reserve value. Submission rechecks current facts inside a
transaction, reserves item quantities and order value, then writes the request, lines, and audit record
atomically. Concurrent requests cannot exceed those limits. Customer IDs and amounts come from the
verified backend context and database, rather than customer-supplied fields or model output.

An exception decision explains the need for human review. The customer can create an escalation;
it does not automatically create a refund. Support/admin accounts can assign and resolve tickets.

Phase 2 exposes no refund approval, completion, rejection, or reservation-release operation. Pending
reservations remain in place until a later explicit lifecycle is implemented. Migrated historical active
refunds without item allocations conservatively reserve their whole order; unknown historical consent
remains unknown. Retrieved documents and future model outputs cannot override these application rules.
