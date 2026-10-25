"""
routes.py —— HTTP API 路由层。

把所有 /api 接口集中定义在此：员工、知识库、文档及其解析/分块/索引流水线、
证据检索（稠密/混合）、查询计划编译与人才搜索等。所有函数均为 FastAPI 端点，
通过模板参数或把参数对象（如 X-Tenant-Id、X-Permission-Scopes 请求头）注入。
路由只做：参数建模校验、调用下层服务、异常转 HTTP 状态码、组织响应 JSON。
"""
import logging  # 日志
from datetime import date  # 日期，用于计算年龄
from typing import Literal  # 字面量类型，用于限定取值集合
from uuid import UUID  # 通用唯一标识符，用作资源 id

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from redis import Redis  # Redis 客户端，用于推送任务队列
from sqlalchemy import func, select  # 聚合函数与查询构造
from sqlalchemy.orm import Session

# 文档分块相关服务
from app.documents.chunk_service import (
    create_chunking_run,  # 执行一次分块
    latest_chunks,  # 取最新一次分块运行及其块
)
from app.core.config import get_settings  # 应用配置
from app.db.database import get_db  # 数据库会话依赖
from app.documents.document_cleanup import delete_document_bundle  # 级联删除文档整套数据
from app.documents.document_pipeline import queue_index_job, queue_parse_job, summarize_pipeline_status
from app.retrieval.evidence_index_service import get_evidence_store  # 获取向量检索存储
from app.retrieval.evidence_citations import load_pack_sources, resolve_citation
from app.retrieval.evidence_pack import build_evidence_packs, model_extractor
from app.retrieval.hybrid_search_service import hybrid_search_evidence as hybrid_search_evidence_service
from app.integrations.milvus_store import EvidenceFilter  # 证据检索过滤器
from app.integrations.model_provider import get_embedding_model  # 获取 Embedding 模型
from app.integrations.model_provider import get_chat_model  # 获取对话模型
from app.retrieval.query_plan import QueryPlan, compile_query_plan, merge_query_results, search_with_optimization, \
    select_candidate_ids
from app.integrations.reranker import get_reranker  # 获取 Rerank 排序器
from app.db.models import (
    ChunkingRun,  # 分块运行记录
    Document,  # 文档实体
    DocumentVersion,  # 文档版本
    EmployeeProfile,  # 员工档案
    EvidenceIndexJob,  # 证据索引任务
    FileObject,  # 文件对象
    IndexStatus,  # 索引状态枚举
    KnowledgeBase,  # 知识库
    ParseArtifact,  # 解析工件
    ParseJob,  # 解析任务
)
from app.integrations.object_store import ObjectStore  # 对象存储客户端
from app.documents.services import current_version, parse_version, upload_document

# 统一 API 前缀
router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


class EmployeeInput(BaseModel):
    """创建员工请求体。"""
    employee_no: str  # 工号（业务主键）
    name: str  # 姓名
    gender: str | None = None  # 性别
    birth_date: date | None = None  # 出生日期（用于计算年龄）
    region: str | None = None  # 地区
    current_position: str | None = None  # 当前职位
    job_level: str | None = Field(default=None, pattern=r"^L\d+$")  # 职级（L 加上数字）
    years_of_experience: float | None = None  # 工作年限
    department: str | None = None  # 部门


class KnowledgeBaseInput(BaseModel):
    """创建知识库请求体。"""
    name: str  # 知识库名称
    description: str | None = None  # 描述
    permission_scope: str = "hr_private"  # 权限范围，默认 HR 私有


class ChunkingInput(BaseModel):
    """分块任务请求体：块大小与重叠字数。"""
    chunk_size: int = Field(default=800, ge=100, le=8000)  # 每个块的目标长度
    chunk_overlap: int = Field(default=100, ge=0, le=2000)  # 相邻块重叠字数


class EvidenceSearchInput(BaseModel):
    """稠密向量证据检索请求体。"""
    query: str = Field(min_length=1, max_length=2000)  # 检索语句
    candidate_ids: list[str] | None = None  # 候选人过滤（可空）
    document_types: list[str] | None = None  # 文档类型过滤（可空）
    limit: int = Field(default=10, ge=1, le=100)  # 返回条数上限
    ef: int = Field(default=80, ge=10, le=1000)  # HNSW 索引 ef 参数（检索范围）


class HybridSearchInput(BaseModel):
    """混合（稠密+稀疏）检索请求体，含 RRF 融合与重排参数。"""
    query: str = Field(min_length=1, max_length=2000)  # 检索语句
    candidate_ids: list[str] | None = None  # 候选人过滤（可空）
    document_types: list[str] | None = None  # 文档类型过滤（可空）
    limit: int = Field(default=10, ge=1, le=100)  # 返回条数上限
    ef: int = Field(default=80, ge=10, le=1000)  # HNSW ef 参数
    rrf_k: int = Field(default=60, ge=1, le=1000)  # RRF 融合常数
    rerank_top_n: int = Field(default=20, ge=1, le=100)  # 重排前的候选数量


class QueryPlanInput(BaseModel):
    """查询计划编译请求体。"""
    query: str = Field(min_length=1, max_length=2000)  # 自然语言查询


