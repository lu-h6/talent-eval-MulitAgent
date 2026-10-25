import json

from app.core.config import get_settings
from app.integrations.milvus_store import EvidenceFilter, EvidenceRecord, MilvusEvidenceStore


def vector(first: float, second: float, dimension: int) -> list[float]:
    return [first, second, *([0.0] * (dimension - 2))]


def main() -> None:
    settings = get_settings()
    store = MilvusEvidenceStore.connect(
        uri=settings.milvus_uri,
        token=settings.milvus_token,
        collection_name="lesson6_verification_v1",
        dimension=settings.embedding_dimension,
    )
    store.ensure_collection()
    records = [
        EvidenceRecord(
            chunk_id="00000000-0000-0000-0000-000000000001",
            candidate_id="C001",
            document_version_id="10000000-0000-0000-0000-000000000001",
            tenant_id="course-demo",
            document_type="performance_review",
            permission_scope="hr_private",
            embedding_model="deterministic-verification",
            content_hash="1" * 64,
            content="负责推荐系统升级，将线上延迟降低 35%",
            embedding=vector(1.0, 0.0, settings.embedding_dimension),
            parent_chunk_id=None,
            page_start=2,
            page_end=2,
        ),
        EvidenceRecord(
            chunk_id="00000000-0000-0000-0000-000000000002",
            candidate_id="C002",
            document_version_id="20000000-0000-0000-0000-000000000002",
            tenant_id="course-demo",
            document_type="resume",
            permission_scope="manager_private",
            embedding_model="deterministic-verification",
            content_hash="2" * 64,
            content="负责企业财务系统报表开发",
            embedding=vector(0.0, 1.0, settings.embedding_dimension),
            parent_chunk_id=None,
            page_start=1,
            page_end=1,
        ),
    ]
    upserted = store.upsert(records)
    results = store.search(
        vector(1.0, 0.0, settings.embedding_dimension),
        filters=EvidenceFilter(tenant_id="course-demo", permission_scopes=["hr_private"]),
        limit=5,
        ef=80,
        consistency_level="Strong", # 默认是 Bounded 查询，即写即查无结果
    )
    print(f"Upserted {upserted} records, found {len(results)} matching results")
    if results:
        print(
            json.dumps(
                {
                    "collection": store.collection_name,
                    "upserted": upserted,
                    "matched": len(results),
                    "top_candidate": results[0].candidate_id,
                    "top_score": round(results[0].score, 4),
                    "permission_scope": results[0].metadata["permission_scope"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
