from __future__ import annotations

import json
import logging
import operator
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

from app.retrieval.evidence_pack import build_evidence_packs
from app.retrieval.query_plan import FilterCondition
from app.talent.talent_decision_graph import DecisionContext
from app.talent.talent_tools import TalentToolContext, ToolExecutor

logger = logging.getLogger(__name__)


class ScoreAnchor(BaseModel):
    """评分锚点：固定为 0 / 3 / 5 分的取值与说明。"""
    score: Literal[0, 3, 5]
    description: str = Field(min_length=1, max_length=300)


class EvaluationDimension(BaseModel):
    """单个可取证的动态评估维度。"""
    name: str = Field(min_length=1, max_length=80)  # 维度名称
    definition: str = Field(min_length=1, max_length=500)  # 维度定义
    weight_percent: int = Field(ge=1, le=100)  # 权重百分比
    evidence_requirements: list[str] = Field(min_length=1, max_length=8)  # 若干取证要求
    score_anchors: list[ScoreAnchor] = Field(min_length=3, max_length=3)  # 三个评分锚点
    retrieval_hints: list[str] = Field(min_length=1, max_length=8)  # 检索提示关键词
    source_requirement_ids: list[str] = Field(min_length=1, max_length=8)  # 来源要求ID

def _branch_query(dimension, requirement):
    """构建取证分支的查询。"""
    terms = list(dict.fromkeys([*dimension.retrieval_hints, requirement]))
    return " ".join(terms)[:500]

class EvaluationDimensionPlan(BaseModel):
    """评估维度计划：一份维度集合。"""
    dimensions: list[EvaluationDimension] = Field(min_length=1, max_length=6)


class DimensionValidationIssue(BaseModel):
    """维度校验问题。"""
    code: str  # 问题码
    message: str  # 问题描述
    dimension_numbers: list[int] = Field(default_factory=list)  # 关联的维度序号


class AssessmentWorkItem(BaseModel):
    """单个取证分支工作项：某候选人 × 某评估维度。"""
    task_id: str  # 任务ID
    candidate_id: str  # 候选人工号
    dimension_number: int = Field(ge=1)  # 维度序号
    dimension: EvaluationDimension  # 维度定义


class EvidenceSourceRef(BaseModel):
    """证据来源引用（回引自材料原文）。"""
    citation_id: str
    chunk_id: str  # 分块ID
    quote: str = Field(min_length=1)  # 原文引文
    quote_start: int = Field(ge=0)  # 引文起始位置
    quote_end: int = Field(gt=0)  # 引文结束位置
    source_label: str = Field(min_length=1)  # 来源标签


class BranchEvidenceFact(BaseModel):
    """单条可复核的事实断言。"""
    event: str  # 事件
    period: str  # 时间区间
    claim: str  # 断言
    answer: Literal["yes", "no"]  # 是否支持
    sources: list[EvidenceSourceRef] = Field(default_factory=list)  # 来源引用


class EvidenceCitationRef(BaseModel):
    """证据引用（供报告回引）。"""
    citation_id: str
    chunk_id: str  # 分块ID
    source_label: str = Field(min_length=1)  # 来源标签


class RequirementEvidence(BaseModel):
    """单条评估要求的证据结果。"""
    requirement_id: str  # 要求ID
    query: str  # 检索查询
    status: Literal["sufficient", "partial", "missing", "conflicting"]  # 证据充分度
    reason: str  # 状态说明
    extraction_status: Literal["not_run", "succeeded", "failed"]  # 抽取状态
    facts: list[BranchEvidenceFact] = Field(default_factory=list)  # 事实断言列表
    conflicts: list[list[int]] = Field(default_factory=list)  # 冲突事实对
    missing_information: list[str] = Field(default_factory=list)  # 缺失信息
    citations: list[EvidenceCitationRef] = Field(default_factory=list)  # 引用列表


class BranchEvidenceDraft(BaseModel):
    """单个取证分支的输出草稿。"""
    model_config = ConfigDict(extra="forbid")

    task_id: str  # 任务ID
    candidate_id: str  # 候选人工号
    dimension_number: int = Field(ge=1)  # 维度序号
    execution_status: Literal["succeeded", "degraded", "failed"] = Field(
        validation_alias=AliasChoices("execution_status", "status")
    )  # 执行状态
    requirements: list[RequirementEvidence] = Field(default_factory=list)  # 各要求证据
    tool_call_ids: list[str] = Field(default_factory=list)  # 工具调用ID
    error_code: str | None = None  # 错误码
    error_message: str | None = None  # 错误信息


