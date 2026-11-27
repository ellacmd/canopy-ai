# Phase 4 — authenticated support workflow

One LangGraph `StateGraph` coordinates classification, context, action, and response nodes. Gemini
classifies requests and composes responses by selecting verified fact IDs. The backend renders the
selected facts and exact policy excerpts, validates every reference, and always includes mandatory
safety facts/citations. It accepts no generated policy claims, prices, customer IDs, or refund authority.
This constrained composition deliberately favors evidence integrity over free-form conversational prose.

## API contract

All conversation routes require a verified, linked customer. Support/admin cannot impersonate customers.
Conversation IDs, history, checkpoints, orders, quotes, turns, and executions are ownership-filtered.
Foreign and absent conversations/quotes have indistinguishable 404 responses. Customer identity and
audit actor always come from verified Auth. No browser database access or auth-token transport changed.

| Method | Path | Behavior |
|---|---|---|
| POST | `/api/conversations` | Create a customer conversation |
| GET | `/api/conversations` | Paginated owned conversations |
| GET | `/api/conversations/{id}/messages` | Paginated owned history |
| GET | `/api/conversations/{id}/executions` | Paginated owned execution metadata |
| POST | `/api/conversations/{id}/turns` | Run one atomic turn; requires `Idempotency-Key` |
| POST | `/api/conversations/{id}/turns/stream` | Same turn with progress and validated text over SSE |

Example message body: `{"message":"Track AMZ-1025"}`. A follow-up such as “Which carrier is handling it?”
uses the last verified order in that conversation. The classifier cannot introduce a new order number
absent from the latest message; supplied numbers are rechecked for ownership. Six recent messages are
bounded before provider submission. Current structured facts are fetched afresh, not trusted from memory.

An explicit refund selection creates a preview, using the existing `EligibilityInput` contract:

```json
{
  "selection": {
    "order_id": "OWNED_ORDER_UUID",
    "items": [{"order_item_id": "OWNED_ITEM_UUID", "quantity": 1}],
    "reason": "change_of_mind"
  }
}
```

The result supplies the exact selected quantities, paid amount, currency, current policy version,
quote ID, and a 15-minute expiration. A preview reserves nothing. Submission requires a separate body:

```json
{
  "action": "confirm_refund",
  "confirmation": {
    "quote_id": "RETURNED_QUOTE_UUID",
    "confirmed": true,
    "policy_version": "amazora-returns-v1.0"
  }
}
```

The backend checks ownership, active quote, expiry, policy version, current eligibility, and exact
amount/currency again. It then calls the deterministic Phase 2 tool inside the same turn transaction.
The model cannot set `confirmed`, select items, change an amount, or submit a refund. Textual consent
only reminds the customer to confirm the preview. Changed free-text instructions invalidate a pending
preview; the narrowly recognized affirmations `yes`, `yes please`, `confirm`, and `i confirm` can retain
it but never submit it. New selections supersede old quotes. `{"action":"cancel_refund"}` cancels a
pending preview; it neither releases existing reservations nor changes submitted requests.

`{"action":"escalate"}` explicitly creates a support ticket. A natural-language request for a person
offers escalation first. Exceptions continue to require human review. Escalation does not create a refund.
Every refund remains simulated and submitted; no payment/approval/completion/release endpoint was added.

Same conversation/key/body replays the saved response without model/tool calls. A changed body under
the same key returns 409. Confirming an already consumed owned quote returns its original refund,
even with a fresh turn key. Different quotes cannot exceed the existing quantity/value reservation limits.

## Graph, transactions, and memory

Order context and policy retrieval run concurrently only when independent, using separate SQLAlchemy
sessions. Model classification and response are serial. There is no autonomous worker agent. Policy
retrieval preserves the Phase 3 missing/conflicting-evidence states and exact source/version citations.
Unavailable evidence is explicit and cannot turn into invented policy. Deterministic eligibility remains
authoritative even when optional explanatory evidence is unavailable.

Migration `0005` adds `workflow_checkpoints`, `workflow_turns`, and `refund_quotes`, and extends
`agent_executions` with safe metadata, for seventeen domain tables. New tables have forced RLS and no
client grants/policies. Composite foreign keys bind quotes and checkpoints to the customer/conversation;
quotes also bind to the owned order. Migrations use the owner; runtime remains `service_role`.

