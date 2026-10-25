from dataclasses import replace

import pytest
from pymilvus import DataType

from app.integrations.milvus_store import EvidenceFilter, EvidenceRecord, MilvusEvidenceStore, build_filter_expression


class FakeMilvusClient:
    def __init__(self):
        self.collections: set[str] = set()
        self.created_schema = None
        self.created_index = None
        self.upserted: list[dict] = []
        self.search_calls: list[dict] = []
        self.hybrid_search_calls: list[dict] = []
        self.deleted_filters: list[str] = []

    def has_collection(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_schema(self, **kwargs):
        return FakeSchema()

    def prepare_index_params(self):
        return FakeIndexParams()

    def create_collection(self, **kwargs):
        self.collections.add(kwargs["collection_name"])
        self.created_schema = kwargs["schema"]
        self.created_index = kwargs["index_params"]

    def upsert(self, **kwargs):
        self.upserted.extend(kwargs["data"])
        return {"upsert_count": len(kwargs["data"])}

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return [[{"id": "chunk-1", "distance": 0.91, "entity": {"candidate_id": "C001", "content": "推荐系统升级"}}]]

    def hybrid_search(self, **kwargs):
        self.hybrid_search_calls.append(kwargs)
        return [[{"id": "chunk-1", "distance": 0.033, "entity": {"candidate_id": "C001", "content": "推荐系统升级"}}]]

    def delete(self, **kwargs):
        self.deleted_filters.append(kwargs["filter"])
        return {"delete_count": 1}


class FakeSchema:
    def __init__(self):
        self.fields: list[dict] = []
        self.functions: list = []

    def add_field(self, **kwargs):
        self.fields.append(kwargs)

    def add_function(self, function):
        self.functions.append(function)


class FakeIndexParams:
    def __init__(self):
        self.indexes: list[dict] = []

    def add_index(self, **kwargs):
        self.indexes.append(kwargs)


def sample_record() -> EvidenceRecord:
    return EvidenceRecord(
        chunk_id="chunk-1",
        candidate_id="C001",
        document_version_id="version-1",
        tenant_id="course-demo",
        document_type="performance_review",
        permission_scope="hr_private",
        embedding_model="text-embedding-v3",
        content_hash="abc123",
        content="负责推荐系统升级，将线上延迟降低 35%",
        embedding=[0.1, 0.2, 0.3],
        parent_chunk_id=None,
        page_start=2,
        page_end=2,
    )


def test_filter_expression_requires_tenant_and_permission_scope():
    expression = build_filter_expression(
        EvidenceFilter(
            tenant_id='tenant"a',
            permission_scopes=["hr_private"],
            candidate_ids=["C001", "C002"],
            document_types=["resume"],
        )
    )

    assert 'tenant_id == "tenant\\"a"' in expression
    assert 'permission_scope in ["hr_private"]' in expression
    assert 'candidate_id in ["C001", "C002"]' in expression
    assert 'document_type in ["resume"]' in expression
    assert "is_active == true" in expression


def test_filter_expression_rejects_empty_permission_scope():
    with pytest.raises(ValueError, match="permission_scopes"):
        build_filter_expression(EvidenceFilter(tenant_id="course-demo", permission_scopes=[]))


def test_store_creates_explicit_schema_and_hnsw_index():
    client = FakeMilvusClient()
    store = MilvusEvidenceStore(client=client, collection_name="talent_evidence_v2", dimension=3)

    store.ensure_collection()

    fields = {field["field_name"]: field for field in client.created_schema.fields}
    assert fields["chunk_id"]["is_primary"] is True
    assert fields["embedding"]["dim"] == 3
    assert fields["tenant_id"]["max_length"] == 64
    assert fields["content"]["enable_analyzer"] is True
    assert fields["content"]["analyzer_params"] == {"type": "chinese"}
    assert fields["content_sparse"]["datatype"] == DataType.SPARSE_FLOAT_VECTOR
    assert len(client.created_schema.functions) == 1
    bm25 = client.created_schema.functions[0]
    assert bm25.name == "content_bm25"
    assert bm25.input_field_names == ["content"]
    assert bm25.output_field_names == ["content_sparse"]
    assert client.created_index.indexes == [
        {
            "field_name": "embedding",
            "index_name": "evidence_embedding_hnsw",
            "index_type": "HNSW",
            "metric_type": "COSINE",
            "params": {"M": 16, "efConstruction": 128},
        },
        {
            "field_name": "content_sparse",
            "index_name": "evidence_content_bm25",
            "index_type": "SPARSE_INVERTED_INDEX",
            "metric_type": "BM25",
        },
    ]


def test_sparse_search_uses_bm25_metric_and_raw_text():
    client = FakeMilvusClient()
    store = MilvusEvidenceStore(client=client, collection_name="talent_evidence_v2", dimension=3)

    results = store.sparse_search(
        "Flink 实时计算",
        filters=EvidenceFilter(tenant_id="course-demo", permission_scopes=["hr_private"]),
        limit=5,
    )

    assert results[0].candidate_id == "C001"
    call = client.search_calls[0]
    assert call["data"] == ["Flink 实时计算"]
    assert call["anns_field"] == "content_sparse"
    assert call["search_params"] == {"metric_type": "BM25"}


def test_hybrid_search_uses_rrf_ranker_and_two_requests():
    client = FakeMilvusClient()
    store = MilvusEvidenceStore(client=client, collection_name="talent_evidence_v2", dimension=3)

    results = store.hybrid_search(
        "Flink 实时计算",
        [0.1, 0.2, 0.3],
        filters=EvidenceFilter(tenant_id="course-demo", permission_scopes=["hr_private"]),
        limit=5,
        ef=80,
        rrf_k=60,
    )

    assert results[0].chunk_id == "chunk-1"
    assert results[0].score == pytest.approx(0.033)
    call = client.hybrid_search_calls[0]
    assert len(call["reqs"]) == 2
    dense_req, sparse_req = call["reqs"]
    assert dense_req.anns_field == "embedding"
    assert sparse_req.anns_field == "content_sparse"
    assert call["limit"] == 5


def test_store_upsert_is_idempotent_by_chunk_id():
    client = FakeMilvusClient()
    store = MilvusEvidenceStore(client=client, collection_name="talent_evidence_v1", dimension=3)
    record = sample_record()

    store.upsert([record])
    store.upsert([replace(record, content="更新后的证据")])

    assert [item["chunk_id"] for item in client.upserted] == ["chunk-1", "chunk-1"]
    assert client.upserted[-1]["content"] == "更新后的证据"


def test_store_rejects_embedding_dimension_mismatch_before_write():
    store = MilvusEvidenceStore(client=FakeMilvusClient(), collection_name="talent_evidence_v1", dimension=4)

    with pytest.raises(ValueError, match="dimension"):
        store.upsert([sample_record()])


def test_search_applies_business_filter_and_returns_evidence():
    client = FakeMilvusClient()
    store = MilvusEvidenceStore(client=client, collection_name="talent_evidence_v1", dimension=3)

    results = store.search(
        [0.1, 0.2, 0.3],
        filters=EvidenceFilter(tenant_id="course-demo", permission_scopes=["hr_private"]),
        limit=5,
        ef=80,
        consistency_level="Strong",
    )

    assert results[0].chunk_id == "chunk-1"
    assert results[0].candidate_id == "C001"
    assert results[0].score == pytest.approx(0.91)
    assert client.search_calls[0]["filter"] == 'tenant_id == "course-demo" and permission_scope in ["hr_private"] and is_active == true'
    assert client.search_calls[0]["search_params"]["params"]["ef"] == 80
    assert client.search_calls[0]["consistency_level"] == "Strong"


def test_delete_version_uses_tenant_and_version_filter():
    client = FakeMilvusClient()
    client.collections.add("talent_evidence_v2")
    store = MilvusEvidenceStore(client=client, collection_name="talent_evidence_v2", dimension=3)

    store.delete_version(tenant_id="course-demo", document_version_id="version-1")

    assert client.deleted_filters == ['tenant_id == "course-demo" and document_version_id == "version-1"']


def test_delete_version_skips_when_collection_does_not_exist():
    client = FakeMilvusClient()
    store = MilvusEvidenceStore(client=client, collection_name="talent_evidence_v2", dimension=3)

    store.delete_version(tenant_id="course-demo", document_version_id="version-1")

    assert client.deleted_filters == []


def test_search_reads_primary_key_from_entity_when_sdk_omits_top_level_id():
    client = FakeMilvusClient()
    client.search = lambda **kwargs: [[{
        "distance": 0.88,
        "entity": {"chunk_id": "chunk-from-entity", "candidate_id": "C001", "content": "证据"},
    }]]
    store = MilvusEvidenceStore(client=client, collection_name="talent_evidence_v1", dimension=3)

    results = store.search(
        [0.1, 0.2, 0.3],
        filters=EvidenceFilter(tenant_id="course-demo", permission_scopes=["hr_private"]),
    )

    assert results[0].chunk_id == "chunk-from-entity"
