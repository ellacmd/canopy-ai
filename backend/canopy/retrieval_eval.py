"""Repeatable synthetic retrieval evaluation, separate from offline adapter tests."""

import json
from importlib.resources import files
from time import perf_counter

from sqlalchemy.orm import Session

from canopy.corpus import load_corpus
from canopy.embedding import EMBEDDING_SPACE, Embedder
from canopy.policy import POLICY_VERSION
from canopy.retrieval import MIN_SIMILARITY, RANKING_VERSION, SearchRequest, search_policy


def evaluate_retrieval(db: Session, embedder: Embedder) -> dict:
    versions = {source.key: source.version for source in load_corpus()}
    cases = json.loads(
        files("canopy").joinpath("corpus/evaluation.json").read_text(encoding="utf-8")
    )
    results = []
    for case in cases:
        start = perf_counter()
        result = search_policy(db, embedder, SearchRequest(query=case["query"]))
        locators = [citation.locator.removeprefix("corpus/") for citation in result.citations]
        correct_versions = all(
            citation.rules_version == POLICY_VERSION
            and citation.source_version == versions.get(citation.source)
            for citation in result.citations
        )
        passed = correct_versions and (
            (case["locator"] in locators and result.status == "evidence_found")
            if case["locator"]
            else (result.status == "missing_evidence" and not result.citations)
        )
        results.append(
            {
                "id": case["id"],
                "passed": passed,
                "status": result.status,
                "locators": locators,
                "similarities": [citation.similarity for citation in result.citations],
                "latency_ms": round((perf_counter() - start) * 1000, 1),
            }
        )
    positives = [row for row, case in zip(results, cases, strict=True) if case["locator"]]
    negatives = [row for row, case in zip(results, cases, strict=True) if not case["locator"]]
    return {
        "embedding_space": EMBEDDING_SPACE,
        "rules_version": POLICY_VERSION,
        "minimum_cosine_similarity": MIN_SIMILARITY,
        "ranking_version": RANKING_VERSION,
        "cases": len(results),
        "passed": sum(row["passed"] for row in results),
        "positive_recall_at_3": sum(row["passed"] for row in positives) / len(positives),
        "negative_abstention": sum(row["passed"] for row in negatives) / len(negatives),
        "results": results,
    }
