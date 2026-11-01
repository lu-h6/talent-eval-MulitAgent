"""数据库 ORM 模型定义（SQLAlchemy）。

包含业务对象（员工档案、岗位 JD、文档、版本、解析/切块/索引任务等）、
审计与知识库等全部表的映射，以及配套的状态枚举。
"""
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""
    pass


class DocumentStatus(StrEnum):
    """文档生命周期状态。"""
    ACTIVE = "active"  # 生效可用
    INACTIVE = "inactive"  # 停用
    DELETED = "deleted"  # 已删除


class ParseStatus(StrEnum):
    """文档解析任务的状态。"""
    PENDING = "pending"  # 待处理
    RUNNING = "running"  # 运行中
    SUCCEEDED = "succeeded"  # 成功
    PARTIALLY_SUCCEEDED = "partially_succeeded"  # 部分成功
    FAILED = "failed"  # 失败


class ChunkingStatus(StrEnum):
    """分块任务的状态。"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class IndexStatus(StrEnum):
    """向量索引任务的状态。"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ChunkStrategyName(StrEnum):
    """分块策略名称。"""
    RECURSIVE = "recursive"  # 递归分块
    MARKDOWN = "markdown"  # 按 Markdown 结构分块


class EmployeeProfile(Base):
    """员工（候选人）基础档案表。"""
    __tablename__ = "employee_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    employee_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 工号（唯一）
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="course-demo")  # 租户
    name: Mapped[str] = mapped_column(String(128), index=True)  # 姓名
    gender: Mapped[str | None] = mapped_column(String(16))  # 性别
    birth_date: Mapped[date | None] = mapped_column(Date)  # 出生日期（用于换算出年龄）
    region: Mapped[str | None] = mapped_column(String(128))  # 工作地区
    current_position: Mapped[str | None] = mapped_column(String(255))  # 当前岗位
    job_level: Mapped[str | None] = mapped_column(String(64))  # 职级（L1、L2…）
    years_of_experience: Mapped[float | None] = mapped_column(Float)  # 工作年限
    department: Mapped[str | None] = mapped_column(String(255))  # 所属部门
    employment_status: Mapped[str] = mapped_column(String(32), default="active")  # 在职状态
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class JobDescription(Base):
    """企业维护的岗位 JD 表。"""
    __tablename__ = "job_descriptions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 岗位编码（唯一）
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)  # 租户
    name: Mapped[str] = mapped_column(String(255), index=True)  # 岗位名称
    content: Mapped[str] = mapped_column(Text)  # JD 内容
    version: Mapped[int] = mapped_column(Integer, default=1)  # 版本号
    status: Mapped[str] = mapped_column(String(32), default="active")  # 状态
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ToolCallAudit(Base):
    """工具调用审计表（记录每一次工具执行的元信息，不存参数值）。"""
    __tablename__ = "tool_call_audits"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    call_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 调用 ID
    tool_name: Mapped[str] = mapped_column(String(128), index=True)  # 工具名
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)  # 租户
    actor_id: Mapped[str] = mapped_column(String(128))  # 操作者
    run_id: Mapped[str] = mapped_column(String(128), index=True)  # 运行 ID
    argument_keys: Mapped[list[str]] = mapped_column(JSON, default=list)  # 参数键（不含值）
    status: Mapped[str] = mapped_column(String(32))  # 执行状态
    attempts: Mapped[int] = mapped_column(Integer)  # 尝试次数
    duration_ms: Mapped[float] = mapped_column(Float)  # 耗时（毫秒）
    error_code: Mapped[str | None] = mapped_column(String(64))  # 错误码
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeBase(Base):
    """知识库表，用于对一批文档做统一权限管理。"""
    __tablename__ = "knowledge_bases"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="course-demo")  # 租户
    name: Mapped[str] = mapped_column(String(255))  # 知识库名称
    description: Mapped[str | None] = mapped_column(Text)  # 描述
    permission_scope: Mapped[str] = mapped_column(String(64), default="hr_private")  # 权限范围
    status: Mapped[str] = mapped_column(String(32), default="active")  # 状态
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Document(Base):
    """人才材料文档表（一道人才原始材料文件的逻辑实体）。"""
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)  # 归属候选人工号
    material_no: Mapped[str | None] = mapped_column(String(64), unique=True)  # 材料编号
    employee_id: Mapped[UUID | None] = mapped_column(ForeignKey("employee_profiles.id"), index=True)  # 关联员工档案
    knowledge_base_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)  # 关联知识库
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)  # 租户
    title: Mapped[str] = mapped_column(String(255))  # 文档标题
    document_type: Mapped[str] = mapped_column(String(64))  # 文档类型
    source: Mapped[str] = mapped_column(String(64), default="manual_upload")  # 来源
    permission_scope: Mapped[str] = mapped_column(String(64), default="hr_private")  # 权限范围
    confidentiality_level: Mapped[str] = mapped_column(String(32), default="internal")  # 密级
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", values_callable=lambda values: [item.value for item in values]),
        default=DocumentStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    versions: Mapped[list["DocumentVersion"]] = relationship(back_populates="document")  # 版本列表
    employee: Mapped[EmployeeProfile | None] = relationship()  # 员工档案
    knowledge_base: Mapped[KnowledgeBase | None] = relationship()  # 知识库


