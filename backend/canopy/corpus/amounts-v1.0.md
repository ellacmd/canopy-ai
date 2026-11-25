# Refund quantities and amounts

## Exact paid amounts

Refunds use USD and fixed-precision decimal arithmetic. The amount is the selected whole item quantities multiplied by the actual net unit amounts paid, bounded by remaining order value. Customer-supplied or model-generated amounts cannot determine refunds.

## Discounts, tax, and shipping allocations

Item paid amounts must reconcile exactly with the order total. Unresolved discounts, tax, shipping, or other amount allocations require human review. Do not guess allocations or promise additional shipping reimbursement. The approved policy does not specify shipping fees or a shipping reimbursement entitlement.

## Partial quantities and reservations

Whole item quantities can be requested independently, up to purchased quantities minus existing reservations. Concurrent requests cannot reserve more quantity or paid value than the purchase. Eligibility checks are read-only and do not reserve value. Migrated historical active requests without item allocations conservatively reserve their whole order.
