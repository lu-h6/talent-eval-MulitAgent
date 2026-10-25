from __future__ import annotations

from collections.abc import Callable
from operator import add
from typing import Annotated, Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel, Field, ValidationError
from typing_extensions import TypedDict

from app.db.database import SessionLocal
from app.integrations.model_provider import get_chat_model
from app.retrieval.query_plan import Clarification, FilterCondition, QueryPlan, SemanticRequirement, TaskType
from app.talent.talent_decision_graph import DecisionContext
from app.talent.talent_tools import TalentToolContext, TalentToolService


RequestInputMode = Literal["detailed_requirement", "job_name"]
RequestStatus = Literal[
    "received",  # 已接收
    "request_understood",  # 请求已理解
    "jobs_found",  # 已找到岗位
    "job_confirmed",  # 岗位已确认
    "no_job_match",  # 无匹配岗位
    "plan_ready",  # 查询计划就绪
    "clarification_required",  # 需要澄清
    "candidates_empty",  # 候选人空集
    "candidates_ready",  # 候选人已就绪
    "plan_revised",  # 计划已修订
    "request_revised",  # 请求已修订
    "cancelled",  # 已取消
]


class JobMatch(TypedDict):
    """一次岗位查找的命中的 JD（含匹配方式与得分）。"""
    job_code: str
    name: str
    match_score: float
    match_type: Literal["exact", "contains", "fuzzy"]
    version: int
    content: str


class TargetJob(TypedDict):
    """最终选定的目标岗位（供下游评估使用）。"""
    job_code: str
    name: str
    version: int


class TalentRequestDraft(BaseModel):
    """模型（解释器）输出的结构化人才请求草稿。"""
    input_mode: RequestInputMode  # 输入模式
    job_query: str | None = None  # 岗位名称查询
    filters: list[FilterCondition] = Field(default_factory=list)  # 结构化硬条件
    semantic_requirements: list[SemanticRequirement] = Field(default_factory=list)  # 语义检索要求
    evaluation_preferences: list[str] = Field(default_factory=list)  # 评估优先项
    clarifications: list[Clarification] = Field(default_factory=list)  # 需澄清项


class TalentRequestInput(TypedDict):
    """请求处理子图输入。"""
    request_text: str


class TalentRequestOutput(TypedDict):
    """请求处理子图输出。"""
    status: RequestStatus
    input_mode: RequestInputMode
    job_matches: list[JobMatch]
    selected_job: JobMatch | None
    talent_request: dict[str, Any]
    query_plan: dict[str, Any]
    candidate_ids: list[str]
    condition_revisions: list[dict[str, Any]]
    plan_version: int
    clarifications: list[dict[str, str]]
    errors: list[str]


class TalentRequestState(TypedDict, total=False):
    """请求处理子图的图状态。"""
    request_text: str  # 原始请求文本
    input_mode: RequestInputMode  # 输入模式
    draft: dict[str, Any]  # 结构化草稿（JSON 形式）
    job_matches: list[JobMatch]  # 命中的岗位列表
    selected_job: JobMatch | None  # 最终选定岗位
    talent_request: dict[str, Any]  # 结构化人才请求
    query_plan: dict[str, Any]  # 查询计划
    candidate_ids: list[str]  # 筛选出的候选人工号
    condition_revisions: Annotated[list[dict[str, Any]], add]  # 条件修订记录（追加合并）
    plan_version: int  # 修订版本号
    clarifications: list[dict[str, str]]  # 澄清问题
    status: RequestStatus  # 当前状态
    errors: list[str]  # 错误列表


RequestInterpreter = Callable[[str, JobMatch | None], TalentRequestDraft]
JobLookup = Callable[[str, DecisionContext], list[JobMatch]]
CandidateFilter = Callable[[list[FilterCondition], DecisionContext], list[str]]