class TalentSearchInput(BaseModel):
    """人才搜索请求体：查询、候选筛选、检索模式与返回条数。"""
    include_evidence_pack: bool = False  # 是否同时构建证据包（Markdown 报告原料）
    query: str = Field(min_length=1, max_length=2000)  # 自然语言查询
    retrieval_mode: Literal["standard", "auto_optimize"] = "standard"  # 检索模式：标准 / 自动优化
    limit: int = Field(default=10, ge=1, le=100)  # 返回条数上限
    ef: int = Field(default=80, ge=10, le=1000)  # HNSW ef 参数
    rrf_k: int = Field(default=60, ge=1, le=1000)  # RRF 融合常数
    rerank_top_n: int = Field(default=20, ge=1, le=100)  # 重排候选数量


class DeleteDocumentsInput(BaseModel):
    """批量删除文档请求体。"""
    document_ids: list[UUID] = Field(min_length=1, max_length=100)  # 待删除文档 id 列表


def employee_json(item: EmployeeProfile, material_count: int = 0):
    """把员工实体序列化为对外 JSON，并按出生日期计算年龄。

    Args:
        item: 员工数据库实体。
        material_count: 该员工名下的材料数量，默认 0。

    Returns:
        字典形式的员工对外表示，含 id、工号、姓名、年龄、职级、材料数量等字段。
    """
    today = date.today()
    age = None
    if item.birth_date:
        # 计算周岁：先减年份差，若生日未到则再减一
        age = today.year - item.birth_date.year - (
                (today.month, today.day) < (item.birth_date.month, item.birth_date.day))
    return {"id": item.id, "employee_no": item.employee_no, "name": item.name, "gender": item.gender,
            "birth_date": item.birth_date, "age": age, "region": item.region, "current_position": item.current_position,
            "job_level": item.job_level, "years_of_experience": item.years_of_experience, "department": item.department,
            "employment_status": item.employment_status, "material_count": material_count}


def latest_parse_job(db: Session, version_id: UUID | None) -> ParseJob | None:
    """取指定文档版本最近创建的解析任务，无版本时返回 None。

    Args:
        db: 数据库会话。
        version_id: 文档版本 id；为 None 时直接返回 None。

    Returns:
        最近创建的 ParseJob，或 None。
    """
    if version_id is None:
        return None
    return db.scalar(
        select(ParseJob)
        .where(ParseJob.document_version_id == version_id)
        .order_by(ParseJob.created_at.desc())
        .limit(1)
    )


def latest_chunking_run(db: Session, version_id: UUID | None) -> ChunkingRun | None:
    """取指定文档版本最近的分块运行记录，无版本时返回 None。

    Args:
        db: 数据库会话。
        version_id: 文档版本 id；为 None 时直接返回 None。

    Returns:
        最近的分块运行 ChunkingRun，或 None。
    """
    if version_id is None:
        return None
    return db.scalar(
        select(ChunkingRun)
        .join(ParseJob, ParseJob.id == ChunkingRun.parse_job_id)
        .where(ParseJob.document_version_id == version_id)
        .order_by(ChunkingRun.created_at.desc())
        .limit(1)
    )


def latest_index_job(db: Session, version_id: UUID | None) -> EvidenceIndexJob | None:
    """取指定文档版本最近的证据索引任务，无版本时返回 None。

    Args:
        db: 数据库会话。
        version_id: 文档版本 id；为 None 时直接返回 None。

    Returns:
        最近的证据索引任务 EvidenceIndexJob，或 None。
    """
    if version_id is None:
        return None
    return db.scalar(
        select(EvidenceIndexJob)
        .where(EvidenceIndexJob.document_version_id == version_id)
        .order_by(EvidenceIndexJob.created_at.desc())
        .limit(1)
    )


def build_document_pipeline_payload(db: Session, document_id: UUID) -> dict[str, str | None]:
    """汇总文档流水线状态：解析、分块、索引三阶段状态。

    Args:
        db: 数据库会话。
        document_id: 文档 id。

    Returns:
        包含 pipeline_status / parse_status / chunk_status / index_status 的字典。
    """
    version = current_version(db, document_id)
    snapshot = summarize_pipeline_status(
        parse_job=latest_parse_job(db, version.id if version else None),
        chunk_run=latest_chunking_run(db, version.id if version else None),
        index_job=latest_index_job(db, version.id if version else None),
    )
    return {
        "pipeline_status": snapshot.pipeline_status,
        "parse_status": snapshot.parse_status,
        "chunk_status": snapshot.chunk_status,
        "index_status": snapshot.index_status,
    }


def enrich_evidence_context(db: Session, results):
    """加载证据命中对应的版本/文档/员工上下文，用于补充展示信息。

    Args:
        db: 数据库会话（可能为 None，此时返回空映射）。
        results: 证据命中结果列表，每项含 metadata.document_version_id。

    Returns:
        三元组 (version_by_id, document_by_id, employee_by_id)，均为按 id 索引的映射。
    """
    # 收集所有命中的版本 id（去 None）
    version_ids = [
        UUID(value)
        for item in results
        for value in [item.metadata.get("document_version_id")]
        if value
    ] if db else []
    versions = db.scalars(
        select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))).all() if db and version_ids else []
    version_by_id = {str(item.id): item for item in versions}  # 版本 id → 实体
    # 根据版本找到对应文档
    documents = db.scalars(select(Document).where(
        Document.id.in_([item.document_id for item in versions]))).all() if db and versions else []
    document_by_id = {item.id: item for item in documents}  # 文档 id → 实体
    # 根据文档找到对应员工
    employees = db.scalars(select(EmployeeProfile).where(EmployeeProfile.id.in_(
        [item.employee_id for item in documents if item.employee_id]))).all() if db and documents else []
    employee_by_id = {item.id: item for item in employees}  # 员工 id → 实体
    return version_by_id, document_by_id, employee_by_id