class TalentEvaluationDispatchState(TypedDict, total=False):
    """
    评估分派执行子图的图状态。
    """
    talent_request: dict[str, Any]  # 结构化人才请求
    query_plan: dict[str, Any]  # 查询计划
    dimensions: list[dict[str, Any]]  # 评估维度
    candidate_ids: list[str]  # 候选人工号
    candidates_preselected: bool  # 是否已预选候选人
    work_items: list[dict[str, Any]]  # 取证工作项
    branch_results: Annotated[list[dict[str, Any]], operator.add]  # 分支结果（追加合并）
    validation_issues: list[dict[str, Any]]  # 维度校验问题
    required_work_items: int  # 必要工作项数
    work_item_limit: int  # 工作项上限
    status: str  # 当前状态
    errors: list[str]  # 错误列表


DimensionGenerator = Callable[[dict[str, Any], dict[str, Any]],EvaluationDimensionPlan,]  # 维度生成器
CandidateProvider = Callable[[dict[str, Any], DecisionContext], list[str]]  # 候选人提供者
BranchWorker = Callable[[AssessmentWorkItem, DecisionContext], BranchEvidenceDraft]  # 分支工作器
ModelProvider = Callable[[], Any]  # 模型提供者
EvidenceSearch = Callable[..., list[Any]]  # 证据检索函数
EvidenceSourceLoader = Callable[..., list[dict[str, Any]]]  # 证据来源加载函数


DIMENSION_GENERATOR_PROMPT = """你负责把已编译的人才要求转成可取证的动态评估维度。
只允许根据 semantic_conditions 和 evaluation_preferences 生成维度。
hard_conditions 已用于候选人过滤，不得再生成评分维度。
语义要求的来源使用原 requirement_id，偏好按顺序使用 preference:1、preference:2。
维度之间不得重复，必须能通过候选人档案或原始材料取证。
权重使用整数百分比且合计为 100。评分锚点固定为 0、3、5 分。
retrieval_hints 应给出可直接用于候选人证据检索的关键词组合。
""".strip()

class BranchExecutionError(RuntimeError):
    """分支执行错误：包含错误码与消息。"""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def build_branch_evidence_provider(
    *,
    search: EvidenceSearch,
    load_sources: EvidenceSourceLoader,
    extract: Callable[[dict[str, Any], list[dict[str, Any]]], Any],
):
    """
    构造分支证据提供者：返回授权分块及 Candidate Evidence Pack 2.0。

    Args:
        search: 证据检索函数
        load_sources: 证据来源加载函数
        extract: 证据抽取函数

    Returns:
        分支证据提供者
    """

    def evidence_provider(
        *,
        query: str,
        candidate_ids: list[str],
        tenant_id: str,
        permission_scopes: list[str],
        include_evidence_pack: bool,
    ) -> dict[str, Any]:
        """
        分支证据提供者：返回授权分块及 Candidate Evidence Pack 2.0。

        Args:
            query: 检索查询
            candidate_ids: 候选人工号
            tenant_id: 租户ID
            permission_scopes: 权限范围
            include_evidence_pack: 是否包含证据包

        Returns:
            分支证据提供者
        """
        logger.info(
            "event=evidence_provider_started function=evidence_provider "
            "candidate_ids=%s query_chars=%s permission_scope_count=%s "
            "include_evidence_pack=%s",
            candidate_ids, len(query), len(permission_scopes), include_evidence_pack,
        )
        # 先在候选人范围内执行混合检索
        ranked = search(
            query=query,
            candidate_ids=candidate_ids,
            tenant_id=tenant_id,
            permission_scopes=permission_scopes,
        )
        logger.info(
            "event=evidence_search_completed function=evidence_provider "
            "candidate_ids=%s hit_count=%s chunk_ids=%s",
            candidate_ids, len(ranked), [item.chunk_id for item in ranked],
        )
        # 将检索命中映射为统一的分块字典
        hits = [
            {
                "chunk_id": item.chunk_id,
                "candidate_id": item.candidate_id,
                "content": item.content,
                "score": item.rerank_score,
                "metadata": item.metadata,
                "requirement_ids": ["branch_requirement"],
            }
            for item in ranked
        ]
        # 依据实时 PostgreSQL 数据加载可信来源（权限校验）
        trusted_sources = load_sources(
            hits,
            tenant_id=tenant_id,
            permission_scopes=permission_scopes,
        )
        logger.info(
            "event=evidence_sources_loaded function=evidence_provider "
            "candidate_ids=%s requested_chunk_count=%s trusted_source_count=%s "
            "trusted_chunk_ids=%s",
            candidate_ids, len(hits), len(trusted_sources),
            [item["chunk_id"] for item in trusted_sources],
        )
        # 需要时抽取并组装 Candidate Evidence Pack 2.0
        evidence_packs = (
            build_evidence_packs(
                candidate_ids=candidate_ids,
                requirements=[
                    {
                        "requirement_id": "branch_requirement",  # 分支要求ID
                        "query": query,
                    }
                ],
                sources=trusted_sources,
                extract=extract,
            )
            if include_evidence_pack
            else []
        )
        logger.info(
            "event=evidence_provider_completed function=evidence_provider "
            "candidate_ids=%s trusted_source_count=%s evidence_pack_count=%s",
            candidate_ids, len(trusted_sources), len(evidence_packs),
        )
        return {
            "candidate_ids": candidate_ids,
            "chunks": trusted_sources,
            "evidence_packs": evidence_packs,
        }

    return evidence_provider