def _receive_request(state: TalentRequestState, runtime: Runtime[DecisionContext]) -> dict[str, Any]:
    """接收并校验原始请求：校验租户/权限上下文与请求文本非空。"""
    if not runtime.context.tenant_id.strip():
        raise ValueError("Runtime Context 中的 tenant_id 不能为空")
    if not runtime.context.permission_scopes:
        raise ValueError("Runtime Context 中的 permission_scopes 不能为空")
    request_text = state["request_text"].strip()
    if not request_text:
        raise ValueError("request_text 不能为空")
    # 初始化请求处理阶段所需的状态字段
    return {
        "request_text": request_text,
        "job_matches": [],
        "selected_job": None,
        "talent_request": {},
        "query_plan": {},
        "candidate_ids": [],
        "condition_revisions": [],
        "plan_version": 0,
        "clarifications": [],
        "status": "received",
        "errors": [],
    }


def _interpret_input(request_interpreter: RequestInterpreter):
    """构造“解释输入”节点：调用解释器生成结构化草稿。"""
    def interpret_input(state: TalentRequestState) -> dict[str, Any]:
        draft = request_interpreter(state["request_text"], None)
        return {
            "draft": draft.model_dump(mode="json"),
            "input_mode": draft.input_mode,
            "status": "request_understood",
        }

    return interpret_input


def _route_input(state: TalentRequestState) -> Literal["lookup_jobs", "build_plan"]:
    """按输入模式路由：岗位名 → 查找岗位；详细要求 → 直接编译计划。"""
    return "lookup_jobs" if state["input_mode"] == "job_name" else "build_plan"


def _lookup_jobs(job_lookup: JobLookup):
    """构造“查找岗位”节点：调用 JD 查找服务并按租户返回命中列表。"""
    def lookup_jobs(state: TalentRequestState, runtime: Runtime[DecisionContext]) -> dict[str, Any]:
        job_query = str(state["draft"].get("job_query") or state["request_text"])
        matches = job_lookup(job_query, runtime.context)
        return {"job_matches": matches, "status": "jobs_found"}

    return lookup_jobs


def _route_job_matches(
    state: TalentRequestState,
) -> Literal["select_exact_job", "confirm_job", "no_job_match"]:
    """按命中结果路由：唯一精确匹配直接选中；多个需人工确认；无则未命中。"""
    exact_matches = [item for item in state["job_matches"] if item["match_type"] == "exact"]
    if len(exact_matches) == 1:
        return "select_exact_job"
    if state["job_matches"]:
        return "confirm_job"
    return "no_job_match"


def _select_exact_job(state: TalentRequestState) -> dict[str, Any]:
    """唯一精确匹配时自动选中岗位。"""
    selected = next(item for item in state["job_matches"] if item["match_type"] == "exact")
    return {"selected_job": selected, "status": "job_confirmed"}


def _confirm_job(state: TalentRequestState) -> dict[str, Any]:
    """多人机节点：暂停图执行并请求用户从命中岗位中选择，或取消。"""
    selection = interrupt(
        {
            "type": "job_selection",
            "question": "请选择本次人才评估使用的岗位 JD",
            "request_text": state["request_text"],
            "options": [
                {
                    "job_code": item["job_code"],
                    "name": item["name"],
                    "match_score": item["match_score"],
                    "match_type": item["match_type"],
                    "version": item["version"],
                }
                for item in state["job_matches"]
            ],
            "actions": ["select", "cancel"],
        }
    )
    if not isinstance(selection, dict):
        raise ValueError("恢复数据必须是 JSON 对象")
    if selection.get("action") == "cancel":
        return {"status": "cancelled"}
    if selection.get("action") != "select":
        raise ValueError("action 必须是 select 或 cancel")
    # 依据返回的 job_code 在命中列表中定位所选岗位
    selected_code = selection.get("job_code")
    selected = next(
        (item for item in state["job_matches"] if item["job_code"] == selected_code),
        None,
    )
    if selected is None:
        raise ValueError("恢复数据中的 job_code 不在待确认岗位列表中")
    return {"selected_job": selected, "status": "job_confirmed"}


def _route_job_confirmation(state: TalentRequestState) -> str:
    """岗位确认后的路由：已确认则进入编译，否则结束。"""
    return "compile_selected_job" if state["status"] == "job_confirmed" else END


def _compile_selected_job(request_interpreter: RequestInterpreter):
    """构造“编译选中岗位”节点：结合已确认的岗位 JD 重新解释请求。"""
    def compile_selected_job(state: TalentRequestState) -> dict[str, Any]:
        draft = request_interpreter(state["request_text"], state["selected_job"])
        return {
            "draft": draft.model_dump(mode="json"),
            "input_mode": "job_name",
        }

    return compile_selected_job


