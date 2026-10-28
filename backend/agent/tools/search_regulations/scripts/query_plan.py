"""Pure construction of the three retrieval inputs."""
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryPlan:
    semantic_query: str
    sparse_query: str
    keywords: tuple[str, ...]


def build_query_plan(query: str, keywords: list[str] | None,
                     prev_query: str | None) -> QueryPlan:
    """Keep semantic context and sparse terms separate.

    Validation and keyword normalization belong to the public Pydantic schema;
    this pure helper only preserves the already validated values.
    """
    semantic_query = (query or "").strip()
    previous = (prev_query or "").strip()[:120]
    if previous:
        semantic_query = f"{previous} {semantic_query}"
    terms = tuple(keywords or ())
    sparse_query = " ".join(terms) if terms else semantic_query
    return QueryPlan(semantic_query=semantic_query, sparse_query=sparse_query,
                     keywords=terms)
