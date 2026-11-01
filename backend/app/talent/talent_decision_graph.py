from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from langchain.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import Command
from typing_extensions import TypedDict


DecisionStatus = Literal[
    "received",  # 已接收
    "request_ready",  # 请求已就绪
    "candidates_ready",  # 候选人已就绪
    "drafting",  # 报告起草中
    "completed",  # 已完成
    "no_candidates",  # 无候选人
    "failed",  # 失败
]


class TalentRequest(TypedDict):
    """结构化人才请求。"""
    original_text: str
    task_type: Literal["evaluate_and_recommend"]


class EvaluationResult(TypedDict):
    """单名候选人的评估结果（占位结构）。"""
    candidate_id: str
    status: Literal["placeholder"]


class TalentDecisionInput(TypedDict):
    """决策子图输入。"""
    messages: list[AnyMessage]
    request_text: str


class TalentDecisionOutput(TypedDict):
    """决策子图输出。"""
    status: DecisionStatus
    candidate_ids: list[str]
    evaluations: list[EvaluationResult]
    report: str
    errors: list[str]


class TalentDecisionState(TypedDict, total=False):
    """决策子图状态。"""
    messages: Annotated[list[AnyMessage], add_messages]  # 消息历史（追加合并）
    request_text: str  # 原始请求文本
    request: TalentRequest  # 结构化请求
    candidate_ids: list[str]  # 候选人工号
    evidence_refs: Annotated[list[str], list.__add__]  # 证据引用（追加合并）
    evaluations: Annotated[list[EvaluationResult], list.__add__]  # 评估结果（追加合并）
    report: str  # 报告文本
    status: DecisionStatus  # 当前状态
    errors: Annotated[list[str], list.__add__]  # 错误列表（追加合并）
    retry_count: int  # 重试次数


@dataclass(frozen=True)
class DecisionContext:
    """决策上下文：携带租户、权限范围与本次运行标识。"""
    tenant_id: str
    permission_scopes: tuple[str, ...]
    run_id: str = ""  # 本次运行 ID（流式运行时由运行管理器注入）
    conversation_id: str = ""  # 所属会话 ID
    turn_no: int = 0  # 当前轮次（从 1 开始）


CandidateProvider = Callable[[TalentRequest, DecisionContext], list[str]] # 候选人提供者


def _receive_request(
    state: TalentDecisionState,
    runtime: Runtime[DecisionContext],
) -> Command[Literal["prepare_request"]]:
    """
    接收并校验请求：校验租户上下文与请求文本，然后进入准备阶段。

    """
    if not runtime.context.tenant_id.strip():
        raise ValueError("Runtime Context 中的 tenant_id 不能为空")
    request_text = state["request_text"].strip()
    if not request_text:
        raise ValueError("request_text 不能为空")
    return Command(
        update={"request_text": request_text, "status": "received", "errors": []},
        goto="prepare_request",
    )


def _prepare_request(state: TalentDecisionState) -> dict:
    """组装结构化人才请求并标记就绪。"""
    return {
        "request": {
            "original_text": state["request_text"],
            "task_type": "evaluate_and_recommend",
        },
        "status": "request_ready",
    }


def _candidate_node(candidate_provider: CandidateProvider):
    """构造“检索候选人”节点：调用候选人提供者筛选。"""
    def retrieve_candidates(
        state: TalentDecisionState,
        runtime: Runtime[DecisionContext],
    ) -> dict:
        candidate_ids = candidate_provider(state["request"], runtime.context)
        return {"candidate_ids": candidate_ids, "status": "candidates_ready"}

    return retrieve_candidates


def _route_candidates(state: TalentDecisionState) -> Literal["evaluate", "no_candidates"]:
    """按是否有候选人路由：有 → 评估；无 → 无候选人。"""
    return "evaluate" if state.get("candidate_ids") else "no_candidates"