def evidence_item_json(item, version_by_id, document_by_id, employee_by_id, *, score: float):
    """把单个证据命中序列化为对外 JSON，并关联候选人姓名与文档元信息。

    Args:
        item: 向量检索返回的命中项。
        version_by_id: 版本 id → 版本实体 的映射。
        document_by_id: 文档 id → 文档实体 的映射。
        employee_by_id: 员工 id → 员工实体 的映射。
        score: 该命中项的最终得分。

    Returns:
        字典形式的证据命中对外表示。
    """
    # 反向解析出版本 / 文档 / 员工
    version = version_by_id.get(str(item.metadata.get("document_version_id")))
    document = document_by_id.get(version.document_id) if version else None
    employee = employee_by_id.get(document.employee_id) if document and document.employee_id else None
    return {
        "chunk_id": item.chunk_id,
        "candidate_id": item.candidate_id,
        "candidate_name": employee.name if employee else item.candidate_id,
        "document_id": str(document.id) if document else None,
        "document_title": document.title if document else None,
        "material_no": document.material_no if document else None,
        "document_type": item.metadata.get("document_type"),
        "permission_scope": item.metadata.get("permission_scope"),
        "page_start": item.metadata.get("page_start"),
        "page_end": item.metadata.get("page_end"),
        "content": item.content,
        "score": score,
        "metadata": item.metadata,
    }


@router.get("/employees")
def list_employees(db: Session = Depends(get_db)):
    """列出全部员工及其名下材料数量，按工号排序。

    Args:
        db: 数据库会话（依赖注入）。

    Returns:
        员工 JSON 列表，每项含材料数量。
    """
    # 统计每个员工的文档（材料）数量
    counts = dict(db.execute(
        select(Document.employee_id, func.count(Document.id)).where(Document.employee_id.is_not(None)).group_by(
            Document.employee_id)).all())
    return [employee_json(item, counts.get(item.id, 0)) for item in
            db.scalars(select(EmployeeProfile).order_by(EmployeeProfile.employee_no)).all()]


@router.post("/employees", status_code=201)
def create_employee(payload: EmployeeInput, db: Session = Depends(get_db)):
    """新增员工；工号冲突时返回 409。

    Args:
        payload: 员工创建请求体。
        db: 数据库会话。

    Returns:
        新员工 JSON。
    """
    # 工号唯一性校验
    if db.scalar(select(EmployeeProfile).where(EmployeeProfile.employee_no == payload.employee_no)):
        raise HTTPException(409, "员工工号已存在")
    item = EmployeeProfile(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    logger.info("employee_created employee_id=%s employee_no=%s name=%s", item.id, item.employee_no, item.name)
    return employee_json(item)


@router.get("/knowledge-bases")
def list_knowledge_bases(db: Session = Depends(get_db)):
    """列出全部知识库及各自材料数量。

    Args:
        db: 数据库会话。

    Returns:
        知识库 JSON 列表，每项含材料数量。
    """
    # 统计每个知识库的文档数量
    counts = dict(db.execute(select(Document.knowledge_base_id, func.count(Document.id)).where(
        Document.knowledge_base_id.is_not(None)).group_by(Document.knowledge_base_id)).all())
    items = db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at)).all()
    return [
        {"id": item.id, "name": item.name, "description": item.description, "permission_scope": item.permission_scope,
         "file_count": counts.get(item.id, 0)} for item in items]


