"""决策引擎：把请求处理、评估分发与报告三个子图串成主流程。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from langchain.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.talent.talent_decision_graph import DecisionContext


class TalentDecisionEngineInput(TypedDict, total=False):
    """决策引擎的输入：多轮消息与（兼容用）原始请求文本。"""
    messages: Annotated[list[AnyMessage], add_messages]  # 多轮对话消息
    request_text: str  # 原始请求文本（早期单轮入口）


class TalentDecisionEngineOutput(TypedDict, total=False):
    """决策引擎的输出：会话摘要、候选人、排名与报告等汇总结果。"""
    messages: list[AnyMessage]  # 累积的多轮消息
    revision_action: str  # 本轮要求的修订动作（initial/add/update/remove）
    revision_summary: str  # 本轮修订说明
    conversation_title: str  # 会话标题（首轮生成、后续保留）
    status: str
    candidate_ids: list[str]
    ranking: list[str]
    report: str
    errors: list[str]


class TalentDecisionEngineState(TypedDict, total=False):
    """决策引擎的图状态，横跨“请求处理 → 评估分派 → 报告生成”三个阶段。"""

    # —— 请求处理阶段 ——
    messages: Annotated[list[AnyMessage], add_messages]  # 多轮对话消息（跨轮累积）
    request_text: str  # 原始请求文本
    revision_action: str  # 本轮要求的修订动作（initial/add/update/remove）
    revision_summary: str  # 本轮修订说明
    conversation_title: str  # 会话标题（首轮生成、后续保留）
    input_mode: str  # 输入模式
    job_matches: list[dict[str, Any]]  # 命中的岗位 JD 列表
    selected_job: dict[str, Any] | None  # 最终选定岗位
    talent_request: dict[str, Any]  # 结构化的人才请求
    # —— 评估分派阶段 ——
    query_plan: dict[str, Any]  # 查询计划
    candidate_ids: list[str]  # 筛选出的候选人工号
    condition_revisions: list[dict[str, Any]]  # 条件修订记录
    plan_version: int  # 计划版本号
    clarifications: list[dict[str, str]]  # 需澄清的问题
    dimensions: list[dict[str, Any]]  # 评估维度
    work_items: list[dict[str, Any]]  # 证据检索工作项
    branch_results: list[dict[str, Any]]  # 各证据分支结果
    validation_issues: list[dict[str, Any]]  # 校验发现的问题
    required_work_items: int  # 必要工作项数量
    work_item_limit: int  # 工作项上限
    evidence_items: list[dict[str, Any]]  # 汇总后的证据项
    # —— 报告生成阶段 ——
    dimension_assessments: list[dict[str, Any]]  # 维度评估结果
    consistency_issues: list[dict[str, Any]]  # 一致性审查问题
    candidate_assessments: list[dict[str, Any]]  # 候选人评估
    ranking: list[str]  # 候选人排名
    report_draft: dict[str, Any]  # 报告草稿
    report: str  # 最终报告文本
    status: str  # 当前阶段状态
    errors: list[str]  # 错误列表


def _begin_turn(state: TalentDecisionEngineState) -> dict[str, Any]:
    """每轮开始时清空上一轮产物，但保留消息、当前输入、人才要求与会话标题。"""

    update: dict[str, Any] = {
        "candidate_ids": [],
        "dimensions": [],
        "work_items": [],
        "branch_results": [],
        "evidence_items": [],
        "dimension_assessments": [],
        "consistency_issues": [],
        "candidate_assessments": [],
        "ranking": [],
        "report_draft": {},
        "report": "",
        "errors": [],
        "status": "received",
    }
    if not state.get("messages") and state.get("request_text"):
        # 兼容早期单轮脚本的纯文本输入，正常多轮 API 已经提交 HumanMessage。
        from langchain.messages import HumanMessage

        update["messages"] = [HumanMessage(content=state["request_text"])]
    return update


def _route_after_request(
    state: TalentDecisionEngineState,
) -> Literal["evaluation", "stop"]:
    """请求处理结束后的路由：候选人已就绪则进入评估，否则终止。"""
    return "evaluation" if state.get("status") == "candidates_ready" else "stop"


def _route_after_evaluation(
    state: TalentDecisionEngineState,
) -> Literal["report", "stop"]:
    """评估分派结束后的路由：分支就绪则进入报告生成，否则终止。"""
    return (
        "report"
        if state.get("status") in {"branches_ready", "branches_ready_with_failures"}
        else "stop"
    )


def build_talent_decision_engine(
    *,
    request_graph: Any,
    evaluation_graph: Any,
    report_graph: Any,
    checkpointer: Any | None = None,
):
    """组装并编译顶层“人才决策引擎”图。

    顺序串联三个子图：请求处理 → 评估分派执行 → 报告生成，
    并在每个阶段结束后依据状态做条件路由。传入 checkpointer 时（如
    Agent Server 或 FastAPI 运行服务），中断/恢复会复用同一线程状态。
    """
    builder = StateGraph(
        TalentDecisionEngineState,
        context_schema=DecisionContext,  # 决策上下文（租户/权限/操作者）
        input_schema=TalentDecisionEngineInput,
        output_schema=TalentDecisionEngineOutput,
    )
    # 注册三个子图节点
    builder.add_node("begin_turn", _begin_turn)
    builder.add_node("request_subgraph", request_graph)
    builder.add_node("evaluation_subgraph", evaluation_graph)
    builder.add_node("report_subgraph", report_graph)
    builder.add_edge(START, "begin_turn")
    builder.add_edge("begin_turn", "request_subgraph")
    # 请求处理完成后：候选人已就绪 → 评估；否则结束
    builder.add_conditional_edges(
        "request_subgraph",
        _route_after_request,
        {"evaluation": "evaluation_subgraph", "stop": END},
    )
    # 评估分派完成后：分支就绪 → 报告；否则结束
    builder.add_conditional_edges(
        "evaluation_subgraph",
        _route_after_evaluation,
        {"report": "report_subgraph", "stop": END},
    )
    builder.add_edge("report_subgraph", END)
    return builder.compile(checkpointer=checkpointer)