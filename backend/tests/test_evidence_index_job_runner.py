from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.retrieval import evidence_index_service
from app.db.models import EvidenceIndexJob, IndexStatus


class FakeScalarResult:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


class FakeSession:
    def __init__(self, *, job, version, document, run_id, chunks):
        self.job = job
        self.version = version
        self.document = document
        self.run_id = run_id
        self.chunks = chunks
        self.commit_count = 0
        self.refreshed = None

    def get(self, model, value):
        name = getattr(model, "__name__", "")
        if name == "EvidenceIndexJob" and value == self.job.id:
            return self.job
        if name == "DocumentVersion" and value == self.version.id:
            return self.version
        if name == "Document" and value == self.document.id:
            return self.document
        return None

    def scalar(self, statement):
        return self.run_id

    def scalars(self, statement):
        return FakeScalarResult(self.chunks)

    def commit(self):
        self.commit_count += 1

    def refresh(self, item):
        self.refreshed = item


def test_run_index_job_marks_job_succeeded_and_updates_index_count(monkeypatch):
    job = EvidenceIndexJob(
        id=uuid4(),
        document_version_id=uuid4(),
        embedding_model="text-embedding-v3",
        collection_name="talent_evidence_v1",
        status=IndexStatus.PENDING,
    )
    version = SimpleNamespace(id=job.document_version_id, document_id=uuid4())
    document = SimpleNamespace(id=version.document_id, tenant_id="course-demo", candidate_id="C001")
    chunks = [
        SimpleNamespace(
            id=uuid4(),
            candidate_id="C001",
            document_version_id=version.id,
            document_type="resume",
            permission_scope="hr_private",
            content="负责推荐系统升级",
            parent_chunk_id=None,
            page_start=1,
            page_end=1,
        )
    ]
    fake_db = FakeSession(job=job, version=version, document=document, run_id=uuid4(), chunks=chunks)

    class Store:
        def ensure_collection(self):
            return None

        def delete_version(self, **kwargs):
            return None

    monkeypatch.setattr(evidence_index_service, "get_embedding_model", lambda: SimpleNamespace())
    monkeypatch.setattr(evidence_index_service, "get_evidence_store", lambda: Store())
    monkeypatch.setattr(evidence_index_service, "index_chunks", lambda **kwargs: 1)

    result = evidence_index_service.run_index_job(fake_db, job.id, version.id)

    assert result.status == IndexStatus.SUCCEEDED
    assert result.indexed_count == 1
    assert isinstance(result.started_at, datetime)
    assert result.started_at.tzinfo == UTC
    assert isinstance(result.finished_at, datetime)
    assert fake_db.commit_count == 2
    assert fake_db.refreshed is result


def test_run_index_job_marks_job_failed_when_embedding_service_unavailable(monkeypatch):
    job = EvidenceIndexJob(
        id=uuid4(),
        document_version_id=uuid4(),
        embedding_model="text-embedding-v3",
        collection_name="talent_evidence_v1",
        status=IndexStatus.PENDING,
    )
    version = SimpleNamespace(id=job.document_version_id, document_id=uuid4())
    document = SimpleNamespace(id=version.document_id, tenant_id="course-demo", candidate_id="C001")
    chunks = [
        SimpleNamespace(
            id=uuid4(),
            candidate_id="C001",
            document_version_id=version.id,
            document_type="resume",
            permission_scope="hr_private",
            content="负责推荐系统升级",
            parent_chunk_id=None,
            page_start=1,
            page_end=1,
        )
    ]
    fake_db = FakeSession(job=job, version=version, document=document, run_id=uuid4(), chunks=chunks)

    monkeypatch.setattr(evidence_index_service, "get_embedding_model", lambda: None)

    result = evidence_index_service.run_index_job(fake_db, job.id, version.id)

    assert result.status == IndexStatus.FAILED
    assert "DASHSCOPE_API_KEY" in result.error_message
    assert fake_db.commit_count == 2


def test_run_index_job_rebuilds_version_index_before_upsert(monkeypatch):
    job = EvidenceIndexJob(
        id=uuid4(),
        document_version_id=uuid4(),
        embedding_model="text-embedding-v3",
        collection_name="talent_evidence_v1",
        status=IndexStatus.PENDING,
    )
    version = SimpleNamespace(id=job.document_version_id, document_id=uuid4())
    document = SimpleNamespace(id=version.document_id, tenant_id="course-demo", candidate_id="C001")
    chunks = [
        SimpleNamespace(
            id=uuid4(),
            candidate_id="C001",
            document_version_id=version.id,
            document_type="resume",
            permission_scope="hr_private",
            content="负责推荐系统升级",
            parent_chunk_id=None,
            page_start=1,
            page_end=1,
        )
    ]
    fake_db = FakeSession(job=job, version=version, document=document, run_id=uuid4(), chunks=chunks)
    seen: dict[str, object] = {}

    class Store:
        def ensure_collection(self):
            return None

        def delete_version(self, *, tenant_id, document_version_id):
            seen["tenant_id"] = tenant_id
            seen["document_version_id"] = document_version_id

    monkeypatch.setattr(evidence_index_service, "get_embedding_model", lambda: SimpleNamespace())
    monkeypatch.setattr(evidence_index_service, "get_evidence_store", lambda: Store())

    def fake_index_chunks(**kwargs):
        seen["store"] = kwargs["store"]
        return 1

    monkeypatch.setattr(evidence_index_service, "index_chunks", fake_index_chunks)

    result = evidence_index_service.run_index_job(fake_db, job.id, version.id)

    assert result.status == IndexStatus.SUCCEEDED
    assert seen["tenant_id"] == "course-demo"
    assert seen["document_version_id"] == str(version.id)
