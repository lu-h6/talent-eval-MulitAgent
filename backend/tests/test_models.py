import pytest
from pydantic import ValidationError

from app.api.routes import EmployeeInput
from app.core.config import Settings
from app.db.models import ChunkStrategyName, ChunkingStatus, DocumentChunk, EvidenceIndexJob, IndexStatus, ParseStatus
from app.api.schemas import DocumentCreate


def test_document_create_applies_security_defaults():
    value = DocumentCreate(
        candidate_id="C001",
        tenant_id="acme",
        title="林晓岚简历",
        document_type="resume",
    )

    assert value.permission_scope == "hr_private"
    assert value.confidentiality_level == "internal"


def test_parse_status_uses_clear_terminal_states():
    assert ParseStatus.SUCCEEDED.value == "succeeded"
    assert ParseStatus.FAILED.value == "failed"
    assert "review_required" not in {item.value for item in ParseStatus}


def test_employee_job_level_uses_l_prefix():
    value = EmployeeInput(employee_no="C006", name="测试员工", job_level="L3")
    assert value.job_level == "L3"

    with pytest.raises(ValidationError):
        EmployeeInput(employee_no="C007", name="错误职级", job_level="T9")


def test_settings_include_dashscope_models():
    value = Settings(
        _env_file=None,
        dashscope_api_key="test-key",
        chat_model="qwen-plus",
        embedding_model="text-embedding-v3",
    )

    assert value.dashscope_api_key == "test-key"
    assert value.chat_model == "qwen-plus"
    assert value.embedding_model == "text-embedding-v3"


def test_settings_include_milvus_connection_and_collection_contract(monkeypatch):
    # pymilvus 在导入时会调用 load_dotenv()，把仓库根目录的 .env 注入 os.environ
    # （导入链：app.api.routes -> milvus_store -> pymilvus.settings）。
    # 这会盖掉 Settings 的默认值，而本用例校验的是默认契约，故先清掉这几个变量。
    for name in ("MILVUS_URI", "MILVUS_COLLECTION", "EMBEDDING_DIMENSION", "RERANK_MODEL", "RERANK_TOP_N"):
        monkeypatch.delenv(name, raising=False)

    value = Settings(_env_file=None)

    assert value.milvus_uri == "http://127.0.0.1:19531"
    assert value.milvus_collection == "talent_evidence_v2"
    assert value.embedding_dimension == 1024
    assert value.rerank_model == "gte-rerank-v2"
    assert value.rerank_top_n == 20


def test_chunking_enums_cover_persisted_strategy_and_status():
    assert {item.value for item in ChunkStrategyName} == {"markdown", "recursive"}
    assert ChunkingStatus.SUCCEEDED.value == "succeeded"


def test_document_chunk_has_unified_source_locators_field():
    assert "source_locators" in DocumentChunk.__table__.columns


def test_document_chunk_has_universal_markdown_offset_fields():
    assert "markdown_start" in DocumentChunk.__table__.columns
    assert "markdown_end" in DocumentChunk.__table__.columns


def test_evidence_index_job_tracks_cross_store_consistency_state():
    assert {item.value for item in IndexStatus} == {"pending", "running", "succeeded", "failed"}
    assert {
        "document_version_id",
        "embedding_model",
        "collection_name",
        "indexed_count",
        "retry_count",
        "error_message",
    } <= set(EvidenceIndexJob.__table__.columns.keys())