def _evaluate(state: TalentDecisionState) -> dict:
    """
    对每位候选人生成占位评估结果。

    """
    return {
        "evaluations": [
            {"candidate_id": candidate_id, "status": "placeholder"}
            for candidate_id in state["candidate_ids"]
        ]
    }


def _compose_report(state: TalentDecisionState) -> dict:
    """基于评估结果拼装报告草稿。"""
    candidate_text = "、".join(item["candidate_id"] for item in state["evaluations"])
    return {"report": f"候选人评估占位结果：{candidate_text}", "status": "drafting"}


def _validate_report(state: TalentDecisionState) -> dict:
    """校验报告：非空则完成；否则重试，重试超限则失败。"""
    if state.get("report"):
        return {"status": "completed"}
    retries = state.get("retry_count", 0) + 1
    if retries > 1:
        return {"retry_count": retries, "status": "failed", "errors": ["报告为空"]}
    return {"retry_count": retries}


def _route_report(state: TalentDecisionState) -> Literal["retry", "done"]:
    """报告校验后的路由：完成/失败 → 结束；否则重试。"""
    if state.get("status") in {"completed", "failed"}:
        return "done"
    return "retry"


def _no_candidates(_: TalentDecisionState) -> dict:
    """无候选人时返回空评估与提示报告。"""
    return {
        "evaluations": [],
        "report": "当前条件下未检索到候选人。",
        "status": "no_candidates",
    }


def build_talent_decision_graph(candidate_provider: CandidateProvider):
    """组装并编译“人才决策”图（简化版占位实现）。

    流程：接收请求 → 准备请求 → 检索候选人 → 评估 → 组报告 → 校验（可重试）。
    """
    builder = StateGraph(
        TalentDecisionState,
        context_schema=DecisionContext,  # 决策上下文
        input_schema=TalentDecisionInput,
        output_schema=TalentDecisionOutput,
    )
    # 注册各节点
    builder.add_node("receive_request", _receive_request)
    builder.add_node("prepare_request", _prepare_request)
    builder.add_node("retrieve_candidates", _candidate_node(candidate_provider))
    builder.add_node("evaluate", _evaluate)
    builder.add_node("compose_report", _compose_report)
    builder.add_node("validate_report", _validate_report)
    builder.add_node("no_candidates", _no_candidates)
    # 串联边与条件路由
    builder.add_edge(START, "receive_request")
    builder.add_edge("prepare_request", "retrieve_candidates")
    builder.add_conditional_edges("retrieve_candidates", _route_candidates)
    builder.add_edge("evaluate", "compose_report")
    builder.add_edge("compose_report", "validate_report")
    builder.add_conditional_edges(
        "validate_report",
        _route_report,
        {"retry": "compose_report", "done": END},
    )
    builder.add_edge("no_candidates", END)
    return builder.compile()


class MissingReducerState(TypedDict):
    items: list[str]


def build_missing_reducer_demo():
    """缺少 reducer 的分支合并演示：两分支各自写入 items，仅保留末次写入。"""
    builder = StateGraph(MissingReducerState)
    builder.add_node("branch_a", lambda _: {"items": ["A"]})
    builder.add_node("branch_b", lambda _: {"items": ["B"]})
    builder.add_edge(START, "branch_a")
    builder.add_edge(START, "branch_b")
    builder.add_edge("branch_a", END)
    builder.add_edge("branch_b", END)
    return builder.compile()


class ReducerState(TypedDict):
    items: Annotated[list[str], list.__add__]


def build_reducer_demo():
    """带 reducer 的分支合并演示：使用 list.__add__ 合并两分支结果。"""
    builder = StateGraph(ReducerState)
    builder.add_node("branch_a", lambda _: {"items": ["A"]})
    builder.add_node("branch_b", lambda _: {"items": ["B"]})
    builder.add_edge(START, "branch_a")
    builder.add_edge(START, "branch_b")
    builder.add_edge("branch_a", END)
    builder.add_edge("branch_b", END)
    return builder.compile()