The JSON-only `BaseCheckpointSaver` stages LangGraph writes safely in memory while worker threads run.
The coordinating thread saves the final END checkpoint atomically with messages, turn replay data,
execution, audit, quote consumption, and business writes. No SQLAlchemy session crosses a worker boundary.
The latest checkpoint survives process restarts and preserves order/confirmation state across turns.
No pickle, binary deserialization, arbitrary thread selection, or checkpoint API is exposed.

This is **turn-boundary persistence**, not durable mid-node resume or time travel. A crash before commit
rolls back all turn actions; retrying the same key reruns the turn. A crash/disconnect after commit replays
the saved response. PostgreSQL transaction advisory locks reject overlapping turns for the same thread
across processes. Bounded process-local striped locks also protect the explicit SQLite fallback. SQLite
is not a substitute for PostgreSQL concurrency/RLS verification.

## Streaming and failures

SSE emits `stage` events containing only fixed stage names, followed by `text_delta` chunks and a
`completed` result after validation and commit. It does not expose provider tokens, chain of thought,
graph/checkpoint state, or unverified partial claims. This is progress plus validated response streaming,
not incremental model-token streaming. Disconnect before commit rolls back; reconnect by retrying the
same turn key. Auth/ownership/input errors detected before streaming retain HTTP status codes. Failures
after headers produce an `error` event with a safe code/status; clients must handle it even on HTTP 200.

Runtime makes at most two chat calls per ordinary turn, one optional embedding search, and one attempt
per provider call. Explicit confirmation/cancellation/escalation needs no chat generation. Network/model
failures return safe errors and preserve action atomicity; there is no pretend-AI fallback. Failed turns
record safe failure usage separately after rollback. Usage unavailable from a failed request is marked
incomplete; it is never presented as a complete zero-cost measurement.

## Provider, cost, and traces

The evaluated chat model remains `gemini-3.5-flash-lite`, with structured JSON/Pydantic schemas,
minimal thinking, bounded inputs/output, the configured timeout (20 seconds by default), and no
automatic tool calls or SDK retries. Official sources checked on 25 November 2026:

