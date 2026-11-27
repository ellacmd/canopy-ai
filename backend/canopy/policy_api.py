from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from canopy.auth import Principal, current_principal, require_roles
from canopy.corpus import CorpusInvalid, load_corpus
from canopy.db import get_db
from canopy.embedding import EmbeddingUnavailable, GeminiEmbedder
from canopy.models import PolicyChunk, PolicyDocument, RefundPolicyRevision
from canopy.published_policy import (
    PolicyPublication,
    PublishedPolicy,
    latest_policy,
    publish,
    published_section,
    view,
)
from canopy.retrieval import SearchRequest, SearchResult, same_chunks, same_document, search_policy

router = APIRouter(prefix="/api/policies", tags=["policy evidence"])


def get_embedder(request: Request):
    settings = request.app.state.settings
    if not settings.policy_search_enabled:
        raise HTTPException(503, "Policy query embedding is not enabled for this environment")
    adapter = None
    try:
        adapter = GeminiEmbedder(settings)
        yield adapter
    except EmbeddingUnavailable:
        raise HTTPException(503, "Policy embedding provider is unavailable") from None
    finally:
        if adapter is not None:
            adapter.close()


@router.post("/search", response_model=SearchResult)
def search(
    body: SearchRequest,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    embedder=Depends(get_embedder),
):
    # The approved public corpus is shared by all verified roles. No customer records are read.
    try:
        return search_policy(
            db,
            embedder,
            body,
            before_query=lambda: request.app.state.admission.provider("embedding", principal.id),
            provider_attempts=request.app.state.settings.provider_max_attempts,
            provider_timeout=request.app.state.settings.gemini_timeout_seconds,
        )
    except EmbeddingUnavailable:
        raise HTTPException(503, "Policy embedding provider is unavailable") from None
    except CorpusInvalid:
        raise HTTPException(503, "Approved policy corpus is invalid") from None
    except SQLAlchemyError:
        raise HTTPException(
            503, "Policy index is unavailable; run migrations and indexing"
        ) from None


@router.get("/index")
def index_status(
    principal: Principal = Depends(require_roles("support", "admin")),
    db: Session = Depends(get_db),
):
    try:
        sources = load_corpus()
        current = latest_policy(db)
        results = []
        for source in sources:
            document = db.scalar(
                select(PolicyDocument).where(
                    PolicyDocument.source_key == source.key,
                    PolicyDocument.version == source.version,
                )
            )
            status = "missing"
            if document is not None:
                chunks = list(
                    db.scalars(
                        select(PolicyChunk)
                        .where(PolicyChunk.document_id == document.id)
                        .limit(len(source.sections) + 1)
                    )
                )
                status = (
                    "ready"
                    if document.index_status == "ready"
                    and same_document(document, source)
                    and same_chunks(chunks, source)
                    else "needs_review"
                )
            results.append(
                {
                    "source": source.key,
                    "title": source.title,
                    "version": source.version,
                    "sha256": source.sha256,
                    "status": status,
                    "sections": [
                        {
                            "heading": section.heading,
                            "anchor": section.anchor,
                            "text": published_section(source, section, current, for_staff=True),
                        }
                        for section in source.sections
                    ],
                }
            )
        return {"rules_version": current.version, "sources": results}
    except CorpusInvalid:
        raise HTTPException(503, "Approved policy corpus is invalid") from None
    except SQLAlchemyError:
        raise HTTPException(503, "Policy index is unavailable") from None


@router.get("/returns", response_model=PublishedPolicy)
def return_policy(
    principal: Principal = Depends(require_roles("support", "admin")),
    db: Session = Depends(get_db),
):
    return latest_policy(db)


@router.get("/returns/history", response_model=list[PublishedPolicy])
def return_policy_history(
    principal: Principal = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    return [
        view(record)
        for record in db.scalars(
            select(RefundPolicyRevision).order_by(RefundPolicyRevision.revision.desc()).limit(20)
        )
    ]


@router.put("/returns", response_model=PublishedPolicy)
def publish_return_policy(
    publication: PolicyPublication,
    principal: Principal = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    return publish(db, publication, principal.id)
