from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api import routes as api
from app.api.routes import HybridSearchInput
from app.retrieval.hybrid_search_service import hybrid_search_evidence, rerank_evidence
from app.integrations.milvus_store import EvidenceFilter, EvidenceSearchResult


def _candidate(chunk_id: str, content: str, score: float) -> EvidenceSearchResult:
    return EvidenceSearchResult(
        chunk_id=chunk_id,
        candidate_id="C001",
        content=content,
        score=score,
        metadata={"document_version_id": str(uuid4()), "document_type": "markdown", "permission_scope": "hr_private"},
    )


class FakeReranker:
    def __init__(self, order: list[int]):
        self.order = order
        self.seen_query = None
        self.seen_docs = None

    def rerank(self, *, query, documents, top_n):
        self.seen_query = query
        self.seen_docs = documents
        return [SimpleNamespace(index=idx, score=1.0 - rank * 0.1) for rank, idx in enumerate(self.order)]


def test_rerank_evidence_maps_index_back_to_candidates():
    candidates = [_candidate("a", "负责 Flink", 0.5), _candidate("b", "负责 React", 0.4)]
    reranker = FakeReranker(order=[1, 0])

    ranked = rerank_evidence(query="Flink 经验", candidates=candidates, reranker=reranker, top_n=2)

    assert [item.chunk_id for item in ranked] == ["b", "a"]
    assert ranked[0].rrf_score == 0.4
    assert ranked[0].rerank_score == pytest.approx(1.0)


def test_hybrid_search_evidence_runs_rerank_over_fused_results():
    class Embedder:
        def embed_query(self, text):
            return [0.1, 0.2, 0.3]

    class Store:
        def hybrid_search(self, query_text, query_vector, *, filters, limit, ef, rrf_k):
            assert query_text == "Flink 经验"
            assert limit == 20
            return [_candidate("a", "负责 Flink", 0.5), _candidate("b", "负责 React", 0.4)]

    ranked = hybrid_search_evidence(
        query="Flink 经验",
        filters=EvidenceFilter(tenant_id="course-demo", permission_scopes=["hr_private"]),
        store=Store(),
        embedder=Embedder(),
        reranker=FakeReranker(order=[0, 1]),
        limit=2,
        rerank_top_n=20,
    )

    assert [item.chunk_id for item in ranked] == ["a", "b"]
    assert ranked[0].rerank_score == pytest.approx(1.0)


def test_hybrid_search_api_builds_filter_and_returns_rerank_score(monkeypatch):
    seen = {}

    class Embedder:
        def embed_query(self, text):
            return [0.1, 0.2, 0.3]

    class Reranker:
        def rerank(self, *, query, documents, top_n):
            return [SimpleNamespace(index=0, score=0.99)]

    class Store:
        def hybrid_search(self, query_text, query_vector, *, filters, limit, ef, rrf_k):
            seen["filters"] = filters
            return [_candidate("chunk-1", "负责 Flink", 0.5)]

    monkeypatch.setattr(api, "get_embedding_model", lambda: Embedder())
    monkeypatch.setattr(api, "get_reranker", lambda: Reranker())
    monkeypatch.setattr(api, "get_evidence_store", lambda: Store())

    result = api.hybrid_search_evidence(
        HybridSearchInput(query="Flink 经验", candidate_ids=["C001"], limit=1),
        x_tenant_id="course-demo",
        x_permission_scopes="hr_private",
        db=None,
    )

    assert result[0]["chunk_id"] == "chunk-1"
    assert result[0]["score"] == pytest.approx(0.99)
    assert seen["filters"].tenant_id == "course-demo"
    assert seen["filters"].candidate_ids == ["C001"]
