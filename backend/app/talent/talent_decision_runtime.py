from __future__ import annotations

from typing import Any

from app.talent.talent_decision_engine import build_talent_decision_engine
from app.talent.talent_evaluation_runtime import graph as evaluation_graph
from app.talent.talent_evaluation_runtime import report_graph
from app.talent.talent_request_graph import graph as request_graph


def build_runtime_decision_graph(*, checkpointer: Any | None = None):
    """按需构建顶层决策图，可注入 checkpointer（Agent Server 或运行服务）。"""

    return build_talent_decision_engine(
        request_graph=request_graph,
        evaluation_graph=evaluation_graph,
        report_graph=report_graph,
        checkpointer=checkpointer,
    )


# 顶层决策引擎：组合“请求处理图 + 评估分派执行图 + 报告生成图”三个子图。
# Agent Server 提供父级 checkpointer，三个子图使用各自默认的按次持久化，
# 并在运行时继承父级 checkpointer。
graph = build_runtime_decision_graph()