def _build_plan(state: TalentRequestState) -> dict[str, Any]:
    """基于结构化草稿与选定岗位编译人才请求与查询计划。"""
    draft = TalentRequestDraft.model_validate(state["draft"])
    selected_job = state.get("selected_job")
    target_job: TargetJob | None = None
    # 若已确认岗位，则将其作为评估的目标岗位
    if selected_job is not None:
        target_job = {
            "job_code": selected_job["job_code"],
            "name": selected_job["name"],
            "version": selected_job["version"],
        }

    # 组装结构化人才请求
    talent_request = {
        "original_text": state["request_text"],
        "task_type": "evaluate_and_recommend",
        "source": state["input_mode"],
        "target_job": target_job,
        "hard_conditions": [item.model_dump(mode="json") for item in draft.filters],
        "semantic_conditions": [
            item.model_dump(mode="json") for item in draft.semantic_requirements
        ],
        "evaluation_preferences": draft.evaluation_preferences,
    }
    # 编译查询计划；有澄清项则计划不可执行
    query_plan = QueryPlan(
        task_type=TaskType.FIND_TALENT,
        filters=draft.filters,
        semantic_requirements=draft.semantic_requirements,
        preferences=draft.evaluation_preferences,
        clarifications=draft.clarifications,
    )
    status: RequestStatus = "plan_ready" if query_plan.executable else "clarification_required"
    return {
        "talent_request": talent_request,
        "query_plan": query_plan.model_dump(mode="json"),
        "clarifications": [item.model_dump(mode="json") for item in draft.clarifications],
        "status": status,
    }


def _no_job_match(state: TalentRequestState) -> dict[str, Any]:
    """未找到岗位：记录澄清信息并结束。"""
    clarification = {
        "expression": state["request_text"],
        "reason": "未找到可确认的岗位 JD",
    }
    return {
        "query_plan": {},
        "talent_request": {},
        "clarifications": [clarification],
        "status": "no_job_match",
    }


# ---------------------------------------------------------------------------
# 第 17 课：候选人检索与可恢复人机协同
# ---------------------------------------------------------------------------


def _route_after_plan(state: TalentRequestState) -> str:
    """计划编译后的路由：需澄清 → 解决需求；否则筛选候选人。"""
    if state["status"] == "clarification_required":
        return "resolve_requirements"
    return "filter_candidates"


def _route_candidates(state: TalentRequestState) -> str:
    """候选人筛选后的路由：空集 → 解决需求；否则结束。"""
    if state["status"] == "candidates_empty":
        return "resolve_requirements"
    return END


def _route_resolution(state: TalentRequestState) -> str:
    """需求解决后的路由：请求已修订 → 重新解释；否则结束。"""
    if state["status"] == "request_revised":
        return "interpret_input"
    return END


