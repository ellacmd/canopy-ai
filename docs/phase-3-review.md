# Phase 3 — cited policy evidence

The backend indexes three versioned Markdown sources (eleven intact sections), then retrieves exact
excerpts with source, source version, application rules version, section locator, and SHA-256 hashes.
`POST /api/policies/search` is authenticated for customers, support, and admins. The corpus contains
public fictional policies shared by those roles; the operation reads no customer/order data.
It does not generate answers or authorize refunds. The Phase 2 deterministic evaluator remains authoritative.

## Model decision

Checked against official Google documentation on 24 November 2026:

- Model: `gemini-embedding-001`, a stable text embedding model with a 2,048-token input limit.
  Google recommends 768, 1536, or 3072 dimensions. We use 768 to keep vectors small.
  See [the model specification](https://aistudio.google.com/docs/models/gemini-embedding-001).
- Standard paid input price: $0.15 per million tokens; free tier available. This is a published price,
  not a measured bill. No billing plan was changed. See [pricing](https://aistudio.google.com/docs/pricing#gemini-embedding).
- Documents use `RETRIEVAL_DOCUMENT`, queries use `RETRIEVAL_QUERY`. Reduced vectors are normalized
  before storage/search. See [embedding guidance](https://aistudio.google.com/docs/embeddings).
- The embedding space is frozen as `gemini-embedding-001:768:unit-v1`; migration `0004` defines
  `vector(768)` and a space check constraint. Changing model, dimensions, or normalization requires
  a reviewed migration, reindex, and evaluation. Chat model configuration cannot change embeddings.

Google's [terms](https://aistudio.google.com/docs/terms#unpaid-services) state that unpaid inputs/outputs
may be used to improve products and prohibit submitting sensitive, confidential, or personal information.
Paid-service data terms differ, and additional regional terms apply. Indexing sends only the public
approved policy text. Smoke/evaluation commands send checked-in synthetic text. Customer-entered query
embedding is disabled by default: configure suitable provider terms (normally a paid-services project),
then explicitly set `POLICY_SEARCH_ENABLED=true`. The flag is an operator configuration, not proof of
provider billing status. No customer details are automatically appended to queries.

## Run and inspect

From `backend/`, with backend-only database/Auth settings and a Gemini key:

```sh
uv sync --frozen
uv run alembic upgrade head
uv run python -m canopy embedding-smoke
uv run python -m canopy policy-index
uv run python -m canopy policy-index
uv run python -m canopy policy-evaluate
```

Indexing makes real embedding requests when content is missing/incomplete; an unchanged second run
returns `indexed: 0, skipped: 3` and makes no embedding calls. Evaluation makes up to 34 query embedding
requests, exits nonzero if any case fails, and reports only case IDs, locators, scores, versions, and
timings. These commands are explicit and require provider access/quota; tests and health checks do not.
Each request uses the configured Gemini timeout (20 seconds by default) with one attempt and no implicit
retries. Inputs are bounded; the current corpus is well below the provider token limit. These request
limits are not a spending budget. Shared rate limits and enforced budgets remain Phase 6 work.

When query embedding is enabled, sign in through the existing Auth endpoint and call:

```http
POST /api/policies/search
Content-Type: application/json

{"query":"Can I return a gift card?","limit":3}
```

The query is 3–500 characters, the result limit is 1–5, and unknown fields are rejected. The POST body
avoids putting query text in URLs. Auth tokens remain HttpOnly. CLI search is also available with
`uv run python -m canopy policy-search --query 'Can I return a gift card?'` when search is enabled.

Response states:

| Status | Behavior |
|---|---|
| `evidence_found` | Return exact excerpts meeting the similarity threshold, with complete citations. This is evidence, not a complete answer or eligibility decision. |
| `missing_evidence` | No current complete index, missing sections, or no sufficiently relevant match: empty citations and an explicit human-review message. |
| `conflicting_evidence` | Source identity/hash/version/metadata or section content differs from the approved corpus, or competing current sources exist: empty citations and human review. |
| HTTP 503 | Search disabled, provider failure, invalid packaged corpus, or unavailable schema/database. No provider details or query text are returned in errors. |

Ranking `cosine-idf-v1` takes up to twenty cosine candidates and requires a cosine similarity of at
least 0.57 plus meaningful English policy-term overlap. A small IDF-weighted keyword overlap bonus
breaks semantic near-ties; generic brand/policy/system words cannot establish support by themselves.
This favors explicit abstention over unsupported paraphrases with no shared policy vocabulary.
Scores in citations remain raw cosine similarities; result ordering includes the keyword bonus.
The threshold is not a calibrated probability of correctness. Related
excerpts do not prove every premise of a question; unsupported details must still be referred to a human.
Phase 4 must ground each generated claim and preserve this distinction.

## Evidence and indexing boundaries

The packaged corpus is in `backend/canopy/corpus/`. `manifest.json` pins each file's source identity,
version, and hash, and binds it to `amazora-returns-v1.0` and `markdown-sections-v1`. All sections keep
their exceptions intact; oversize or empty sections and duplicate anchors are rejected. Indexing does
not accept uploaded paths or arbitrary text. A policy change requires review against
`docs/amazora-rules.md`, a source-version update, and a matching manifest; updating a hash alone does
not approve a policy change. Source bytes and all indexed section metadata are revalidated at retrieval.

An existing source/version with different metadata/hash is never overwritten. Missing or damaged chunks
can be rebuilt from the approved source. The caller's transaction publishes all sources atomically;
provider/DB failures roll back the entire run. A PostgreSQL transaction advisory lock serializes
concurrent indexers, and uniqueness constraints prevent duplicate sections. Explicit rerun is the retry
mechanism. Nothing starts processing in a background worker or from an HTTP request.

Only the current approved rules version participates in search. Historical/legacy documents stay in
the database but cannot become current evidence. Current competing sources fail closed, including an
unknown source claiming the current rules version. This prevents contradictory source selection; it is
not an LLM-based semantic contradiction detector for arbitrary future uploads.

PostgreSQL uses pgvector exact cosine candidates with a deterministic keyword rerank. Eleven chunks
do not justify an approximate HNSW/IVFFlat
index, which could reduce recall; the document foreign-key index supports corpus filtering. Re-evaluate
index design as the corpus grows. SQLite stores JSON vectors and runs an explicit local cosine fallback
for isolated tests/development. Production continues to require PostgreSQL.

## Migration and recovery

Migration `0004` extends the existing `policy_documents` table with nullable verified source metadata
and adds `policy_chunks`, bringing the domain table count to fourteen. Existing documents are preserved
and excluded until verified/indexed. PostgreSQL must have pgvector available; migration runs as owner
and installs it if absent. An existing extension in `public` or Supabase's `extensions` schema is preserved.
Runtime uses `service_role` with the fixed `public, extensions` search path. New chunks have forced RLS,
no client policies/grants, and backend-only DML permissions. No RPC bypass is introduced.

Back up development/production data using the environment's normal process before applying migrations.
To reverse this phase intentionally, stop retrieval traffic and run `uv run alembic downgrade 0003`
as the owner. This removes indexed chunks and verified source metadata, preserves document records and
business data, and never drops the possibly shared vector extension. Re-upgrading requires reindexing;
the former records are treated as legacy evidence. Normal recovery uses `policy-index`, not downgrade.
Never run integration tests or reset commands against a shared database.

## Verification

Verified on 24 November 2026:

- All **131 backend tests passed** on Python 3.13 with a fresh disposable PostgreSQL 15/pgvector
  0.8.2 database. This includes actual vector storage/cosine queries, concurrent indexers, wrong-dimension
  rejection, forced RLS/client denial, metadata drift, downgrade/re-upgrade, and extension relocation
  to Supabase's `extensions` schema. It also includes the existing Auth/refund/ownership workflows.
  Controlled corruption tests cover missing/conflicting evidence; provider mocks cover bounded requests,
  task types, normalization, safe failures, no retries, transaction rollback, and opt-in/auth boundaries.
- The live embedding smoke returned 768 dimensions for both document and query tasks using the
  configured Gemini key. This verifies access to this model independently of the chat model.
- Hosted development and local fallback migrations reached `0004`, with **3 ready sources and
  11 chunks** in each. Hosted schema comparison reported no new migration operations. All fourteen
  domain tables have forced RLS; policy table grants are denied to `anon`/`authenticated`, and no
  client policies exist. Business records and seed dates were preserved.
- Real hosted Auth plus the policy API passed for customer and support sessions using synthetic
  questions, with versioned gift-card evidence and unauthenticated denial. Search opt-in was temporary
  in the verification app; active customer query embedding remains disabled. The local backend was
  restarted and serves Phase 3 liveness, ready database status, and protected policy routes.
- The final **live hosted retrieval evaluation passed 34/34**: expected source/version in the top
  three for **26/26** policy questions, and empty evidence for **8/8** unsupported questions.
  [The saved JSON report](evaluations/phase-3-retrieval.json) records every case, locator, raw cosine
  similarity, and timing. Median full retrieval latency was **1.68 s**, mean **1.80 s**, range
  **1.39–5.79 s** over those 34 serial queries. This includes database/evidence checks and query embedding;
  it excludes login/startup. Billing usage/cost was not measured.
- Initial pure cosine retrieval passed 23/26 cases at a 0.65 cutoff. We added keyword reranking and
  policy-term support, expanded the fixture with eight paraphrase/unsupported cases, and tuned the
  cosine floor to 0.57. These are **tuned regression cases, not an independent holdout**. The small
  English corpus and suite do not establish broad customer accuracy, multilingual support, or claim-level
  grounding. Related evidence can still fail to answer part of a question; Phase 4 must preserve abstention.

Ruff lint/format, actionlint, public-file guard, secret scans, Python/npm dependency audits, and the
backend wheel build passed locally. The wheel includes the corpus and evaluation assets. The updated
CI uses pgvector images for PostgreSQL 15/17; no Phase 3 remote CI run, push, or deployment is claimed.

PDF ingestion, uploads, private storage, document administration, LLM answers, LangGraph, memory,
streaming, UI, and deployment remain deferred.
