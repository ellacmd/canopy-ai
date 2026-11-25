# Simulated submission and support

## Explicit confirmation

Explicit confirmed: true and the current application policy version amazora-returns-v1.0 are required before submission. Submission rechecks deterministic eligibility inside a transaction, reserves quantities and order value, and writes the request, line allocations, and audit record atomically. Persist the verified actor, server-recorded confirmation time, policy version, decision, selected quantities, and exact amount. Unknown historical consent stays unknown.

## Simulation and physical returns

Every created refund request is simulated: true with status submitted. No actual payment occurs. Receipt of a physical return is not required to submit a simulated request. Submission is not approval or completed payment. There is no refund approval, completion, rejection, or reservation-release endpoint in the current workflow; reservations remain in place.

## Duplicate submissions

The same customer, idempotency key, and request body return the original record. A changed body with the same key returns 409. A fresh key cannot reserve the same quantity twice. Customer identity and amounts come from the verified backend context and database.

## Human support and policy authority

An exception decision explains why human review is needed. A customer can create an escalation; it does not automatically create a refund. Support and admin accounts can assign and resolve tickets. Retrieved documents and model outputs cannot override deterministic application rules or authorize monetary actions. Missing or conflicting policy evidence must be reported explicitly without inventing policy. These rules govern fictional Amazora support workflows.