@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(payload: KnowledgeBaseInput, db: Session = Depends(get_db)):
    """新增知识库。

    Args:
        payload: 知识库请求体。
        db: 数据库会话。

    Returns:
        新知识库 JSON（文件数为 0）。
    """
    item = KnowledgeBase(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    logger.info("knowledge_base_created knowledge_base_id=%s name=%s permission_scope=%s", item.id, item.name,
                item.permission_scope)
    return {"id": item.id, "name": item.name, "description": item.description,
            "permission_scope": item.permission_scope, "file_count": 0}


@router.delete("/knowledge-bases/{knowledge_base_id}")
def delete_knowledge_base(knowledge_base_id: UUID, db: Session = Depends(get_db)):
    """删除知识库；库内仍有材料时拒绝删除。

    Args:
        knowledge_base_id: 待删除知识库 id（路径参数）。
        db: 数据库会话。

    Returns:
        删除结果 JSON。
    """
    item = db.get(KnowledgeBase, knowledge_base_id)
    if not item:
        raise HTTPException(404, "知识库不存在")
    # 若库内还有材料则禁止删除
    file_count = db.scalar(select(func.count(Document.id)).where(Document.knowledge_base_id == knowledge_base_id)) or 0
    if file_count:
        raise HTTPException(409, f"知识库中还有 {file_count} 份材料，请先删除或迁移材料")
    db.delete(item)
    db.commit()
    logger.info("knowledge_base_deleted knowledge_base_id=%s name=%s", item.id, item.name)
    return {"id": item.id, "name": item.name, "deleted": True}


@router.get("/documents")
def list_documents(knowledge_base_id: UUID | None = None, db: Session = Depends(get_db)):
    """按创建时间倒序列出文档，并附带各自流水线状态。

    Args:
        knowledge_base_id: 可选的知识库过滤。
        db: 数据库会话。

    Returns:
        文档 JSON 列表，每项含流水线（解析/分块/索引）状态。
    """
    query = select(Document).order_by(Document.created_at.desc())
    if knowledge_base_id:
        query = query.where(Document.knowledge_base_id == knowledge_base_id)
    items = db.scalars(query).all()
    result = []
    for item in items:
        pipeline = build_document_pipeline_payload(db, item.id)
        result.append(
            {
                "id": item.id,
                "material_no": item.material_no,
                "candidate_id": item.candidate_id,
                "employee_name": item.employee.name if item.employee else item.candidate_id,
                "knowledge_base_id": item.knowledge_base_id,
                "title": item.title,
                "document_type": item.document_type,
                "status": item.status,
                "pipeline_status": pipeline["pipeline_status"],
                "parse_status": pipeline["parse_status"],
                "chunk_status": pipeline["chunk_status"],
                "index_status": pipeline["index_status"],
                "created_at": item.created_at,
            }
        )
    return result


@router.post("/documents", status_code=201)
async def create_document(file: UploadFile = File(...), employee_id: UUID | None = Form(None),
                          knowledge_base_id: UUID | None = Form(None), candidate_id: str = Form(""),
                          title: str = Form(...), document_type: str = Form(...),
                          permission_scope: str = Form("hr_private"), db: Session = Depends(get_db)):
    """上传文档文件：入库后立即入队解析任务。

    Args:
        file: 上传的文件。
        employee_id: 可选的关联员工 id。
        knowledge_base_id: 可选的知识库 id。
        candidate_id: 候选人标识（可能为空字符串）。
        title: 文档标题。
        document_type: 文档类型。
        permission_scope: 权限范围。
        db: 数据库会话。

    Returns:
        新建文档的概要 JSON，含解析任务 id。
    """
    logger.info("document_upload_request filename=%s employee_id=%s knowledge_base_id=%s document_type=%s",
                file.filename, employee_id, knowledge_base_id, document_type)
    content = await file.read()
    if not content:
        raise HTTPException(400, "文件内容为空")
    # 校验关联的员工与知识库必须存在
    if employee_id and not db.get(EmployeeProfile, employee_id):
        raise HTTPException(404, "员工不存在")
    if knowledge_base_id and not db.get(KnowledgeBase, knowledge_base_id):
        raise HTTPException(404, "知识库不存在")
    document = upload_document(db, ObjectStore(), filename=file.filename or "upload.bin", content=content,
                               candidate_id=candidate_id, employee_id=employee_id, knowledge_base_id=knowledge_base_id,
                               title=title, document_type=document_type, permission_scope=permission_scope)
    version = current_version(db, document.id)
    # 创建并向队列推送解析任务
    parse_job = queue_parse_job(
        db,
        Redis.from_url(get_settings().redis_url, decode_responses=True),
        version.id,
    )
    logger.info("document_upload_response document_id=%s material_no=%s", document.id, document.material_no)
    return {
        "id": document.id,
        "material_no": document.material_no,
        "candidate_id": document.candidate_id,
        "title": document.title,
        "pipeline_status": "parsing",
        "parse_job_id": parse_job.id,
    }


@router.get("/documents/{document_id}")
def get_document(document_id: UUID, db: Session = Depends(get_db)):
    """查询文档详情：原文、版本、解析/索引任务与产物。

    Args:
        document_id: 文档 id。
        db: 数据库会话。

    Returns:
        文档完整详情 JSON。
    """
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "文档不存在")
    version = current_version(db, document_id)
    # 找到版本对应的原始文件对象
    file_object = db.get(FileObject, version.file_object_id) if version else None
    # 该版本下的解析任务与索引任务（倒序）
    jobs = db.scalars(select(ParseJob).where(ParseJob.document_version_id == version.id).order_by(
        ParseJob.created_at.desc())).all() if version else []
    index_jobs = db.scalars(select(EvidenceIndexJob).where(EvidenceIndexJob.document_version_id == version.id).order_by(
        EvidenceIndexJob.created_at.desc())).all() if version else []
    latest = jobs[0] if jobs else None
    artifacts = db.scalars(select(ParseArtifact).where(ParseArtifact.parse_job_id == latest.id)).all() if latest else []
    store = ObjectStore()
    artifact_items = []
    for item in artifacts:
        content = None
        if item.artifact_type == "markdown":
            # 只有 markdown 产物回传正文，其余用预签名 URL
            content = store.get_bytes(item.object_key).decode("utf-8", errors="replace")
        artifact_items.append({"id": item.id, "type": item.artifact_type, "content_type": item.content_type,
                               "url": store.presigned_get(item.object_key), "content": content})
    pipeline = build_document_pipeline_payload(db, document.id)
    return {"id": document.id, "material_no": document.material_no, "candidate_id": document.candidate_id,
            "employee": {"id": document.employee.id, "employee_no": document.employee.employee_no,
                         "name": document.employee.name} if document.employee else None,
            "knowledge_base": {"id": document.knowledge_base.id,
                               "name": document.knowledge_base.name} if document.knowledge_base else None,
            "title": document.title, "document_type": document.document_type,
            "permission_scope": document.permission_scope, "created_at": document.created_at,
            "updated_at": document.updated_at, "version": {"id": version.id, "version_no": version.version_no,
                                                           "created_at": version.created_at} if version else None,
            "file": {"name": file_object.original_name, "mime_type": file_object.mime_type,
                     "size_bytes": file_object.size_bytes, "bucket_name": file_object.bucket_name,
                     "object_key": file_object.object_key,
                     "preview_url": store.presigned_get(file_object.object_key)} if file_object else None, "jobs": [
            {"id": job.id, "parser_name": job.parser_name, "parser_version": job.parser_version, "status": job.status,
             "progress": job.progress, "error_message": job.error_message, "created_at": job.created_at} for job in
            jobs], "index_jobs": [{"id": job.id, "status": job.status, "embedding_model": job.embedding_model,
                                   "collection_name": job.collection_name, "indexed_count": job.indexed_count,
                                   "retry_count": job.retry_count, "error_message": job.error_message,
                                   "created_at": job.created_at} for job in index_jobs],
            "pipeline_status": pipeline["pipeline_status"], "parse_status": pipeline["parse_status"],
            "chunk_status": pipeline["chunk_status"], "index_status": pipeline["index_status"],
            "artifacts": artifact_items}