class FileObject(Base):
    """对象存储中的文件元信息表。"""
    __tablename__ = "file_objects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    bucket_name: Mapped[str] = mapped_column(String(128))  # 存储桶
    object_key: Mapped[str] = mapped_column(String(1024), unique=True)  # 对象键（唯一）
    original_name: Mapped[str] = mapped_column(String(512))  # 原始文件名
    mime_type: Mapped[str] = mapped_column(String(255))  # MIME 类型
    size_bytes: Mapped[int] = mapped_column(BigInteger)  # 文件大小
    sha256: Mapped[str] = mapped_column(String(64), index=True)  # 内容哈希
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentVersion(Base):
    """文档版本表（一个文档的某一快照，指向具体文件）。"""
    __tablename__ = "document_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"))  # 所属文档
    file_object_id: Mapped[UUID] = mapped_column(ForeignKey("file_objects.id"))  # 对应文件
    version_no: Mapped[int] = mapped_column(Integer)  # 版本号
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)  # 是否为当前版本
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document: Mapped[Document] = relationship(back_populates="versions")
    file_object: Mapped[FileObject] = relationship()


class ParseJob(Base):
    """文档解析任务表。"""
    __tablename__ = "parse_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"))  # 目标版本
    parser_name: Mapped[str] = mapped_column(String(64))  # 解析器名
    parser_version: Mapped[str | None] = mapped_column(String(64))  # 解析器版本
    status: Mapped[ParseStatus] = mapped_column(
        Enum(ParseStatus, name="parse_status", values_callable=lambda values: [item.value for item in values]),
        default=ParseStatus.PENDING,
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 进度百分比
    retry_count: Mapped[int] = mapped_column(Integer, default=0)  # 重试次数
    error_code: Mapped[str | None] = mapped_column(String(64))  # 错误码
    error_message: Mapped[str | None] = mapped_column(Text)  # 错误信息
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 开始时间
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 结束时间
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParseArtifact(Base):
    """解析产物表（解析输出的文件制品，存于对象存储）。"""
    __tablename__ = "parse_artifacts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    parse_job_id: Mapped[UUID] = mapped_column(ForeignKey("parse_jobs.id"))  # 所属解析任务
    artifact_type: Mapped[str] = mapped_column(String(64))  # 产物类型
    bucket_name: Mapped[str] = mapped_column(String(128))  # 存储桶
    object_key: Mapped[str] = mapped_column(String(1024))  # 对象键
    content_type: Mapped[str] = mapped_column(String(255))  # 内容类型
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)  # 大小
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # 创建时间


class DocumentMetadata(Base):
    """文档扩展元信息表（键值对、值可以是 JSON）。"""
    __tablename__ = "document_metadata"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id"))  # 所属文档
    metadata_key: Mapped[str] = mapped_column(String(128))  # 元数据键
    metadata_value: Mapped[dict] = mapped_column(JSON)  # 元数据值
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ChunkingRun(Base):
    """分块运行记录表（一次对某解析产物做切块的执行）。"""
    __tablename__ = "chunking_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    parse_job_id: Mapped[UUID] = mapped_column(ForeignKey("parse_jobs.id"), index=True)  # 来源解析任务
    strategy: Mapped[ChunkStrategyName] = mapped_column(
        Enum(ChunkStrategyName, name="chunk_strategy", values_callable=lambda values: [item.value for item in values])
    )  # 分块策略
    status: Mapped[ChunkingStatus] = mapped_column(
        Enum(ChunkingStatus, name="chunking_status", values_callable=lambda values: [item.value for item in values]),
        default=ChunkingStatus.PENDING,
    )
    chunk_size: Mapped[int] = mapped_column(Integer)  # 分块大小
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=0)  # 分块重叠
    chunker_version: Mapped[str] = mapped_column(String(64), default="lesson-5-v1")  # 切块器版本
    error_message: Mapped[str | None] = mapped_column(Text)  # 错误信息
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 结束时间