def build_structured_dimension_generator(
    model_provider: ModelProvider,
) -> DimensionGenerator:
    """
    构造维度生成器：由模型基于人才请求与查询计划生成评估维度计划。

    Args:
        model_provider: 模型提供者

    Returns:
        维度生成器
    """
    def generate(
        talent_request: dict[str, Any],
        query_plan: dict[str, Any],
    ) -> EvaluationDimensionPlan:
        """
        维度生成器：由模型基于人才请求与查询计划生成评估维度计划。

        Args:
            talent_request: 人才请求
            query_plan: 查询计划

        Returns:
            评估维度计划
        """
        logger.info(
            "event=dimension_generation_started "
            "function=build_structured_dimension_generator.generate "
            "semantic_condition_count=%s preference_count=%s filter_count=%s",
            len(talent_request.get("semantic_conditions", [])),
            len(talent_request.get("evaluation_preferences", [])),
            len(query_plan.get("filters", [])),
        )
        model = model_provider()
        if model is None:
            raise RuntimeError("评估维度生成模型未配置")
        payload = {
            "talent_request": talent_request,
            "query_plan": query_plan,
        }
        # 以结构化输出模式让模型生成维度计划
        result = model.with_structured_output(EvaluationDimensionPlan).invoke(
            [
                ("system", DIMENSION_GENERATOR_PROMPT),
                ("user", json.dumps(payload, ensure_ascii=False)),
            ]
        )
        logger.info(
            "event=dimension_generation_completed "
            "function=build_structured_dimension_generator.generate "
            "dimension_count=%s dimension_names=%s weight_total=%s",
            len(result.dimensions),
            [item.name for item in result.dimensions],
            sum(item.weight_percent for item in result.dimensions),
        )
        return result

    return generate


def _branch_query(dimension: EvaluationDimension, requirement: str) -> str:
    """
    按维度检索提示与要求关键词组合构建分支检索查询。

    """
    terms = list(dict.fromkeys([*dimension.retrieval_hints, requirement]))
    return " ".join(terms)[:500]


def _citation_refs(raw_citations: list[Any]) -> list[dict[str, str]]:
    """
    规范化证据引用列表（去重 chunk_id，并解析来源标签）。
    """
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_citations:
        if not isinstance(raw, dict):
            continue
        chunk_id = raw.get("chunk_id")
        citation_id = raw.get("citation_id") or chunk_id
        if not isinstance(chunk_id, str) or not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        refs.append(
            {
                "citation_id": str(citation_id),
                "chunk_id": chunk_id,
                "source_label": str(
                    raw.get("document_title")
                    or raw.get("source_label")
                    or chunk_id
                ),
            }
        )
    return refs


def _missing_requirement(
    *,
    requirement_id: str,
    query: str,
    requirement: str,
    reason: str,
) -> RequirementEvidence:
    """
    构造缺失证据的 RequirementEvidence（未运行抽取）。
    Args:
        requirement_id: 证据要求ID
        query: 检索查询
        requirement: 证据要求
        reason: 缺失原因

    Returns:
        缺失证据的 RequirementEvidence
    """
    return RequirementEvidence(
        requirement_id=requirement_id,
        query=query,
        status="missing",
        reason=reason,
        extraction_status="not_run",
        missing_information=[requirement],
    )