@router.post("/documents/{document_id}/parse")
def parse_document_endpoint(document_id: UUID, async_mode: bool = True, db: Session = Depends(get_db)):
    """触发文档解析：异步入队或同步执行。

    Args:
        document_id: 文档 id。
        async_mode: 是否异步（默认 True，入队；False 时同步执行）。
        db: 数据库会话。

    Returns:
        解析任务概要 JSON；异步时为待处理状态，同步时为最终状态。
    """
    logger.info("parse_request_received document_id=%s async_mode=%s", document_id, async_mode)
    version = current_version(db, document_id)
    if not version:
        raise HTTPException(404, "文档版本不存在")
    if async_mode:
        # 异步：创建解析任务并入队，交给 worker 消费
        job = queue_parse_job(
            db,
            Redis.from_url(get_settings().redis_url, decode_responses=True),
            version.id,
        )
        logger.info("parse_job_queued document_id=%s version_id=%s job_id=%s queue=talent:parse:queue", document_id,
                    version.id, job.id)
        return {"id": job.id, "status": job.status, "parser_name": job.parser_name, "error_message": None}
    # 同步：直接调用解析服务当前线程执行并返回最终状态
    job = parse_version(db, ObjectStore(), version.id)
    logger.info("parse_sync_completed document_id=%s version_id=%s job_id=%s status=%s", document_id, version.id,
                job.id, job.status)
    return {"id": job.id, "status": job.status, "parser_name": job.parser_name, "error_message": job.error_message}


@router.get("/documents/{document_id}/chunks")
def get_document_chunks(document_id: UUID, db: Session = Depends(get_db)):
    """查询文档最新一次分块运行及其全部块。

    Args:
        document_id: 文档 id。
        db: 数据库会话。

    Returns:
        含 run（运行概要）与 chunks（分块列表）的 JSON。
    """
    version = current_version(db, document_id)
    if not version:
        raise HTTPException(404, "文档版本不存在")
    run, chunks = latest_chunks(db, version.id)
    if not run:
        return {"run": None, "chunks": []}
    return {
        "run": {
            "id": run.id,
            "strategy": run.strategy.value,
            "status": run.status,
            "chunk_size": run.chunk_size,
            "chunk_overlap": run.chunk_overlap,
            "chunker_version": run.chunker_version,
            "created_at": run.created_at,
        },
        "chunks": [
            {
                "id": chunk.id,
                "stable_key": chunk.stable_key,
                "position": chunk.position,
                "level": chunk.chunk_level,
                "content": chunk.content,
                "element_ids": chunk.element_ids,
                "heading_path": chunk.heading_path,
                "parent_chunk_id": chunk.parent_chunk_id,
                "previous_chunk_id": chunk.previous_chunk_id,
                "next_chunk_id": chunk.next_chunk_id,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "timestamp_start": chunk.timestamp_start,
                "timestamp_end": chunk.timestamp_end,
                "markdown_start": chunk.markdown_start,
                "markdown_end": chunk.markdown_end,
                "source_locators": chunk.source_locators,
            }
            for chunk in chunks
        ],
    }


@router.post("/documents/{document_id}/chunks", status_code=201)
def create_document_chunks(document_id: UUID, payload: ChunkingInput, db: Session = Depends(get_db)):
    """为文档触发一次分块运行。

    Args:
        document_id: 文档 id。
        payload: 分块参数（块大小 / 重叠）。
        db: 数据库会话。

    Returns:
        分块运行概要 JSON。
    """
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "文档不存在")
    version = current_version(db, document_id)
    if not version:
        raise HTTPException(404, "文档版本不存在")
    # 参数合法性校验
    if payload.chunk_overlap >= payload.chunk_size:
        raise HTTPException(422, "chunk_overlap 必须小于 chunk_size")
    try:
        run = create_chunking_run(
            db,
            ObjectStore(),
            document,
            version,
            chunk_size=payload.chunk_size,
            chunk_overlap=payload.chunk_overlap,
        )
    except ValueError as exc:
        # 业务性错误（如尚无成功解析）转为 409
        raise HTTPException(409, str(exc)) from exc
    return {"id": run.id, "strategy": run.strategy.value, "status": run.status}


@router.post("/documents/{document_id}/evidence-index", status_code=202)
def create_evidence_index(document_id: UUID, db: Session = Depends(get_db)):
    """入队一次证据索引任务（同一文档版本重建向量索引）。

    Args:
        document_id: 文档 id。
        db: 数据库会话。

    Returns:
        索引任务概要 JSON。
    """
    version = current_version(db, document_id)
    if not version:
        raise HTTPException(404, "文档版本不存在")
    settings = get_settings()
    job = queue_index_job(
        db,
        Redis.from_url(settings.redis_url, decode_responses=True),
        version.id,
        settings=settings,
    )
    logger.info("evidence_index_job_queued job_id=%s version_id=%s", job.id, version.id)
    return {"id": job.id, "status": job.status, "document_version_id": version.id}


