import sys
import time

from app.core.config import get_settings
from app.retrieval.hybrid_search_service import hybrid_search_evidence
from app.integrations.milvus_store import EvidenceFilter, MilvusEvidenceStore
from app.integrations.model_provider import get_embedding_model
from app.integrations.reranker import get_reranker


def _filter(candidate_ids=None):
    return EvidenceFilter(
        tenant_id="course-demo",
        permission_scopes=["hr_private"],
        candidate_ids=candidate_ids,
    )


def _print_hits(title, results):
    print(f"\n== {title} ==")
    for idx, item in enumerate(results, start=1):
        snippet = item.content.replace("\n", " ")[:40]
        print(f"  {idx:>2}. {item.candidate_id}  score={item.score:.4f}  {snippet}")


def run_dense(store, query_vector, query, filters, limit):
    start = time.perf_counter()
    results = store.search(query_vector, filters=filters, limit=limit, ef=80)
    elapsed = time.perf_counter() - start
    _print_hits(f"纯向量召回 (dense)  {elapsed*1000:.1f} ms", results)
    return elapsed, results


def run_sparse(store, query, filters, limit):
    start = time.perf_counter()
    results = store.sparse_search(query, filters=filters, limit=limit)
    elapsed = time.perf_counter() - start
    _print_hits(f"纯关键词召回 (BM25)  {elapsed*1000:.1f} ms", results)
    return elapsed, results


def run_hybrid(store, query_vector, query, filters, limit):
    start = time.perf_counter()
    results = store.hybrid_search(query, query_vector, filters=filters, limit=limit, ef=80, rrf_k=60)
    elapsed = time.perf_counter() - start
    _print_hits(f"混合召回 (RRF)  {elapsed*1000:.1f} ms", results)
    return elapsed, results


def run_hybrid_rerank(store, embedder, reranker, query, filters, limit):
    start = time.perf_counter()
    results = hybrid_search_evidence(
        query=query,
        filters=filters,
        store=store,
        embedder=embedder,
        reranker=reranker,
        limit=limit,
        ef=80,
        rrf_k=60,
        rerank_top_n=20,
    )
    elapsed = time.perf_counter() - start
    print(f"\n== 混合检索 + Rerank 精排  {elapsed*1000:.1f} ms ==")
    for idx, item in enumerate(results, start=1):
        snippet = item.content.replace("\n", " ")[:40]
        print(f"  {idx:>2}. {item.candidate_id}  rerank={item.rerank_score:.4f}  {snippet}")
    return elapsed, results


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "有 Flink 实时计算经验的数据平台工程师"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    settings = get_settings()
    embedder = get_embedding_model()
    reranker = get_reranker()
    if embedder is None or reranker is None:
        raise SystemExit("DASHSCOPE_API_KEY 未配置，无法运行混合检索验收")
    store = MilvusEvidenceStore.connect(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        collection_name=settings.milvus_collection,
        dimension=settings.embedding_dimension,
    )
    query_vector = list(embedder.embed_query(query))
    filters = _filter()
    print(f"collection={settings.milvus_collection} query={query!r} limit={limit}")

    run_dense(store, query_vector, query, filters, limit)
    run_sparse(store, query, filters, limit)
    run_hybrid(store, query_vector, query, filters, limit)
    run_hybrid_rerank(store, embedder, reranker, query, filters, limit)


if __name__ == "__main__":
    main()