def build_evidence_branch_worker(service: Any, executor: ToolExecutor) -> BranchWorker:
    """
    构造证据分支工作器：无需额外 ReAct 循环即可准备评估就绪的证据。
    """

    def branch_worker(
        work_item: AssessmentWorkItem,
        context: DecisionContext,
    ) -> BranchEvidenceDraft:
        """
        证据分支工作器：针对评估任务中的某条证据要求执行证据检索与抽取。
        Args:
            work_item: 评估任务工作项
            context: 决策上下文

        Returns:
            证据分支工作结果
        """
        logger.info(
            "event=branch_worker_started function=branch_worker task_id=%s "
            "candidate_id=%s dimension_number=%s requirement_count=%s",
            work_item.task_id, work_item.candidate_id,
            work_item.dimension_number,
            len(work_item.dimension.evidence_requirements),
        )
        # 构造工具调用上下文（继承租户与权限）
        tool_context = TalentToolContext(
            tenant_id=context.tenant_id,
            permission_scopes=tuple(context.permission_scopes),
            actor_id="evaluation-branch-worker",
            run_id=work_item.task_id,
        )
        requirement_results: list[RequirementEvidence] = []
        call_ids: list[str] = []
        degraded = False

        def evaluate_requirement(
            index: int,
            requirement: str,
        ) -> tuple[RequirementEvidence, str | None, bool]:
            """
            针对某条取证要求执行证据检索与抽取，返回（结果, call_id, 是否降级）。
            Args:
                index: 证据要求索引
                requirement: 证据要求

            Returns:
                证据结果、调用ID、是否降级
            """
            requirement_id = f"{work_item.dimension_number}:{index}"
            query = _branch_query(work_item.dimension, requirement)
            logger.info(
                "event=branch_requirement_started function=branch_worker "
                "task_id=%s candidate_id=%s dimension_number=%s "
                "requirement_id=%s query_chars=%s",
                work_item.task_id, work_item.candidate_id,
                work_item.dimension_number, requirement_id, len(query),
            )
            # 通过执行器调用树工具检索证据（带重试/超时/熔断/审计）
            envelope = executor.execute(
                "search_candidate_evidence",
                lambda : service.search_candidate_evidence(
                    query,
                    [work_item.candidate_id],
                    context=tool_context,
                ),
                context=tool_context,
                arguments={
                    "query": query,
                    "candidate_ids": [work_item.candidate_id],
                },
            )
            meta = envelope.get("meta") or {}
            call_id = meta.get("call_id")
            normalized_call_id = call_id if isinstance(call_id, str) and call_id else None
            # 检索失败：返回缺失证据并标记降级
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                logger.error(
                    "event=branch_requirement_failed function=branch_worker "
                    "task_id=%s candidate_id=%s dimension_number=%s "
                    "requirement_id=%s call_id=%s error_code=%s degraded=%s",
                    work_item.task_id, work_item.candidate_id,
                    work_item.dimension_number, requirement_id, call_id,
                    error.get("code") or "tool_failed", meta.get("degraded"),
                )
                return (
                    _missing_requirement(
                        requirement_id=requirement_id,
                        query=query,
                        requirement=requirement,
                        reason=str(error.get("code") or "tool_failed"),
                    ),
                    normalized_call_id,
                    True,
                )

            data = envelope.get("data") or {}
            # 取出属于当前候选人的 Evidence Pack
            pack = next(
                (
                    item
                    for item in data.get("evidence_packs", [])
                    if item.get("candidate_id") == work_item.candidate_id
                ),
                None,
            )
            raw_requirement = (
                pack.get("requirements", [None])[0]
                if isinstance(pack, dict) and pack.get("requirements")
                else None
            )
            # 证据包缺失：返回缺失证据
            if not isinstance(raw_requirement, dict):
                logger.error(
                    "event=branch_requirement_failed function=branch_worker "
                    "task_id=%s candidate_id=%s dimension_number=%s "
                    "requirement_id=%s call_id=%s error_code=evidence_pack_missing",
                    work_item.task_id, work_item.candidate_id,
                    work_item.dimension_number, requirement_id, call_id,
                )
                return (
                    _missing_requirement(
                        requirement_id=requirement_id,
                        query=query,
                        requirement=requirement,
                        reason="evidence_pack_missing",
                    ),
                    normalized_call_id,
                    True,
                )
            # 组装 RequirementEvidence（修正 requirement_id/query 并规范化引用）
            result = RequirementEvidence.model_validate(
                {
                    **raw_requirement,
                    "requirement_id": requirement_id,
                    "query": query,
                    "citations": _citation_refs(
                        raw_requirement.get("citations", [])
                    ),
                }
            )
            logger.info(
                "event=branch_requirement_completed function=branch_worker "
                "task_id=%s candidate_id=%s dimension_number=%s "
                "requirement_id=%s call_id=%s status=%s reason=%s "
                "extraction_status=%s fact_count=%s citation_count=%s",
                work_item.task_id, work_item.candidate_id,
                work_item.dimension_number, requirement_id, call_id,
                result.status, result.reason, result.extraction_status,
                len(result.facts), len(result.citations),
            )
            return result, normalized_call_id, bool(meta.get("degraded"))

        # 并行执行该维度下的所有取证要求
        indexed_requirements = list(
            enumerate(work_item.dimension.evidence_requirements, start=1)
        )
        with ThreadPoolExecutor(
            max_workers=len(indexed_requirements),
            thread_name_prefix="evaluation-requirement",
        ) as pool:
            futures = [
                pool.submit(evaluate_requirement, index, requirement)
                for index, requirement in indexed_requirements
            ]
            outcomes = [future.result() for future in futures]

        # 汇总各要求结果、工具调用ID，并聚合降级标记
        for result, call_id, requirement_degraded in outcomes:
            requirement_results.append(result)
            if call_id is not None:
                call_ids.append(call_id)
            degraded = degraded or requirement_degraded # 只要有一次requirement 结果 degraded 就算 degraded

        branch_result = BranchEvidenceDraft(
            task_id=work_item.task_id,
            candidate_id=work_item.candidate_id,
            dimension_number=work_item.dimension_number,
            execution_status="degraded" if degraded else "succeeded",
            requirements=requirement_results,
            tool_call_ids=call_ids,
        )
        logger.info(
            "event=branch_worker_completed function=branch_worker task_id=%s "
            "candidate_id=%s dimension_number=%s execution_status=%s "
            "requirement_count=%s tool_call_count=%s",
            work_item.task_id, work_item.candidate_id,
            work_item.dimension_number, branch_result.execution_status,
            len(branch_result.requirements), len(branch_result.tool_call_ids),
        )
        return branch_result

    return branch_worker


