from types import SimpleNamespace

import pytest

from app.retrieval.evidence_index_service import build_evidence_records, index_chunks


def test_build_records_copies_business_identity_and_source_fields():
    document = SimpleNamespace(tenant_id="tenant-a")
    chunk = SimpleNamespace(
        id="22222222-2222-2222-2222-222222222222",
        candidate_id="C001",
        document_version_id="11111111-1111-1111-1111-111111111111",
        document_type="performance_review",
        permission_scope="hr_private",
        content="负责推荐系统升级",
        parent_chunk_id=None,
        page_start=2,
        page_end=3,
    )

    records = build_evidence_records(
        document=document,
        chunks=[chunk],
        vectors=[[0.1, 0.2, 0.3]],
        embedding_model="text-embedding-v3",
    )

    assert records[0].chunk_id == str(chunk.id)
    assert records[0].tenant_id == "tenant-a"
    assert records[0].document_version_id == str(chunk.document_version_id)
    assert records[0].page_start == 2
    assert len(records[0].content_hash) == 64


def test_build_records_rejects_vector_count_mismatch():
    with pytest.raises(ValueError, match="vector count"):
        build_evidence_records(
            document=SimpleNamespace(tenant_id="tenant-a"),
            chunks=[SimpleNamespace(content="a"), SimpleNamespace(content="b")],
            vectors=[[0.1]],
            embedding_model="text-embedding-v3",
        )


def test_index_chunks_embeds_content_and_upserts_records():
    document = SimpleNamespace(tenant_id="tenant-a")
    chunk = SimpleNamespace(
        id="22222222-2222-2222-2222-222222222222",
        candidate_id="C001",
        document_version_id="11111111-1111-1111-1111-111111111111",
        document_type="resume",
        permission_scope="hr_private",
        content="推荐系统升级",
        parent_chunk_id=None,
        page_start=1,
        page_end=1,
    )

    class Embedder:
        def embed_documents(self, texts):
            assert texts == ["推荐系统升级"]
            return [[0.1, 0.2, 0.3]]

    class Store:
        def __init__(self):
            self.records = []
            self.ensured = False

        def ensure_collection(self):
            self.ensured = True

        def upsert(self, records):
            self.records.extend(records)
            return len(records)

    store = Store()

    count = index_chunks(
        document=document,
        chunks=[chunk],
        embedding_model="text-embedding-v3",
        embedder=Embedder(),
        store=store,
    )

    assert count == 1
    assert store.ensured is True
    assert store.records[0].candidate_id == "C001"
