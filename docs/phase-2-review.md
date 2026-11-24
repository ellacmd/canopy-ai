# Phase 2 review

Phase 2 implements business operations through the authenticated API. Gemini orchestration, retrieval,
and the product UI remain deferred. The approved rules are in [amazora-rules.md](amazora-rules.md).

## Try a refund in Swagger

Open `http://127.0.0.1:8000/docs` while the backend is running. Synthetic credentials are in the ignored
`backend/.env.demo` file. Sign in with `POST /api/auth/login` as Maya; the browser keeps HttpOnly cookies.

1. Call `GET /api/orders/lookup/AMZ-1024` and copy its order ID and an `order_item_id`.
2. Call `POST /api/refunds/eligibility` with those IDs:

   ```json
   {
     "order_id": "COPY_ORDER_ID",
     "items": [{"order_item_id": "COPY_ITEM_ID", "quantity": 1}],
     "reason": "change_of_mind"
   }
   ```

3. Read the outcome, amount, reasons, and policy version. If eligible and the customer confirms,
   call `POST /api/refunds` with the same body plus `"confirmed": true` and
   `"policy_version": "amazora-returns-v1.0"`. Set `Idempotency-Key` to a fresh value such as
   `review-refund-request-001` (16–100 letters, digits, hyphens, or underscores).
4. The first successful submission returns 201, `status: submitted`, and `simulated: true`.
   Repeating the same body/key returns 200 and the same ID with `replayed: true`.
   Changing the body with that key returns 409. A fresh key cannot reserve the same item again.
5. Use `GET /api/refunds/{id}` to check status. A different customer receives 404.

Dates remain those originally seeded. Eligibility depends on the current UTC date, so the examples
eventually expire. Previously submitted requests also reduce available quantities. Automated tests use
a fixed clock and fresh databases to keep boundary scenarios reproducible.

Submission reserves value and quantities. It never charges or refunds a payment method, verifies
physical evidence, or marks the request approved/completed. No refund state mutation or reservation
release endpoint exists in this phase.

## Try human review

Use `reason: damaged` in the eligibility body to receive `human_review`. No refund is created.
Call `POST /api/escalations` with a fresh `Idempotency-Key`:

```json
{
  "reason": "refund_exception",
  "summary": "Synthetic review: the customer reports a damaged item.",
  "order_id": "COPY_ORDER_ID"
}
```

The response identifies the open ticket and conversation. Identical retries return the same ticket.
Sign out and sign in as support or admin to inspect `GET /api/admin/escalations` and
`GET /api/admin/refunds`. Customers receive 403 from these operations routes.

Use `PATCH /api/admin/escalations/{id}` with `status: in_progress` and `assign_to_me: true` to take a
ticket, then resolve it with a note. Status transitions move forward; closed tickets cannot be reopened.
When no open/in-progress tickets remain, the conversation becomes resolved. Each write records an
audit event containing actor and status metadata rather than raw customer notes.

## Verification

On 24 November 2026, all **95 tests passed**, including both live PostgreSQL tests against a fresh
disposable database. Cloud and local fallback databases were upgraded to revision `0003`. Alembic
reported no schema drift on the cloud database.

Live HTTP checks against hosted Supabase passed order lookup/tracking, a simulated $89 submission
for Maya's `AMZ-1034`, identical replay, duplicate rejection, customer isolation, customer/support role
restrictions, a damage exception, escalation creation/replay, support assignment, refresh, and logout.
One submitted simulated request, one in-progress ticket, and three audit events remain as review examples.
`AMZ-1024` retains its unreserved quantity for a separate review walkthrough.
All thirteen domain tables have forced RLS, no client grants, and no client policies. Direct REST
reads of orders, requests, refund lines, audit events, and escalations were denied for both anonymous
and signed-in customer credentials.

The suite covers policy boundaries, exact decimal amounts, excluded/damaged items, partial quantities,
explicit confirmation, policy version changes, cross-customer access, atomic rollback, idempotency,
escalation permissions/transitions, migration reversal, and historical requests without consent metadata.

The disposable PostgreSQL checks race eight independent sessions for a single item, race eight retries
of one key, submit two valid partial returns concurrently, and reject cross-order/cross-customer writes
at the database constraint layer. They also check runtime roles, forced RLS, and direct client denial.

Run ordinary tests from `backend/` with `uv run --frozen pytest -q`. PostgreSQL tests require
`POSTGRES_TEST_URL` pointing to a newly created disposable database, never the development cloud DB.
Cloud smoke checks use synthetic records and do not transfer money.
