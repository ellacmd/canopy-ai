"""Versioned evidence retrieval. No generated answers or monetary decisions."""

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from canopy.corpus import CHUNKER_VERSION, Source, digest, load_corpus
from canopy.embedding import EMBEDDING_SPACE, Embedder, EmbeddingUnavailable, unit_vector
from canopy.models import PolicyChunk, PolicyDocument
from canopy.policy import POLICY_VERSION
from canopy.provider_retry import retry_provider
from canopy.published_policy import latest_policy, published_section

# Evaluated against the checked-in synthetic cases; cosine similarity, not confidence.
MIN_SIMILARITY = 0.57
RANKING_VERSION = "cosine-idf-v1"


def terms(value: str) -> set[str]:
    # Light plural normalization; uncommon policy terms should break semantic near-ties.
    stop = {
        "this",
        "that",
        "what",
        "with",
        "from",
        "have",
        "does",
        "after",
        "before",
        "your",
        "the",
        "and",
        "for",
        "can",
        "may",
        "who",
        "how",
        "are",
        "was",
        "but",
        "not",
        "than",
        "other",
        "there",
        "these",
        "those",
        "should",
        "would",
        "could",
        "will",
        "amazora",
        "policy",
        "policies",
        "rule",
        "rules",
        "system",
        "prompt",
    }
    return {
        word[:-3] + "y"
        if word.endswith("ies")
        else (word.removesuffix("s") if len(word) > 4 else word)
        for word in re.findall(r"[a-z]{3,}", value.lower())
        if word not in stop
    }


def rerank(scores, chunks: list[PolicyChunk], query: str):
    """Small IDF-weighted overlap bonus on a pgvector cosine candidate set.

    The cosine floor still governs admission. This is ranking, not answer verification.
    """
    query_terms = terms(query)
    chunk_terms = {chunk.id: terms(chunk.content) for chunk in chunks}
    weights = {
        term: math.log(1 + len(chunks) / (1 + sum(term in t for t in chunk_terms.values())))
        for term in query_terms
    }
    denominator = sum(weights.values()) or 1

    def rank(pair):
        chunk_id, similarity = pair
        overlap = sum(weights[term] for term in query_terms & chunk_terms[chunk_id]) / denominator
        return (-float(similarity) - 0.25 * overlap, str(chunk_id))

    # Generic brand/policy words alone cannot establish evidence for an unsupported topic.
    supported = [pair for pair in scores if query_terms & chunk_terms[pair[0]]]
    return sorted(supported, key=rank)