def build_query_plan_candidate_provider(service: Any) -> CandidateProvider:
    """构造候选人提供者：从查询计划解析过滤条件并调用服务筛选。"""
    def candidate_provider(
        query_plan: dict[str, Any],
        context: DecisionContext,
    ) -> list[str]:
        filters = [
            FilterCondition.model_validate(item)
            for item in query_plan.get("filters", [])
        ]
        return service.filter_candidates(
            filters,
            context=TalentToolContext(
                tenant_id=context.tenant_id,
                permission_scopes=tuple(context.permission_scopes),
                actor_id="evaluation-dispatch",
                run_id="dispatch",
            ),
        )

    return candidate_provider


def validate_dimension_plan(
    plan: EvaluationDimensionPlan,
) -> list[DimensionValidationIssue]:
    """
    校验维度计划的权重合计必须为 100。
    """
    total = sum(item.weight_percent for item in plan.dimensions)
    if total == 100:
        return []
    return [
        DimensionValidationIssue(
            code="weight_total_invalid",
            message=f"维度权重合计必须为 100，当前为 {total}",
        )
    ]


def _receive_input(
    state: TalentEvaluationDispatchState,
    runtime: Runtime[DecisionContext],
) -> dict[str, Any]:
    """
    接收并校验评估输入：校验上下文、人才请求与查询计划。
    """
    logger.info(
        "event=graph_node_started function=_receive_input node=receive_input "
        "tenant_id=%s permission_scope_count=%s has_talent_request=%s "
        "has_query_plan=%s",
        runtime.context.tenant_id, len(runtime.context.permission_scopes),
        bool(state.get("talent_request")), bool(state.get("query_plan")),
    )
    if not runtime.context.tenant_id.strip():
        raise ValueError("Runtime Context 中的 tenant_id 不能为空")
    if not runtime.context.permission_scopes:
        raise ValueError("Runtime Context 中的 permission_scopes 不能为空")
    if not state.get("talent_request"):
        raise ValueError("talent_request 不能为空")
    if not state.get("query_plan"):
        raise ValueError("query_plan 不能为空")
    candidates_preselected = "candidate_ids" in state
    return {
        "dimensions": [],
        "candidate_ids": list(state.get("candidate_ids", [])),
        "candidates_preselected": candidates_preselected,
        "work_items": [],
        "branch_results": [],
        "validation_issues": [],
        "required_work_items": 0,
        "work_item_limit": 0,
        "status": "received",
        "errors": [],
    }