- [Gemini structured output](https://aistudio.google.com/docs/structured-output)
- [Gemini thinking controls](https://aistudio.google.com/docs/thinking#thinking-levels)
- [Model and published standard pricing](https://aistudio.google.com/docs/models/gemini-3.5-flash-lite#pricing):
  $0.30 per million input tokens and $2.50 per million output tokens, including reported thinking tokens.
- [LangGraph checkpoint interface](https://reference.langchain.com/python/langgraph/checkpoints)
- [LangSmith manual instrumentation](https://docs.langchain.com/langsmith/annotate-code)

Execution metadata records latency, observed input/output tokens, usage completeness, model/embedding
attempts, fixed stages, state, and a versioned **estimated chat cost** computed from reported usage and
published prices. It is not an invoice. Embedding charges and unreported failed-provider usage are excluded.
The selected/evaluated chat model is checked explicitly; changing it requires evaluation/pricing review.

Automatic LangSmith tracing is disabled around the entire graph even if inherited environment settings
enable it. Optional manual export constructs a fresh allowlisted metadata payload: no customer/thread/
order IDs, raw prompts, messages, graph state, tool arguments, policy queries, tokens, or exception text.
The independent random trace ID correlates an internal execution with an optional external run. Trace
export failures cannot undo committed actions. An internal trace ID alone does not prove external delivery.

`WORKFLOW_ENABLED=false` by default. Configure provider data terms suitable for customer messages/order
context before enabling it; configure `POLICY_SEARCH_ENABLED=true` for policy search. Existing Google
[data-use terms](https://aistudio.google.com/docs/terms) still apply. No billing plan is changed by these
flags. `WORKFLOW_TRACING_ENABLED=true` plus backend-only `LANGSMITH_API_KEY` enables optional manual
export to the fixed US LangSmith API. No LangSmith key was configured during implementation; export is
covered by mocks and ambient-tracing suppression tests, not claimed as a live external verification.

## Repeatable evaluation

`backend/canopy/evaluations/workflow-v1.json` contains 36 synthetic cases: orders/tracking and memory,
twelve policy questions, refund requests, human support, unsupported/injected messages, and four
multi-turn confirmation/cancellation/escalation flows. The live evaluator requires a **separate loopback**
PostgreSQL URL with database name beginning `canopy_workflow_eval`; it refuses the active database URL.
Provision standard test roles first as demonstrated by integration tests. It migrates/seeds/indexes only
that disposable database and does not re-date shared seed records.

From the repository root, using your disposable test URL and the server's Gemini key:

```sh
WORKFLOW_EVAL_DATABASE_URL=postgresql+psycopg://postgres:TEST_PASSWORD@127.0.0.1:TEST_PORT/canopy_workflow_eval \
  uv run --project backend --frozen python -m canopy workflow-evaluate \
  --interval-seconds 6 --max-attempts 4
```

The historical Phase 4 build used one runtime attempt; pacing and whole-turn retries were
evaluation-only explicit options. Current text workflows separately retry known transient provider
failures with quota accounting and a shared deadline; see [the release runbook](release.md).
Evaluation defaults to one turn attempt; the maximum is four, using the same key and a bounded
10-second backoff only for transient provider failures. Every failed attempt remains in execution metrics.
Reports separate active latency from deliberate pacing/backoff and record observed states, failures,
tokens, and estimated cost. The fixture is used during development; it is not an independent holdout
or proof of broad customer accuracy. Provider availability and routing/grounding results must be assessed
separately. No real customer data or payments are used.

## Verification and recovery

The full local suite passes **157 tests** with actual disposable PostgreSQL/pgvector, including atomic rollback,
concurrent confirmation, cross-process advisory locks, ownership/FK constraints, checkpoint restart,
disconnect rollback, invalid generation, scoped history, SSE errors, and ambient trace suppression.

The final live 36-case evaluation completed with **25 passed and 11 unavailable** despite the explicit
four-attempt evaluation budget. All eleven failures were provider/service or connection failures
(HTTP 503/504, connection errors/timeouts, or read timeouts); no failed case reported a completed
response that violated its expected routing/state/evidence assertions. This is **not a passing end-to-end
reliability gate** or a 100% accuracy result. Earlier unpaced runs also encountered rate limits. Runtime
had no implicit retry; clients retried failed turns with the same key. Provider/network availability
must improve and the live suite must be rerun before claiming reliable customer-facing operation.

The [saved live report](evaluations/phase-4-workflow.json) preserves every case and failure, observed
states, attempts, usage completeness, and timings. It records 106 chat calls with 11,064 reported input
tokens and 1,619 reported output tokens. The estimate from that reported usage is **$0.00736670**, excluding
embeddings and unreported failed requests; it is an incomplete cost estimate, not an invoice. Over 43
logical turn invocations, median active latency was **2.67 s**, mean **9.53 s**, excluding deliberate
pacing/backoff but including failed attempts/network waits. Retries and the connection outage make
these run-specific baselines unsuitable as steady-state performance claims.

Separate real hosted Auth/API checks passed for authenticated SSE preview, cancellation, stale quote
denial, confirmed simulated submission, replay under same/fresh keys, cross-customer history/turn denial,
support-role restriction, escalation, and support assignment. A new synthetic review order was used;
original seed records/dates were preserved and no actual payment occurred. All seventeen domain tables
have forced RLS, and clients lack access to the three new workflow tables. Local liveness/readiness
and unauthenticated denial also passed. LangSmith delivery remains unverified without a key.

Hosted development and local fallback are migrated to `0005`. Hosted schema comparison reports no
new operations. The local backend has been restarted with Phase 4 routes; customer workflow and trace
export remain opt-in. UI implementation and product deployment are still Phase 5/6 work.

Normal recovery is retry with the **same turn key**. Intentional downgrade to `0004` removes checkpoint,
turn replay, and quote records plus execution metadata; it preserves conversations/messages, submitted
refunds, reservations, and audits. Stop workflow traffic and back up first. This is destructive to workflow
memory and should not be used as routine recovery. Re-upgrade starts new workflow memory and cannot
restore old quote confirmations. Existing phase-specific records remain historical evidence.