class DocumentChunk(Base):
    """文档分块表（切分后的最小可检索单元）。"""
    __tablename__ = "document_chunks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    chunking_run_id: Mapped[UUID] = mapped_column(ForeignKey("chunking_runs.id", ondelete="CASCADE"), index=True)  # 所属运行
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)  # 所属版本
    candidate_id: Mapped[str] = mapped_column(String(64), index=True)  # 归属候选人工号
    document_type: Mapped[str] = mapped_column(String(64))  # 文档类型
    permission_scope: Mapped[str] = mapped_column(String(64))  # 权限范围
    stable_key: Mapped[str] = mapped_column(String(64), index=True)  # 稳定键（跨版本稳定定位）
    position: Mapped[int] = mapped_column(Integer)  # 在文档中的位置
    chunk_level: Mapped[str] = mapped_column(String(16), default="child")  # 分块层级（child/parent）
    content: Mapped[str] = mapped_column(Text)  # 分块内容
    element_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # 源元素 ID 列表
    heading_path: Mapped[list[str]] = mapped_column(JSON, default=list)  # 标题层级路径
    parent_chunk_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_chunks.id"))  # 父分块
    previous_chunk_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_chunks.id"))  # 前一兄弟分块
    next_chunk_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_chunks.id"))  # 后一兄弟分块
    page_start: Mapped[int | None] = mapped_column(Integer)  # 起始页码
    page_end: Mapped[int | None] = mapped_column(Integer)  # 结束页码
    timestamp_start: Mapped[float | None] = mapped_column(Float)  # 起始时间戳（视频等）
    timestamp_end: Mapped[float | None] = mapped_column(Float)  # 结束时间戳
    markdown_start: Mapped[int | None] = mapped_column(Integer)  # 在 Markdown 中的起始位置
    markdown_end: Mapped[int | None] = mapped_column(Integer)  # 在 Markdown 中的结束位置
    source_locators: Mapped[list[dict]] = mapped_column(JSON, default=list)  # 源定位器列表
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceIndexJob(Base):
    """证据向量索引任务表。"""
    __tablename__ = "evidence_index_jobs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("document_versions.id"), index=True)  # 目标版本
    status: Mapped[IndexStatus] = mapped_column(
        Enum(IndexStatus, name="index_status", values_callable=lambda values: [item.value for item in values]),
        default=IndexStatus.PENDING,
    )
    embedding_model: Mapped[str] = mapped_column(String(128))  # 使用的向量化模型
    collection_name: Mapped[str] = mapped_column(String(128))  # 目标集合名
    indexed_count: Mapped[int] = mapped_column(Integer, default=0)  # 已索引条数
    retry_count: Mapped[int] = mapped_column(Integer, default=0)  # 重试次数
    error_message: Mapped[str | None] = mapped_column(Text)  # 错误信息
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 开始时间
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 结束时间
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# 多轮会话 / 运行历史 / 评测（human-eval）相关表
# 与 database/migrations/020~024 的建表脚本一一对应，供对话运行记录、
# 运行步骤流、评测用例、人工反馈与标注、评测数据集使用。
# ---------------------------------------------------------------------------


class TalentRunRecord(Base):
    """人才评估运行记录表（一轮请求对应一次运行）。"""
    __tablename__ = "talent_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_no: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    report: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TalentConversationRecord(Base):
    """人才评估会话表（一个会话包含多轮运行）。"""
    __tablename__ = "talent_conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True)
    latest_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TalentRunStepRecord(Base):
    """运行步骤表：记录单次运行中各节点的进度、耗时与输出。"""
    __tablename__ = "talent_run_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_id", name="uq_talent_run_step"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("talent_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    step_id: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    completed: Mapped[int | None] = mapped_column(Integer)
    total: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)


class EvaluationCaseRecord(Base):
    """评测用例表：把一次运行中的某个模块调用固化为可回放的用例。"""
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint(
            "source_run_id",
            "module_key",
            "invocation_no",
            name="uq_evaluation_case_invocation",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_run_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_no: Mapped[int] = mapped_column(Integer)
    module_key: Mapped[str] = mapped_column(String(64), index=True)
    invocation_no: Mapped[int] = mapped_column(Integer)
    input_payload: Mapped[dict] = mapped_column(JSON)
    output_payload: Mapped[dict] = mapped_column(JSON)
    replay_context: Mapped[dict] = mapped_column(JSON, default=dict)
    version_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="succeeded")
    error_code: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    annotation_status: Mapped[str] = mapped_column(String(32), default="unlabeled")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class EvaluationFeedbackRecord(Base):
    """评测反馈表：用户对某次运行或某个用例给出的反馈。"""
    __tablename__ = "evaluation_feedback"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_run_id: Mapped[str] = mapped_column(ForeignKey("talent_runs.run_id"), index=True)
    case_id: Mapped[UUID | None] = mapped_column(ForeignKey("evaluation_cases.id"), index=True)
    feedback_scope: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationAnnotationRecord(Base):
    """评测标注表：模型 Judge 或人工给出的评分、结论与理由。"""
    __tablename__ = "evaluation_annotations"

    case_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_cases.id"), primary_key=True)
    annotator_type: Mapped[str] = mapped_column(String(16))
    judge_version: Mapped[str] = mapped_column(String(64))
    judge_model: Mapped[str | None] = mapped_column(String(128))
    score: Mapped[int | None] = mapped_column(Integer)
    verdict: Mapped[str] = mapped_column(String(32))
    error_types: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text)
    golden_candidate: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationDatasetItemRecord(Base):
    """评测数据集条目表：由用例沉淀出的黄金输入/输出。"""
    __tablename__ = "evaluation_dataset_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_case_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_cases.id"), unique=True)
    module_key: Mapped[str] = mapped_column(String(64))
    input_payload: Mapped[dict] = mapped_column(JSON)
    golden_output: Mapped[dict] = mapped_column(JSON)
    replay_context: Mapped[dict] = mapped_column(JSON)
    dataset_split: Mapped[str] = mapped_column(String(16), default="test")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())