def _generate_dimensions(dimension_generator: DimensionGenerator):
    """
    构造“生成维度”节点：调用维度生成器生成评估维度计划。
    Args:
        dimension_generator: 维度生成器
    Returns:
        生成的评估维度计划
    """
    def generate_dimensions(state: TalentEvaluationDispatchState) -> dict[str, Any]:
        plan = dimension_generator(
            state["talent_request"],
            state["query_plan"],
        )
        logger.info(
            "event=graph_node_completed function=generate_dimensions "
            "node=generate_dimensions dimension_count=%s dimension_names=%s",
            len(plan.dimensions), [item.name for item in plan.dimensions],
        )
        return {
            "dimensions": [item.model_dump(mode="json") for item in plan.dimensions],
            "status": "dimensions_generated",
        }

    return generate_dimensions


def _validate_dimensions(state: TalentEvaluationDispatchState) -> dict[str, Any]:
    """
    校验维度计划（权重合计是否合法）。
    """
    plan = EvaluationDimensionPlan(dimensions=state["dimensions"])
    issues = validate_dimension_plan(plan)
    logger.info(
        "event=graph_node_completed function=_validate_dimensions "
        "node=validate_dimensions issue_count=%s weight_total=%s",
        len(issues), sum(item.weight_percent for item in plan.dimensions),
    )
    return {
        "validation_issues": [item.model_dump(mode="json") for item in issues],
        "status": "dimensions_valid" if not issues else "dimension_invalid",
    }


def _route_dimension_validation(
    state: TalentEvaluationDispatchState,
) -> Literal["retrieve_candidates", "prepare_work_items", "dimension_invalid"]:
    """
    维度校验后的路由：有问题→维度非法；已预选→进入工作项；否则检索候选人。
    """
    if state.get("validation_issues"):  # 如果校验问题
        return "dimension_invalid"  # 维度非法
    if state.get("candidates_preselected"):  # 如果已预选候选人
        return "prepare_work_items"  # 进入工作项
    return "retrieve_candidates"  # 检索候选人


def _retrieve_candidates(candidate_provider: CandidateProvider):
    """
    构造“检索候选人”节点：调用候选人提供者筛选候选人。
    """
    def retrieve_candidates(
        state: TalentEvaluationDispatchState,
        runtime: Runtime[DecisionContext],
    ) -> dict[str, Any]:
        candidate_ids = candidate_provider(state["query_plan"], runtime.context)
        logger.info(
            "event=graph_node_completed function=retrieve_candidates "
            "node=retrieve_candidates candidate_count=%s candidate_ids=%s",
            len(candidate_ids), candidate_ids,
        )
        return {
            "candidate_ids": candidate_ids,
            "status": "candidates_ready" if candidate_ids else "no_candidates",
        }

    return retrieve_candidates


def _prepare_work_items(max_work_items: int):
    """
    构造“准备工作项”节点：生成候选人×维度的取证工作项并做容量检查。
    """
    def prepare_work_items(state: TalentEvaluationDispatchState) -> dict[str, Any]:
        dimensions = [
            EvaluationDimension.model_validate(item) for item in state["dimensions"]
        ]
        required_work_items = len(state["candidate_ids"]) * len(dimensions)
        if required_work_items > max_work_items:
            logger.error(
                "event=graph_capacity_exceeded function=prepare_work_items "
                "node=prepare_work_items candidate_count=%s dimension_count=%s "
                "required_work_items=%s work_item_limit=%s",
                len(state["candidate_ids"]), len(dimensions),
                required_work_items, max_work_items,
            )
            return {
                "work_items": [],
                "required_work_items": required_work_items,
                "work_item_limit": max_work_items,
                "status": "capacity_exceeded",
            }
        numbered_dimensions = list(enumerate(dimensions, start=1))
        work_items = [
            AssessmentWorkItem(
                task_id=f"{candidate_id}:{dimension_number}",
                candidate_id=candidate_id,
                dimension_number=dimension_number,
                dimension=dimension,
            ).model_dump(mode="json")
            for candidate_id in state["candidate_ids"]
            for dimension_number, dimension in numbered_dimensions
        ]
        logger.info(
            "event=graph_node_completed function=prepare_work_items "
            "node=prepare_work_items candidate_count=%s dimension_count=%s "
            "work_item_count=%s task_ids=%s",
            len(state["candidate_ids"]), len(dimensions), len(work_items),
            [item["task_id"] for item in work_items],
        )
        return {
            "work_items": work_items,
            "required_work_items": required_work_items,
            "work_item_limit": max_work_items,
            "status": "work_items_ready" if work_items else "no_candidates",
        }

    return prepare_work_items


