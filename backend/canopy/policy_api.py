from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from canopy.auth import Principal, current_principal
from canopy.corpus import CorpusInvalid
from canopy.db import get_db
from canopy.embedding import EmbeddingUnavailable, GeminiEmbedder
from canopy.retrieval import SearchRequest, SearchResult, search_policy

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
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    embedder=Depends(get_embedder),
):
    # The approved public corpus is shared by all verified roles. No customer records are read.
    try:
        return search_policy(db, embedder, body)
    except EmbeddingUnavailable:
        raise HTTPException(503, "Policy embedding provider is unavailable") from None
    except CorpusInvalid:
        raise HTTPException(503, "Approved policy corpus is invalid") from None
    except SQLAlchemyError:
        raise HTTPException(
            503, "Policy index is unavailable; run migrations and indexing"
        ) from None