@router.delete("/documents")
def delete_documents(payload: DeleteDocumentsInput = Body(...), db: Session = Depends(get_db)):
    """批量删除文档：清理对象存储与向量索引的整套数据。

    Args:
        payload: 待删除文档 id 列表。
        db: 数据库会话。

    Returns:
        删除结果 JSON，含删除数量与每项结果。
    """
    # 校验所有文档都存在
    missing = [document_id for document_id in payload.document_ids if not db.get(Document, document_id)]
    if missing:
        raise HTTPException(404, f"文档不存在: {missing[0]}")
    store = ObjectStore()
    milvus_store = get_evidence_store()
    # 逐文档级联删除：文件、向量、数据库记录
    items = [delete_document_bundle(db, store, milvus_store, document_id) for document_id in payload.document_ids]
    return {"deleted_count": len(items), "items": items}


@router.post("/evidence/search")
def search_evidence(
        payload: EvidenceSearchInput,
        x_tenant_id: str = Header(...),
        x_permission_scopes: str = Header(...),
        db: Session | None = Depends(get_db),
):
    """稠密向量证据检索：先校验权限范围并做向量查询，再补充上下文。

    Args:
        payload: 检索参数。
        x_tenant_id: 租户 id（请求头）。
        x_permission_scopes: 逗号分隔的权限范围（请求头）。
        db: 可选的数据库会话。

    Returns:
        证据命中 JSON 列表。
    """
    # 从请求头解析权限范围，为空则拒绝
    permission_scopes = [value.strip() for value in x_permission_scopes.split(",") if value.strip()]
    if not permission_scopes:
        raise HTTPException(403, "缺少可用的证据权限范围")
    embedder = get_embedding_model()
    if embedder is None:
        raise HTTPException(503, "Embedding 服务未配置")
    try:
        query_vector = embedder.embed_query(payload.query)
        # 按租户/权限/候选人/文档类型过滤后做向量检索
        results = get_evidence_store().search(
            list(query_vector),
            filters=EvidenceFilter(
                tenant_id=x_tenant_id,
                permission_scopes=permission_scopes,
                candidate_ids=payload.candidate_ids,
                document_types=payload.document_types,
            ),
            limit=payload.limit,
            ef=payload.ef,
        )
    except Exception as exc:
        logger.exception("evidence_search_failed tenant_id=%s", x_tenant_id)
        raise HTTPException(503, f"证据检索失败: {exc}") from exc
    # 补充版本 / 文档 / 员工上下文
    version_ids = [
        UUID(value)
        for item in results
        for value in [item.metadata.get("document_version_id")]
        if value
    ] if db else []
    versions = db.scalars(
        select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))).all() if db and version_ids else []
    version_by_id = {str(item.id): item for item in versions}
    documents = db.scalars(select(Document).where(
        Document.id.in_([item.document_id for item in versions]))).all() if db and versions else []
    document_by_id = {item.id: item for item in documents}
    employees = db.scalars(select(EmployeeProfile).where(EmployeeProfile.id.in_(
        [item.employee_id for item in documents if item.employee_id]))).all() if db and documents else []
    employee_by_id = {item.id: item for item in employees}

    items = []
    for item in results:
        # 反向解析版本/文档/员工，拼装带候选人姓名的结果
        version = version_by_id.get(str(item.metadata.get("document_version_id")))
        document = document_by_id.get(version.document_id) if version else None
        employee = employee_by_id.get(document.employee_id) if document and document.employee_id else None
        items.append(
            {
                "chunk_id": item.chunk_id,
                "candidate_id": item.candidate_id,
                "candidate_name": employee.name if employee else item.candidate_id,
                "document_id": str(document.id) if document else None,
                "document_title": document.title if document else None,
                "material_no": document.material_no if document else None,
                "document_type": item.metadata.get("document_type"),
                "permission_scope": item.metadata.get("permission_scope"),
                "page_start": item.metadata.get("page_start"),
                "page_end": item.metadata.get("page_end"),
                "content": item.content,
                "score": item.score,
                "metadata": item.metadata,
            }
        )
    return items


@router.post("/evidence/hybrid-search")
def hybrid_search_evidence(
        payload: HybridSearchInput,
        x_tenant_id: str = Header(...),
        x_permission_scopes: str = Header(...),
        db: Session | None = Depends(get_db),
):
    """混合检索证据：稠密+稀疏融合后经重排，并补充上下文。

    Args:
        payload: 混合检索参数。
        x_tenant_id: 租户 id（请求头）。
        x_permission_scopes: 逗号分隔的权限范围（请求头）。
        db: 可选的数据库会话。

    Returns:
        证据命中 JSON 列表（按重排得分排序）。
    """
    # 解析权限范围并校验，缺一即拒绝
    permission_scopes = [value.strip() for value in x_permission_scopes.split(",") if value.strip()]
    if not permission_scopes:
        raise HTTPException(403, "缺少可用的证据权限范围")
    embedder = get_embedding_model()
    if embedder is None:
        raise HTTPException(503, "Embedding 服务未配置")
    reranker = get_reranker()
    if reranker is None:
        raise HTTPException(503, "Rerank 服务未配置")
    try:
        results = hybrid_search_evidence_service(
            query=payload.query,
            filters=EvidenceFilter(
                tenant_id=x_tenant_id,
                permission_scopes=permission_scopes,
                candidate_ids=payload.candidate_ids,
                document_types=payload.document_types,
            ),
            store=get_evidence_store(),
            embedder=embedder,
            reranker=reranker,
            limit=payload.limit,
            ef=payload.ef,
            rrf_k=payload.rrf_k,
            rerank_top_n=payload.rerank_top_n,
        )
    except Exception as exc:
        logger.exception("hybrid_search_failed tenant_id=%s", x_tenant_id)
        raise HTTPException(503, f"混合检索失败: {exc}") from exc
    version_by_id, document_by_id, employee_by_id = enrich_evidence_context(db, results)
    return [
        evidence_item_json(item, version_by_id, document_by_id, employee_by_id, score=item.rerank_score)
        for item in results
    ]