def build_work_item_sends(
    work_items: list[dict[str, Any]],
) -> list[Send]:
    """
    将工作项转换为 Send 指令，以并行分派给取证分支节点。
    """
    logger.info(
        "event=work_items_dispatched function=build_work_item_sends "
        "work_item_count=%s task_ids=%s",
        len(work_items), [item["task_id"] for item in work_items],
    )
    return [
        Send("run_assessment_branch", {"work_item": item})
        for item in work_items
    ]


def _dispatch_work_items(state: TalentEvaluationDispatchState):
    """
    工作项分派路由：超限→结束；无工作项→无候选人；否则并行 Send。
    """
    if state.get("status") == "capacity_exceeded":
        return "capacity_exceeded"  # 容量超限
    if not state.get("work_items"):  # 如果无工作项
        return "no_candidates"  # 无候选人
    return build_work_item_sends(state["work_items"])  # 并行分派工作项


def _run_assessment_branch(branch_worker: BranchWorker):
    """
    构造“取证分支”节点：为单个工作项执行证据检索，并以失败兜底。
    """
    def run_assessment_branch(
        state: dict[str, Any],
        runtime: Runtime[DecisionContext],
    ) -> dict[str, Any]:
        work_item = AssessmentWorkItem.model_validate(state["work_item"])
        logger.info(
            "event=graph_branch_started function=run_assessment_branch "
            "node=run_assessment_branch task_id=%s candidate_id=%s dimension_number=%s",
            work_item.task_id, work_item.candidate_id,
            work_item.dimension_number,
        )
        try:
            result = branch_worker(work_item, runtime.context)
        except TimeoutError as exc:
            logger.error(
                "event=graph_branch_failed function=run_assessment_branch "
                "node=run_assessment_branch task_id=%s candidate_id=%s "
                "dimension_number=%s error_code=branch_timeout error_type=%s",
                work_item.task_id, work_item.candidate_id,
                work_item.dimension_number, type(exc).__name__,
            )
            result = BranchEvidenceDraft(
                task_id=work_item.task_id,
                candidate_id=work_item.candidate_id,
                dimension_number=work_item.dimension_number,
                execution_status="failed",
                error_code="branch_timeout",
                error_message=str(exc),
            )
        except BranchExecutionError as exc:
            logger.error(
                "event=graph_branch_failed function=run_assessment_branch "
                "node=run_assessment_branch task_id=%s candidate_id=%s "
                "dimension_number=%s error_code=%s error_type=%s",
                work_item.task_id, work_item.candidate_id,
                work_item.dimension_number, exc.code, type(exc).__name__,
            )
            result = BranchEvidenceDraft(
                task_id=work_item.task_id,
                candidate_id=work_item.candidate_id,
                dimension_number=work_item.dimension_number,
                execution_status="failed",
                error_code=exc.code,
                error_message=str(exc),
            )
        except Exception as exc:
            logger.exception(
                "event=graph_branch_crashed function=run_assessment_branch "
                "node=run_assessment_branch task_id=%s candidate_id=%s "
                "dimension_number=%s error_type=%s",
                work_item.task_id, work_item.candidate_id,
                work_item.dimension_number, type(exc).__name__,
            )
            raise
        logger.info(
            "event=graph_branch_completed function=run_assessment_branch "
            "node=run_assessment_branch task_id=%s execution_status=%s "
            "requirement_count=%s",
            work_item.task_id, result.execution_status, len(result.requirements),
        )
        return {"branch_results": [result.model_dump(mode="json")]}

    return run_assessment_branch