def _await_decision(payload: dict[str, Any], parse: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
    """等待恢复数据并完成校验。

    校验失败时不抛出异常，而是携带 resume_error 重新 interrupt，
    让 Thread 停留在暂停点等待下一次提交，避免节点失败导致线程卡死。
    """
    decision = interrupt(payload)
    # 循环校验：非法则重新暂停请求修正，直到通过或取消
    while True:
        try:
            return parse(decision)
        except ValueError as exc:
            decision = interrupt({**payload, "resume_error": str(exc)})


def _require_interaction(decision: Any, expected_id: str) -> dict[str, Any]:
    """校验恢复数据为 JSON 对象且 interaction_id 与当前暂停点匹配。"""
    if not isinstance(decision, dict):
        raise ValueError("恢复数据必须是 JSON 对象")
    if decision.get("interaction_id") != expected_id:
        raise ValueError(f"恢复数据与当前暂停点不匹配，期望 interaction_id={expected_id}")
    return decision


def _filter_candidates_node(candidate_filter: CandidateFilter):
    """构造“筛选候选人”节点：执行结构化过滤并判断是否有命中。"""
    def filter_candidates(state: TalentRequestState, runtime: Runtime[DecisionContext]) -> dict[str, Any]:
        plan = QueryPlan.model_validate(state["query_plan"])
        candidate_ids = candidate_filter(plan.filters, runtime.context)
        if candidate_ids:
            return {"candidate_ids": candidate_ids, "status": "candidates_ready"}
        return {"candidate_ids": [], "status": "candidates_empty"}

    return filter_candidates


def _diagnose_filters(
    filters: list[FilterCondition],
    candidate_filter: CandidateFilter,
    context: DecisionContext,
) -> list[dict[str, Any]]:
    """leave-one-out 诊断：逐个移除条件，观察候选数变化，定位导致空集的条件。"""
    diagnostics = []
    for index, condition in enumerate(filters):
        reduced = filters[:index] + filters[index + 1 :]
        hit_count = len(candidate_filter(reduced, context))
        diagnostics.append(
            {
                "field": str(condition.field),
                "operator": condition.operator,
                "value": condition.value,
                "candidates_without_condition": hit_count,
            }
        )
    return diagnostics


def _resolve_requirements():
    """构造“解决需求”节点：用户补充/修订人才要求，部分场景提供 leave-one-out 诊断。"""
    def resolve_requirements(
        state: TalentRequestState,
        runtime: Runtime[DecisionContext],
    ) -> dict[str, Any]:
        plan_version = state.get("plan_version", 0)
        interaction_id = f"rr-v{plan_version}"
        reason_code = "plan_blocked" if state["status"] == "clarification_required" else "empty_candidates"
        filters = state.get("draft", {}).get("filters", [])
        # 组装阻断原因：计划不可执行或候选人空集
        if reason_code == "plan_blocked":
            blocking_reason = "当前要求存在歧义或冲突，无法生成可执行查询计划"
        else:
            rendered = "、".join(
                f"{item['field']} {item['operator']} {item['value']}" for item in filters
            )
            blocking_reason = f"使用以下结构化条件筛选后未命中候选人：{rendered}"
        # 决定可编辑的请求文本来源（岗位 JD 或用户请求）
        selected_job = state.get("selected_job")
        if selected_job is not None:
            editable_request = selected_job["content"]
            request_source = "job_profile"
            selected_job_source: dict[str, Any] | None = {
                "job_code": selected_job["job_code"],
                "name": selected_job["name"],
                "version": selected_job["version"],
            }
        else:
            editable_request = state["request_text"]
            request_source = "user_request"
            selected_job_source = None
        payload = {
            "type": "requirement_revision",
            "interaction_id": interaction_id,
            "reason_code": reason_code,
            "blocking_reason": blocking_reason,
            "question": "请补充或修订人才要求后重新提交，或取消本次任务",
            "request_text": editable_request,
            "request_source": request_source,
            "selected_job": selected_job_source,
            "filters": filters,
            "semantic_requirements": state.get("draft", {}).get("semantic_requirements", []),
            "clarifications": state.get("clarifications", []),
            "actions": ["revise", "cancel"],
        }

        def parse(decision: Any) -> dict[str, Any]:
            """校验恢复数据：必须匹配暂停点且携带合法的 revise/revision_text。"""
            decision = _require_interaction(decision, interaction_id)
            action = decision.get("action")
            if action == "cancel":
                return {"action": "cancel"}
            if action != "revise":
                raise ValueError("action 必须是 revise 或 cancel")
            revision_text = decision.get("revision_text")
            if not isinstance(revision_text, str) or not revision_text.strip():
                raise ValueError("revise 动作必须携带非空 revision_text")
            return {"action": "revise", "revision_text": revision_text.strip()}

        parsed = _await_decision(payload, parse)
        if parsed["action"] == "cancel":
            return {"status": "cancelled"}

        revision_text = parsed["revision_text"]
        # 记录本次修订并递增计划版本，请求文本替换为新文本
        revision = {
            "interaction_id": interaction_id,
            "action": "revise",
            "reason_code": reason_code,
            "revision_text": revision_text,
            "plan_version": plan_version + 1,
        }
        return {
            "request_text": revision_text,
            "selected_job": None,
            "job_matches": [],
            "candidate_ids": [],
            "condition_revisions": [revision],
            "plan_version": plan_version + 1,
            "status": "request_revised",
        }

    return resolve_requirements


def _apply_relaxations(
    current: list[FilterCondition],
    relaxations: list[dict[str, Any]],
) -> tuple[list[FilterCondition], list[dict[str, Any]]]:
    """对当前过滤条件应用一组放宽操作（remove 移除 / update 替换）。"""
    updated = list(current)
    changes: list[dict[str, Any]] = []
    for item in relaxations:
        field = item.get("field")
        op = item.get("op")
        # 定位目标条件在列表中的下标
        index = next((i for i, cond in enumerate(updated) if str(cond.field) == field), None)
        if index is None:
            raise ValueError(f"放宽目标条件不存在：{field}")
        if op == "remove":
            removed = updated.pop(index)
            changes.append({"field": field, "op": "remove", "removed": removed.model_dump(mode="json")})
        elif op == "update":
            replacement = FilterCondition.model_validate(
                {"field": field, "operator": item.get("operator"), "value": item.get("value")}
            )
            previous = updated[index]
            updated[index] = replacement
            changes.append(
                {
                    "field": field,
                    "op": "update",
                    "previous": previous.model_dump(mode="json"),
                    "current": replacement.model_dump(mode="json"),
                }
            )
        else:
            raise ValueError(f"不支持的放宽操作：{op}")
    return updated, changes


def _resolve_conditions(state: TalentRequestState) -> dict[str, Any]:
    """多人机节点：针对不可直接执行的语义/硬条件请求用户修订。"""
    plan_version = state.get("plan_version", 0)
    interaction_id = f"cc-v{plan_version}"
    draft = dict(state["draft"])
    payload = {
        "type": "condition_clarification",
        "interaction_id": interaction_id,
        "question": "以下条件无法直接执行，请修订后重新提交，或取消本次任务",
        "clarifications": state["clarifications"],
        "current_filters": draft.get("filters", []),
        "current_semantic_requirements": draft.get("semantic_requirements", []),
        "actions": ["revise", "cancel"],
    }

    def parse(decision: Any) -> dict[str, Any]:
        """校验并解析条件澄清的恢复数据。"""
        decision = _require_interaction(decision, interaction_id)
        action = decision.get("action")
        if action == "cancel":
            return {"action": "cancel"}
        if action != "revise":
            raise ValueError("action 必须是 revise 或 cancel")
        parsed: dict[str, Any] = {"action": "revise"}
        # 可选的 filters：逐个校验为结构化条件
        if "filters" in decision:
            try:
                parsed["filters"] = [
                    FilterCondition.model_validate(item) for item in decision["filters"]
                ]
            except ValidationError as exc:
                raise ValueError(f"filters 校验失败：{exc.errors()[0]['msg']}") from exc
        # 可选的 semantic_requirements：要求每项含 query，自动分配 requirement_id
        if "semantic_requirements" in decision:
            items = decision["semantic_requirements"]
            if not isinstance(items, list) or any(not isinstance(item, dict) or not item.get("query") for item in items):
                raise ValueError("semantic_requirements 的每一项都必须包含 query")
            parsed["semantic_requirements"] = [
                SemanticRequirement(
                    requirement_id=f"S{index + 1}",
                    query=str(item["query"]),
                    required=bool(item.get("required", True)),
                )
                for index, item in enumerate(items)
            ]
        # 至少修订 filters 或 semantic_requirements 之一
        if "filters" not in parsed and "semantic_requirements" not in parsed:
            raise ValueError("revise 动作必须携带 filters 或 semantic_requirements")
        return parsed

    parsed = _await_decision(payload, parse)
    if parsed["action"] == "cancel":
        return {"status": "cancelled"}

    # 应用修订并记录被无效化的下游产物
    changes: list[dict[str, Any]] = []
    invalidates: set[str] = set()
    if "filters" in parsed:
        draft["filters"] = [item.model_dump(mode="json") for item in parsed["filters"]]
        changes.append({"kind": "filters", "count": len(parsed["filters"])})
        invalidates.add("candidate_set")
    if "semantic_requirements" in parsed:
        draft["semantic_requirements"] = [
            item.model_dump(mode="json") for item in parsed["semantic_requirements"]
        ]
        changes.append({"kind": "semantic_requirements", "count": len(parsed["semantic_requirements"])})
        invalidates.update(["evidence_retrieval", "evaluation_dimensions", "evaluation_report"])

    # 将本次澄清标记为已解决并入修订记录
    resolved = list(draft.get("clarifications", []))
    draft["clarifications"] = []
    revision = {
        "interaction_id": interaction_id,
        "action": "revise",
        "changes": changes,
        "resolved_clarifications": resolved,
        "invalidates": sorted(invalidates),
        "plan_version": plan_version + 1,
    }
    return {
        "draft": draft,
        "condition_revisions": [revision],
        "plan_version": plan_version + 1,
        "status": "plan_revised",
    }


def _resolve_empty_candidates(candidate_filter: CandidateFilter):
    """构造“解决空候选人集”节点：诊断后请求用户放宽部分条件。"""
    def resolve_empty_candidates(
        state: TalentRequestState,
        runtime: Runtime[DecisionContext],
    ) -> dict[str, Any]:
        plan = QueryPlan.model_validate(state["query_plan"])
        plan_version = state.get("plan_version", 0)
        interaction_id = f"ec-v{plan_version}"
        diagnostics = _diagnose_filters(plan.filters, candidate_filter, runtime.context)
        current = [
            FilterCondition.model_validate(item) for item in state["draft"].get("filters", [])
        ]
        payload = {
            "type": "empty_candidate_set",
            "interaction_id": interaction_id,
            "question": "当前条件未命中任何候选人，可放宽部分条件后重试，或取消本次任务",
            "filters": diagnostics,
            "actions": ["relax", "cancel"],
        }

        def parse(decision: Any) -> dict[str, Any]:
            """校验并解析宽松请求的恢复数据。"""
            decision = _require_interaction(decision, interaction_id)
            action = decision.get("action")
            if action == "cancel":
                return {"action": "cancel"}
            if action != "relax":
                raise ValueError("action 必须是 relax 或 cancel")
            relaxations = decision.get("relaxations") or []
            if not relaxations:
                raise ValueError("relax 动作必须携带 relaxations")
            # 应用放宽操作，得到更新后的条件与变更记录
            updated, changes = _apply_relaxations(current, relaxations)
            return {"action": "relax", "updated": updated, "changes": changes}

        parsed = _await_decision(payload, parse)
        if parsed["action"] == "cancel":
            return {"status": "cancelled"}

        # 写入放宽后的过滤条件并记录修订
        draft = dict(state["draft"])
        draft["filters"] = [item.model_dump(mode="json") for item in parsed["updated"]]
        revision = {
            "interaction_id": interaction_id,
            "action": "relax",
            "changes": parsed["changes"],
            "resolved_clarifications": [],
            "invalidates": ["candidate_set"],
            "plan_version": plan_version + 1,
        }
        return {
            "draft": draft,
            "condition_revisions": [revision],
            "plan_version": plan_version + 1,
            "status": "plan_revised",
        }

    return resolve_empty_candidates


def build_talent_request_graph(
    *,
    request_interpreter: RequestInterpreter,
    job_lookup: JobLookup,
    candidate_filter: CandidateFilter | None = None,
    checkpointer: Any | None = None,
):
    """组装并编译“人才请求处理”子图。

    负责理解请求、确认岗位、编译查询计划，以及（可选）执行候选人检索
    与可恢复的人机协同（澄清/放宽/修订）。
    """
    builder = StateGraph(
        TalentRequestState,
        context_schema=DecisionContext,  # 决策上下文（租户/权限/操作者）
        input_schema=TalentRequestInput,
        output_schema=TalentRequestOutput,
    )
    # 注册请求处理阶段各节点
    builder.add_node("receive_request", _receive_request)
    builder.add_node("interpret_input", _interpret_input(request_interpreter))
    builder.add_node("lookup_jobs", _lookup_jobs(job_lookup))
    builder.add_node("select_exact_job", _select_exact_job)
    builder.add_node("confirm_job", _confirm_job)
    builder.add_node("compile_selected_job", _compile_selected_job(request_interpreter))
    builder.add_node("build_plan", _build_plan)
    builder.add_node("no_job_match", _no_job_match)
    # 串联：接收 → 解释 → （岗位路由）→ 编译计划
    builder.add_edge(START, "receive_request")
    builder.add_edge("receive_request", "interpret_input")
    builder.add_conditional_edges("interpret_input", _route_input)
    builder.add_conditional_edges("lookup_jobs", _route_job_matches)
    builder.add_edge("select_exact_job", "compile_selected_job")
    builder.add_conditional_edges(
        "confirm_job",
        _route_job_confirmation,
        {"compile_selected_job": "compile_selected_job", END: END},
    )
    builder.add_edge("compile_selected_job", "build_plan")
    builder.add_edge("no_job_match", END)

    if candidate_filter is None:
        # 第 14 课行为：编译出 QueryPlan 即结束，候选人检索与澄清节点不挂载
        builder.add_edge("build_plan", END)
    else:
        # 挂载候选人检索与可恢复人机协同节点
        builder.add_node("filter_candidates", _filter_candidates_node(candidate_filter))
        builder.add_node("resolve_requirements", _resolve_requirements())
        builder.add_conditional_edges(
            "build_plan",
            _route_after_plan,
            {"resolve_requirements": "resolve_requirements", "filter_candidates": "filter_candidates"},
        )
        builder.add_conditional_edges(
            "filter_candidates",
            _route_candidates,
            {"resolve_requirements": "resolve_requirements", END: END},
        )
        builder.add_conditional_edges(
            "resolve_requirements",
            _route_resolution,
            {"interpret_input": "interpret_input", END: END},
        )
    # 支持可恢复执行时注入外部 checkpointer；否则使用默认
    return builder.compile(checkpointer=checkpointer) if checkpointer is not None else builder.compile()


MODEL_SYSTEM_PROMPT = """你是人才评估任务编译器。输出 TalentRequestDraft。
如果输入只有岗位名称或岗位简称，input_mode 使用 job_name，只填写 job_query，不根据岗位名称猜测条件。
如果输入包含地区、年限、职级、技能、项目经验或偏好，input_mode 使用 detailed_requirement。
结构化硬条件只能写入 filters。技能、经历和成果写入 semantic_requirements。优先项写入 evaluation_preferences。
只有歧义或矛盾导致无法形成可执行查询计划时才写入 clarifications。资深、经验丰富等可以用于语义检索或排序的描述写入 semantic_requirements 或 evaluation_preferences，不要求补充年龄等结构化阈值。租户和权限不得从用户文本提取。
当输入中包含 confirmed_job 时，按照已确认岗位 JD 编译条件，并保留用户补充要求。"""


def model_request_interpreter(request_text: str, selected_job: JobMatch | None = None) -> TalentRequestDraft:
    """用大模型将请求文本解释为结构化 TalentRequestDraft（可将已确认岗位并入上下文）。"""
    model = get_chat_model(temperature=0)
    if model is None:
        raise RuntimeError("任务理解模型未配置，请设置 DASHSCOPE_API_KEY")
    user_content = request_text
    # 若已确认岗位，则将岗位名与 JD 内容一并交给模型
    if selected_job is not None:
        user_content = (
            f"original_request={request_text}\n"
            f"confirmed_job={selected_job['name']}\n"
            f"job_description={selected_job['content']}"
        )
    return model.with_structured_output(TalentRequestDraft).invoke(
        [("system", MODEL_SYSTEM_PROMPT), ("user", user_content)]
    )


def database_job_lookup(query: str, context: DecisionContext) -> list[JobMatch]:
    """基于数据库的岗位查找适配器：构造工具上下文并调用服务。"""
    service = TalentToolService(session_factory=SessionLocal)
    tool_context = TalentToolContext(
        tenant_id=context.tenant_id,
        permission_scopes=context.permission_scopes,
        actor_id="langsmith-studio",
        run_id="lesson-14",
    )
    return service.lookup_job_descriptions(query, context=tool_context)


def database_candidate_filter(filters: list[FilterCondition], context: DecisionContext) -> list[str]:
    """基于数据库的候选人筛选适配器：构造工具上下文并调用服务。"""
    service = TalentToolService(session_factory=SessionLocal)
    tool_context = TalentToolContext(
        tenant_id=context.tenant_id,
        permission_scopes=context.permission_scopes,
        actor_id="langsmith-studio",
        run_id="lesson-17",
    )
    return service.filter_candidates(filters, context=tool_context)


# Agent Server 在从 langgraph.json 加载本图时注入其托管 checkpointer。
graph = build_talent_request_graph(
    request_interpreter=model_request_interpreter,
    job_lookup=database_job_lookup,
    candidate_filter=database_candidate_filter,
)