class IndexConflict(RuntimeError):
    pass


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=3, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def bounded_query(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 2000:
            raise ValueError("Policy query exceeds 2000 UTF-8 bytes")
        return value


class Citation(BaseModel):
    source: str
    source_version: str
    rules_version: str
    title: str
    section: str
    locator: str
    source_sha256: str
    chunk_sha256: str
    excerpt: str
    similarity: float


class SearchResult(BaseModel):
    status: Literal["evidence_found", "missing_evidence", "conflicting_evidence"]
    rules_version: str = POLICY_VERSION
    embedding_space: str = EMBEDDING_SPACE
    ranking_version: str = RANKING_VERSION
    message: str
    citations: list[Citation] = Field(default_factory=list)


def same_document(document: PolicyDocument, source: Source) -> bool:
    return (
        document.source_key == source.key
        and document.version == source.version
        and document.rules_version == POLICY_VERSION
        and document.content_sha256 == source.sha256
        and document.embedding_space == EMBEDDING_SPACE
        and document.chunker_version == CHUNKER_VERSION
        and document.title == source.title
        and document.storage_path == f"corpus/{source.file}"
        and document.category == "returns"
    )


def same_chunks(chunks: list[PolicyChunk], source: Source) -> bool:
    if len(chunks) != len(source.sections):
        return False
    for chunk, section in zip(
        sorted(chunks, key=lambda c: c.ordinal), source.sections, strict=True
    ):
        if (
            chunk.ordinal != section.ordinal
            or chunk.anchor != section.anchor
            or chunk.heading != section.heading
            or chunk.content != section.text
            or chunk.content_sha256 != digest(section.text)
            or chunk.embedding_space != EMBEDDING_SPACE
        ):
            return False
        try:
            unit_vector(chunk.embedding)
        except EmbeddingUnavailable:
            return False
    return True


def index_corpus(db: Session, embedder: Embedder) -> dict:
    """Caller owns the transaction. Failed batches roll back the entire publication.

    Re-running skips complete identical sources; incomplete chunks can be rebuilt.
    Editing an existing source version is refused: review and version it instead.
    """
    sources = load_corpus()
    if embedder.space != EMBEDDING_SPACE:
        raise IndexConflict("Embedding space differs from the frozen schema")
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(739281403)"))
    indexed = skipped = 0
    for source in sources:
        document = db.scalar(
            select(PolicyDocument).where(
                PolicyDocument.source_key == source.key, PolicyDocument.version == source.version
            )
        )
        if document is not None and not same_document(document, source):
            raise IndexConflict("Existing source/version differs; refusing to overwrite evidence")
        chunks = (
            list(db.scalars(select(PolicyChunk).where(PolicyChunk.document_id == document.id)))
            if document is not None
            else []
        )
        if (
            document is not None
            and document.index_status == "ready"
            and same_chunks(chunks, source)
        ):
            skipped += 1
            continue
        if document is None:
            document = PolicyDocument(
                source_key=source.key,
                title=source.title,
                category="returns",
                version=source.version,
                storage_path=f"corpus/{source.file}",
                content_sha256=source.sha256,
                rules_version=POLICY_VERSION,
                embedding_space=EMBEDDING_SPACE,
                chunker_version=CHUNKER_VERSION,
                index_status="pending",
            )
            db.add(document)
            db.flush()
        vectors = embedder.embed([section.text for section in source.sections], task="document")
        if len(vectors) != len(source.sections):
            raise EmbeddingUnavailable("Embedding provider returned an incomplete batch")
        vectors = [unit_vector(vector) for vector in vectors]
        db.execute(delete(PolicyChunk).where(PolicyChunk.document_id == document.id))
        for section, vector in zip(source.sections, vectors, strict=True):
            db.add(
                PolicyChunk(
                    document_id=document.id,
                    ordinal=section.ordinal,
                    anchor=section.anchor,
                    heading=section.heading,
                    content=section.text,
                    content_sha256=digest(section.text),
                    embedding_space=EMBEDDING_SPACE,
                    embedding=vector,
                )
            )
        document.index_status = "ready"
        db.flush()
        indexed += 1
    return {"indexed": indexed, "skipped": skipped, "embedding_space": EMBEDDING_SPACE}


def missing(message: str) -> SearchResult:
    return SearchResult(status="missing_evidence", message=message)


def conflict() -> SearchResult:
    return SearchResult(
        status="conflicting_evidence",
        message="Policy evidence differs from the approved corpus. Human review is required.",
    )


def search_policy(
    db: Session,
    embedder: Embedder,
    request: SearchRequest,
    *,
    before_query=None,
    provider_attempts=1,
    provider_timeout=20,
    provider_deadline=None,
) -> SearchResult:
    """Validate evidence before spending query tokens; search only the current corpus."""
    sources = load_corpus()
    expected = {source.key: source for source in sources}
    documents = list(
        db.scalars(select(PolicyDocument).where(PolicyDocument.rules_version == POLICY_VERSION))
    )
    by_key = {}
    for document in documents:
        source = expected.get(document.source_key)
        if source is None or document.source_key in by_key or not same_document(document, source):
            return conflict()
        by_key[document.source_key] = document
    if set(by_key) != set(expected) or any(d.index_status != "ready" for d in documents):
        return missing(
            "The current approved corpus is not fully indexed. Human review is required."
        )
    chunks = list(
        db.scalars(
            select(PolicyChunk).where(PolicyChunk.document_id.in_([d.id for d in documents]))
        )
    )
    for source in sources:
        selected = [chunk for chunk in chunks if chunk.document_id == by_key[source.key].id]
        if not selected:
            return missing("Policy sections are missing. Human review is required.")
        if not same_chunks(selected, source):
            return conflict()
    if embedder.space != EMBEDDING_SPACE:
        raise EmbeddingUnavailable("Query embedding space differs from the frozen index")
    vectors = retry_provider(
        lambda: embedder.embed([request.query], task="query"),
        attempts=provider_attempts,
        before_attempt=before_query or (lambda: None),
        timeout=provider_timeout,
        deadline=provider_deadline if provider_deadline is not None else float("inf"),
        deadline_error=lambda message: EmbeddingUnavailable(message, code="provider_deadline"),
    )
    if len(vectors) != 1:
        raise EmbeddingUnavailable("Embedding provider returned an incomplete query")
    vector = unit_vector(vectors[0])
    if db.bind.dialect.name == "postgresql":
        distance = PolicyChunk.embedding.cosine_distance(vector)
        scores = db.execute(
            select(PolicyChunk.id, (1 - distance).label("similarity"))
            .where(PolicyChunk.document_id.in_([d.id for d in documents]))
            .order_by(distance, PolicyChunk.id)
            .limit(20)
        ).all()
    else:
        # SQLite is an explicit test/development fallback, never the production search engine.
        scores = sorted(
            (
                (
                    chunk.id,
                    sum(a * b for a, b in zip(unit_vector(chunk.embedding), vector, strict=True)),
                )
                for chunk in chunks
            ),
            key=lambda pair: (-pair[1], str(pair[0])),
        )[:20]
    scores = rerank(
        [(chunk_id, score) for chunk_id, score in scores if score >= MIN_SIMILARITY],
        chunks,
        request.query,
    )[: request.limit]
    chunk_map = {chunk.id: chunk for chunk in chunks}
    document_map = {document.id: document for document in documents}
    policy = latest_policy(db)
    published = {
        source.key: {
            section.anchor: published_section(source, section, policy)
            for section in source.sections
        }
        for source in sources
    }
    citations = []
    for chunk_id, similarity in scores:
        if similarity < MIN_SIMILARITY:
            continue
        chunk = chunk_map[chunk_id]
        document = document_map[chunk.document_id]
        rendered = published[document.source_key]
        excerpt = rendered[chunk.anchor]
        updated = policy.revision > 1
        citations.append(
            Citation(
                source=document.source_key,
                source_version=f"{policy.revision}.0" if updated else document.version,
                rules_version=policy.version,
                title=document.title,
                section=chunk.heading,
                locator=(
                    f"return-policy/{policy.revision}/{document.source_key}#{chunk.anchor}"
                    if updated
                    else f"{document.storage_path}#{chunk.anchor}"
                ),
                source_sha256=digest("\n\n".join(rendered.values()))
                if updated
                else document.content_sha256,
                chunk_sha256=digest(excerpt) if updated else chunk.content_sha256,
                excerpt=excerpt,
                similarity=round(max(-1.0, min(1.0, float(similarity))), 6),
            )
        )
    if not citations:
        return missing(
            "No sufficiently relevant policy evidence was found. Human review is required."
        )
    return SearchResult(
        status="evidence_found",
        rules_version=policy.version,
        message=(
            "These excerpts are evidence, not an eligibility decision or a complete answer. "
            "Uncovered questions require human review; "
            "deterministic application rules govern refunds."
        ),
        citations=citations,
    )