def _finalize(state: TalentEvaluationDispatchState) -> dict[str, Any]:
    """
    汇总分支结果：依据是否存在失败分支设定最终状态。
    """
    has_failures = any(
        item["execution_status"] == "failed"
        for item in state.get("branch_results", [])
    )
    status = "branches_ready_with_failures" if has_failures else "branches_ready"
    status_counts: dict[str, int] = {}
    for item in state.get("branch_results", []):
        execution_status = item["execution_status"]
        status_counts[execution_status] = status_counts.get(execution_status, 0) + 1
    logger.info(
        "event=graph_node_completed function=_finalize node=finalize "
        "status=%s branch_count=%s status_counts=%s",
        status, len(state.get("branch_results", [])), status_counts,
    )
    return {"status": status}


def _dimension_invalid(state: TalentEvaluationDispatchState) -> dict[str, Any]:
    """维度非法：记录校验问题的错误信息并结束。"""
    return {
        "status": "dimension_invalid",
        "errors": [item["message"] for item in state.get("validation_issues", [])],
    }


def _no_candidates(_: TalentEvaluationDispatchState) -> dict[str, Any]:
    """无候选人：清空工作项与分支结果。"""
    return {"status": "no_candidates", "work_items": [], "branch_results": []}


def _capacity_exceeded(state: TalentEvaluationDispatchState) -> dict[str, Any]:
    """容量超限：返回错误信息。"""
    return {
        "status": "capacity_exceeded",
        "errors": [
            f"评估任务数 {state['required_work_items']} 超过本次上限 {state['work_item_limit']}"
        ],
    }


def build_talent_evaluation_dispatch_graph(
    *,
    dimension_generator: DimensionGenerator,
    candidate_provider: CandidateProvider,
    branch_worker: BranchWorker,
    max_work_items: int = 24,
):
    """
    组装并编译“评估分派执行”子图。

    流程：接收输入 → 生成/校验维度 → （检索候选人）→ 准备工作项 → 并行分派取证分支 → 汇总（finalize）。


    """
    if max_work_items < 1:
        raise ValueError("max_work_items 必须大于等于 1")
    builder = StateGraph(
        TalentEvaluationDispatchState,
        context_schema=DecisionContext,
    )
    builder.add_node("receive_input", _receive_input)  # 接收输入
    builder.add_node("generate_dimensions", _generate_dimensions(dimension_generator))  # 生成维度
    builder.add_node("validate_dimensions", _validate_dimensions)  # 校验维度
    builder.add_node("retrieve_candidates", _retrieve_candidates(candidate_provider))  # 检索候选人
    builder.add_node("prepare_work_items", _prepare_work_items(max_work_items))  # 准备工作项
    builder.add_node("run_assessment_branch", _run_assessment_branch(branch_worker))  # 并行分派取证分支
    builder.add_node("finalize", _finalize)  # 汇总（finalize）
    builder.add_node("dimension_invalid", _dimension_invalid)  # 维度非法
    builder.add_node("no_candidates", _no_candidates)  # 无候选人
    builder.add_node("capacity_exceeded", _capacity_exceeded)  # 容量超限


    builder.add_edge(START, "receive_input")  # 从开始节点到接收输入节点
    builder.add_edge("receive_input", "generate_dimensions")  # 从接收输入节点到生成维度节点
    builder.add_edge("generate_dimensions", "validate_dimensions")  # 从生成维度节点到校验维度节点
    builder.add_conditional_edges("validate_dimensions",_route_dimension_validation,)  # 从校验维度节点到路由校验节点
    builder.add_edge("retrieve_candidates", "prepare_work_items")  # 从检索候选人节点到准备工作项节点
    builder.add_conditional_edges(
        "prepare_work_items",  # 从准备工作项节点到分派工作项节点
        _dispatch_work_items,
        ["run_assessment_branch", "no_candidates", "capacity_exceeded"],
    )  # 从分派工作项节点到汇总节点
    builder.add_edge("run_assessment_branch", "finalize")  # 从分派取证分支节点到汇总节点
    builder.add_edge("finalize", END)  # 从汇总节点到结束节点
    builder.add_edge("dimension_invalid", END)  # 从维度非法节点到结束节点
    builder.add_edge("no_candidates", END)  # 从无候选人节点到结束节点
    builder.add_edge("capacity_exceeded", END)  # 从容量超限节点到结束节点
    return builder.compile()