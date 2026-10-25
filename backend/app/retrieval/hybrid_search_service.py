from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.integrations.milvus_store import EvidenceFilter, EvidenceSearchResult


@dataclass(frozen=True)
class RankedEvidence:
    """一条经排序后的证据：包含 RRF 与重排两档得分。"""
    chunk_id: str
    candidate_id: str
    content: str
    rrf_score: float  # RRF 融合阶段得分
    rerank_score: float  # 交叉编码器重排得分
    metadata: dict[str, Any]


def rerank_evidence(
    *,
    query: str,
    candidates: list[EvidenceSearchResult],
    reranker: Any,
    top_n: int,
) -> list[RankedEvidence]:
    """对候选证据用交叉编码器 Reranker 重新排序。

    Args:
        query: 查询文本。
        candidates: RRF 融合后的候选结果。
        reranker: 实现了 .rerank(query, documents, top_n) 的对象（或 None 时跳过）。
        top_n: 最多返回的排序结果数。

    Returns:
        按重排得分排序的 RankedEvidence 列表。
    """
    if not candidates:
        return []
    # 调用 Reranker，按相关性排序并截取 top_n
    hits = reranker.rerank(
        query=query,
        documents=[item.content for item in candidates],
        top_n=min(top_n, len(candidates)),
    )
    ranked: list[RankedEvidence] = []
    # 依据命中下标回填候选并保留 RRF 得分与完整元信息
    for hit in hits:
        item = candidates[hit.index]
        ranked.append(
            RankedEvidence(
                chunk_id=item.chunk_id,
                candidate_id=item.candidate_id,
                content=item.content,
                rrf_score=item.score,
                rerank_score=hit.score,
                metadata=item.metadata,
            )
        )
    return ranked


def hybrid_search_evidence(
    *,
    query: str,
    filters: EvidenceFilter,
    store: Any,
    embedder: Any,
    reranker: Any,
    limit: int = 10,
    ef: int = 80,
    rrf_k: int = 60,
    rerank_top_n: int = 20,
) -> list[RankedEvidence]:
    """执行端到端混合检索：向量化查询 -> 混合检索(RRF) -> Rerank -> 截断。

    先对查询做 embedding，再走 store.hybrid_search 做稠密+稀疏融合，
    然后经 rerank_evidence 精排，最后按 limit 截断返回。

    Args:
        query: 查询文本。
        filters: 证据过滤条件（租户/权限/候选人等）。
        store: 实现了 hybrid_search 的向量库实例。
        embedder: 提供 embed_query(text) 的向量化模型。
        reranker: 实现 .rerank(...) 的重排器（或 None 时跳过重排）。
        limit: 最终返回的证据条数，默认 10。
        ef: 稠密检索的 HNSW 探索宽度，默认 80。
        rrf_k: 倒数排名融合参数 k，默认 60。
        rerank_top_n: 进入重排阶段的候选条数，默认 20。

    Returns:
        排序后的 RankedEvidence 列表，至多 limit 条。

    Raises:
        ValueError: 当查询向量维度与集合维度不一致时（由底层 store 抛出）抛出。
    """
    query_vector = list(embedder.embed_query(query))
    # 混合检索：取较多的 rerank_top_n 条进入重排，避免重排前过早截断
    fused = store.hybrid_search(
        query_text=query,
        query_vector=query_vector,
        filters=filters,
        limit=rerank_top_n,
        ef=ef,
        rrf_k=rrf_k,
    )
    ranked = rerank_evidence(query=query, candidates=fused, reranker=reranker, top_n=rerank_top_n)
    return ranked[:limit]