@router.post("/talent-search/plan")
def create_talent_query_plan(payload: QueryPlanInput):
    """把自然语言查询编译为结构化查询计划（含候选筛选与检索条件）。

    Args:
        payload: 查询计划请求体。

    Returns:
        编译后的查询计划。
    """
    model = get_chat_model(temperature=0)
    if model is None:
        raise HTTPException(503, "查询计划模型未配置")
    try:
        return compile_query_plan(payload.query, model)
    except Exception as exc:
        logger.exception("query_plan_compile_failed")
        raise HTTPException(503, f"查询计划生成失败: {exc}") from exc


@router.post("/talent-search/candidates")
def query_talent_candidates(
        plan: QueryPlan,
        x_tenant_id: str = Header(...),
        db: Session = Depends(get_db),
):
    """仅按查询计划选出候选人工号集合。

    Args:
        plan: 已编译的查询计划。
        x_tenant_id: 租户 id（请求头）。
        db: 数据库会话。

    Returns:
        候选人工号集合及其数量、语义需求概要。
    """
    if not plan.executable:
        # 计划需要澄清时返回澄清列表
        raise HTTPException(422, {"code": "clarification_required",
                                  "items": [item.model_dump() for item in plan.clarifications]})
    try:
        # 以 PostgreSQL 实时数据为准筛选候选人
        candidate_ids = select_candidate_ids(db, plan, tenant_id=x_tenant_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"candidate_ids": candidate_ids, "candidate_count": len(candidate_ids),
            "semantic_requirements": plan.semantic_requirements}


@router.post("/talent-search")
def search_talent(
        payload: TalentSearchInput,
        x_tenant_id: str = Header(...),
        x_permission_scopes: str = Header(...),
        db: Session = Depends(get_db),
):
    """人才搜索：查询计划→选候选人→混合检索→可选证据包=Markdown 报告原料。

    Args:
        payload: 搜索参数。
        x_tenant_id: 租户 id（请求头）。
        x_permission_scopes: 逗号分隔的权限范围（请求头）。
        db: 数据库会话。

    Returns:
        含查询计划、候选人、搜索结果与（可选）证据包的 JSON。
    """
    # 解析并校验权限范围
    permission_scopes = [value.strip() for value in x_permission_scopes.split(",") if value.strip()]
    if not permission_scopes:
        raise HTTPException(403, "缺少可用的证据权限范围")

    model = get_chat_model(temperature=0)
    embedder = get_embedding_model()
    reranker = get_reranker()
    if model is None:
        raise HTTPException(503, "查询计划模型未配置")
    if embedder is None:
        raise HTTPException(503, "Embedding 服务未配置")
    if reranker is None:
        raise HTTPException(503, "Rerank 服务未配置")

    try:
        # 1) 编译查询计划并校验可执行性
        plan = compile_query_plan(payload.query, model)
        if not plan.executable:
            raise HTTPException(
                422,
                {
                    "code": "clarification_required",
                    "items": [item.model_dump() for item in plan.clarifications],
                },
            )
        # 2) 基于实时数据选出候选人
        candidate_ids = select_candidate_ids(db, plan, tenant_id=x_tenant_id)
        if not candidate_ids:
            # 没有候选人时直接返回空结果（若请求了证据包则标记 no_candidates）
            return {"query_plan": plan, "candidate_ids": [], "searches": [], "chunks": [],
                    **({"evidence_packs": [],
                        "evidence_status": "no_candidates"} if payload.include_evidence_pack else {})}

        store = get_evidence_store() # 获取证据库实例

        # 3) 封装按候选人过滤的混合检索，供逐条语义需求复用
        def run_hybrid_search(query: str):
            return hybrid_search_evidence_service(
                query=query,
                filters=EvidenceFilter(
                    tenant_id=x_tenant_id,
                    permission_scopes=permission_scopes,
                    candidate_ids=candidate_ids,
                ),
                store=store,
                embedder=embedder,
                reranker=reranker,
                limit=payload.limit,
                ef=payload.ef,
                rrf_k=payload.rrf_k,
                rerank_top_n=payload.rerank_top_n,
            )

        searches = []
        result_sets = []
        requirement_ids_by_chunk: dict[str, list[str]] = {}
        # 4) 对每条语义需求执行（可选自动优化）检索
        for requirement in plan.semantic_requirements:
            if payload.retrieval_mode == "auto_optimize": # 自动优化检索
                outcome = search_with_optimization(requirement, search_query=run_hybrid_search, model=model)
            else:
                results = run_hybrid_search(requirement.query) # 执行混合检索
                outcome = {"strategy": None, "queries": [requirement.query], "results": results}
            result_sets.append(outcome["results"])
            # 记录某块命中了哪些语义需求
            for item in outcome["results"]:
                requirement_ids_by_chunk.setdefault(item.chunk_id, []).append(requirement.requirement_id)
            searches.append(
                {
                    "requirement_id": requirement.requirement_id,
                    "strategy": outcome["strategy"],
                    "queries": outcome["queries"],
                    "chunk_count": len(outcome["results"]),
                }
            )

        # 5) 合并各需求结果集并去重排序成最终块列表
        chunks = [
            {
                "chunk_id": item.chunk_id,
                "candidate_id": item.candidate_id,
                "content": item.content,
                "score": item.rerank_score,
                "metadata": item.metadata,
                "requirement_ids": requirement_ids_by_chunk[item.chunk_id],
            }
            for item in merge_query_results(result_sets)
        ]
        response = {
            "query_plan": plan,
            "candidate_ids": candidate_ids,
            "searches": searches,
            "chunks": chunks,
        }
        if payload.include_evidence_pack:
            # 6) 可选：从数据库加载校验过的原文并构建证据包（Markdown 报告原料）
            sources = load_pack_sources(db, chunks, tenant_id=x_tenant_id,
                                        permission_scopes=permission_scopes)
            # 不回传未经校验/越权的索引文本，改用数据库原文
            source_by_id = {row["chunk_id"]: row for row in sources}
            response["chunks"] = [dict(hit, content=source_by_id[hit["chunk_id"]]["content"])
                                  for hit in chunks if hit["chunk_id"] in source_by_id]
            response["evidence_packs"] = build_evidence_packs(
                candidate_ids=candidate_ids,
                requirements=[r.model_dump() for r in plan.semantic_requirements],
                sources=sources, extract=model_extractor(model))
            response["evidence_status"] = "reviewed" if plan.semantic_requirements else "not_requested"
        return response
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("talent_search_failed tenant_id=%s", x_tenant_id)
        raise HTTPException(503, f"人才检索失败: {exc}") from exc


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: UUID, db: Session = Depends(get_db)):
    """重试解析任务：新建一条任务并入解析队列。

    Args:
        job_id: 待重试的解析任务 id。
        db: 数据库会话。

    Returns:
        新任务概要 JSON。
    """
    old = db.get(ParseJob, job_id)
    if not old:
        raise HTTPException(404, "解析任务不存在")
    # 继承版本与累计重试次数，新建一条“queued”任务
    job = ParseJob(document_version_id=old.document_version_id, parser_name="queued", retry_count=old.retry_count + 1)
    db.add(job)
    db.commit()
    Redis.from_url(get_settings().redis_url, decode_responses=True).rpush("talent:parse:queue",
                                                                          f"{job.id}:{job.document_version_id}")
    logger.info("parse_job_retried previous_job_id=%s new_job_id=%s version_id=%s retry_count=%s", old.id, job.id,
                job.document_version_id, job.retry_count)
    return {"id": job.id, "status": job.status, "retry_count": job.retry_count}


