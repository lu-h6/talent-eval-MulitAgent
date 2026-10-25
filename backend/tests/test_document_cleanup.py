from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.documents.document_cleanup import delete_document_bundle
from app.db.models import (
    Base,
    ChunkStrategyName,
    ChunkingRun,
    ChunkingStatus,
    Document,
    DocumentChunk,
    DocumentVersion,
    EvidenceIndexJob,
    FileObject,
    ParseArtifact,
    ParseJob,
    ParseStatus,
)


class FakeObjectStore:
    def __init__(self):
        self.deleted_keys: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted_keys.append(key)


class FakeMilvusStore:
    def __init__(self):
        self.deleted_versions: list[tuple[str, str]] = []

    def delete_version(self, *, tenant_id: str, document_version_id: str) -> None:
        self.deleted_versions.append((tenant_id, document_version_id))


def test_delete_document_bundle_removes_objects_rows_and_embeddings():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    document = Document(
        candidate_id="C001",
        tenant_id="course-demo",
        title="林晓岚简历",
        document_type="resume",
        permission_scope="hr_private",
    )
    session.add(document)
    session.flush()
    file_object = FileObject(
        bucket_name="talent-documents",
        object_key="documents/original.pdf",
        original_name="original.pdf",
        mime_type="application/pdf",
        size_bytes=128,
        sha256="1" * 64,
    )
    session.add(file_object)
    session.flush()
    version = DocumentVersion(document_id=document.id, file_object_id=file_object.id, version_no=1, is_current=True)
    session.add(version)
    session.flush()
    parse_job = ParseJob(document_version_id=version.id, parser_name="mineru", status=ParseStatus.SUCCEEDED)
    session.add(parse_job)
    session.flush()
    artifact = ParseArtifact(
        parse_job_id=parse_job.id,
        artifact_type="markdown",
        bucket_name="talent-documents",
        object_key="artifacts/content.md",
        content_type="text/markdown",
        size_bytes=64,
    )
    session.add(artifact)
    session.flush()
    chunk_run = ChunkingRun(
        parse_job_id=parse_job.id,
        strategy=ChunkStrategyName.MARKDOWN,
        status=ChunkingStatus.SUCCEEDED,
        chunk_size=800,
        chunk_overlap=100,
    )
    session.add(chunk_run)
    session.flush()
    session.add(
        DocumentChunk(
            chunking_run_id=chunk_run.id,
            document_version_id=version.id,
            candidate_id="C001",
            document_type="resume",
            permission_scope="hr_private",
            stable_key="stable-1",
            position=0,
            chunk_level="child",
            content="负责推荐系统升级",
        )
    )
    session.add(
        EvidenceIndexJob(
            document_version_id=version.id,
            embedding_model="text-embedding-v3",
            collection_name="talent_evidence_v1",
        )
    )
    session.commit()

    object_store = FakeObjectStore()
    milvus_store = FakeMilvusStore()

    summary = delete_document_bundle(session, object_store, milvus_store, document.id)

    assert summary["document_id"] == str(document.id)
    assert set(object_store.deleted_keys) == {"documents/original.pdf", "artifacts/content.md"}
    assert milvus_store.deleted_versions == [("course-demo", str(version.id))]
    assert session.scalar(select(Document).where(Document.id == document.id)) is None
    assert session.scalar(select(DocumentVersion).where(DocumentVersion.id == version.id)) is None
    assert session.scalar(select(ParseJob).where(ParseJob.id == parse_job.id)) is None
    assert session.scalar(select(ParseArtifact).where(ParseArtifact.id == artifact.id)) is None
