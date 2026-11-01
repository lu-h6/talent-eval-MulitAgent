"""人才工具 MCP 客户端：连接并调用远端 MCP 服务。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


def build_mcp_tool_graph(tools: Sequence[BaseTool]) -> Any:
    """构建一个可执行已发现 MCP 工具的最小 LangGraph 图。

    MCP（Model Context Protocol）工具在运行时被发现，这里把一组工具包装成
    LangGraph 的执行图：从起始节点进入工具节点统一执行，再回到结束节点。

    Args:
        tools: 从 MCP 服务发现并包装出的工具列表。

    Returns:
        编译后的可调用 LangGraph 应用。
    """
    # 构建最简状态图：输入为消息状态
    builder = StateGraph(MessagesState)
    # 工具节点：统一调用传入的所有工具
    builder.add_node("mcp_tools", ToolNode(list(tools)))
    # 从起点进入工具节点，执行后结束
    builder.add_edge(START, "mcp_tools")
    builder.add_edge("mcp_tools", END)
    return builder.compile()