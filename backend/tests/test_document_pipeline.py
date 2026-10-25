from types import SimpleNamespace
from uuid import uuid4

from app.documents import document_pipeline
from app.db.models import IndexStatus, ParseStatus


class FakeRedis:
    def __init__(self):
        self.items: list[tuple[str, str]] = []

    def rpush(self, queue: str, payload: str) -> None:
        self.items.append((queue, payload))


class FakeDb:
    def __init__(self, *, document=None, version=None):
        self.document = document
        self.version = version
        self.added: list[object] = []
        self.commits = 0

    def add(self, item: object) -> None:
        self.added.append(item)

    def commit(self) -> None:
        self.commits += 1

    def get(self, model: object, value: object) -> object | None:
        name = getattr(model, "__name__", "")
        if name == "DocumentVersion" and self.version and value == self.version.id:
            return self.version
        if name == "Document" and self.document and value == self.document.id:
            return self.document
        return None


def test_queue_parse_job_creates_pending_job_and_pushes_parse_queue():
    version_id = uuid4()
    fake_db = FakeDb()
    fake_redis = FakeRedis()

    job = document_pipeline.queue_parse_job(fake_db, fake_redis, version_id)

    assert job.document_version_id == version_id
    assert job.status == ParseStatus.PENDING
    assert fake_db.commits == 1
    assert fake_redis.items == [("talent:parse:queue", f"{job.id}:{version_id}")]


def test_continue_document_pipeline_creates_chunk_run_and_queues_index_job(monkeypatch):
    document = SimpleNamespace(id=uuid4(), tenant_id="course-demo", candidate_id="C001", document_type="resume")
    version = SimpleNamespace(id=uuid4(), document_id=document.id)
    fake_db = FakeDb(document=document, version=version)
    fake_redis = FakeRedis()
    seen: dict[str, object] = {}

    def fake_create_chunking_run(db, store, current_document, current_version, *, chunk_size, chunk_overlap):
        seen["document"] = current_document
        seen["version"] = current_version
        seen["chunk_size"] = chunk_size
        seen["chunk_overlap"] = chunk_overlap
        return SimpleNamespace(id=uuid4(), status="succeeded")

    monkeypatch.setattr(document_pipeline, "create_chunking_run", fake_create_chunking_run)

    job = document_pipeline.continue_document_pipeline(
        fake_db,
        store=object(),
        redis_client=fake_redis,
        version_id=version.id,
        settings=SimpleNamespace(
            embedding_model="text-embedding-v3",
            milvus_collection="talent_evidence_v1",
        ),
    )

    assert seen["document"] is document
    assert seen["version"] is version
    assert seen["chunk_size"] == document_pipeline.DEFAULT_CHUNK_SIZE
    assert seen["chunk_overlap"] == document_pipeline.DEFAULT_CHUNK_OVERLAP
    assert job.status == IndexStatus.PENDING
    assert fake_redis.items == [("talent:index:queue", f"{job.id}:{version.id}")]