@router.post("/index-jobs/{job_id}/retry", status_code=202)
def retry_index_job(job_id: UUID, db: Session = Depends(get_db)):
    """重试失败的证据索引任务：新建任务并入索引队列。

    Args:
        job_id: 待重试的索引任务 id。
        db: 数据库会话。

    Returns:
        新索引任务概要 JSON。
    """
    old = db.get(EvidenceIndexJob, job_id)
    if not old:
        raise HTTPException(404, "索引任务不存在")
    if old.status != IndexStatus.FAILED:
        raise HTTPException(409, "只能重试失败的索引任务")
    # 新建一条 PENDING 任务，继承原任务参数并累计重试次数
    job = EvidenceIndexJob(
        document_version_id=old.document_version_id,
        status=IndexStatus.PENDING,
        embedding_model=old.embedding_model,
        collection_name=old.collection_name,
        retry_count=old.retry_count + 1,
    )
    db.add(job)
    db.commit()
    Redis.from_url(get_settings().redis_url, decode_responses=True).rpush(
        "talent:index:queue", f"{job.id}:{job.document_version_id}"
    )
    logger.info(
        "evidence_index_job_retried previous_job_id=%s new_job_id=%s retry_count=%s",
        old.id,
        job.id,
        job.retry_count,
    )
    return {"id": job.id, "status": job.status, "retry_count": job.retry_count}


@router.get("/jobs/{job_id}/artifacts")
def list_artifacts(job_id: UUID, db: Session = Depends(get_db)):
    """列出某解析任务的全部产物（含预签名下载 URL）。

    Args:
        job_id: 解析任务 id。
        db: 数据库会话。

    Returns:
        产物 JSON 列表。
    """
    items = db.scalars(select(ParseArtifact).where(ParseArtifact.parse_job_id == job_id)).all()
    store = ObjectStore()
    return [{"id": item.id, "type": item.artifact_type, "content_type": item.content_type,
             "url": store.presigned_get(item.object_key)} for item in items]


@router.get("/evidence/citations/{chunk_id}")
def get_evidence_citation(chunk_id: UUID, x_tenant_id: str = Header(...),
                          x_permission_scopes: str = Header(...), quote_start: int | None = None,
                          quote_end: int | None = None, db: Session = Depends(get_db)):
    """解析某分块在原始文档中的引用位置。

    Args:
        chunk_id: 分块 id。
        x_tenant_id: 租户 id（请求头）。
        x_permission_scopes: 逗号分隔的权限范围（请求头）。
        quote_start: 引文起始偏移（可空）。
        quote_end: 引文结束偏移（可空）。
        db: 数据库会话。

    Returns:
        引用解析结果。
    """
    return resolve_citation(db, chunk_id, tenant_id=x_tenant_id,
                            permission_scopes=[s.strip() for s in x_permission_scopes.split(",") if s.strip()],
                            quote_start=quote_start, quote_end=quote_end)