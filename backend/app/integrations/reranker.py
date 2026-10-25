from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from dashscope.rerank import TextReRank

from app.core.config import Settings, get_settings


class Reranker(Protocol):
    """Rerank 器的统一协议接口（用于类型标注与依赖注入）。"""
    def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> list[dict]:
        ...


@dataclass(frozen=True)
class RerankHit:
    """一条重排命中的结果：在原文中的下标与相关性得分。"""
    index: int
    score: float


class DashScopeReranker:
    """基于 DashScope TextReRank 的 Rerank 实现。"""

    def __init__(self, *, model: str, api_key: str) -> None:
        self.model = model  # Rerank 模型名
        self.api_key = api_key  # DashScope API Key

    def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int,
    ) -> list[RerankHit]:
        """对候选文档按与 query 的相关性重新排序，返回得分最高的 top_n 条。

        Args:
            query: 查询文本。
            documents: 待重新排序的文档列表。
            top_n: 返回的最多结果条数。

        Returns:
            按得分解读的 RerankHit 列表（含原下标与得分）。

        Raises:
            RuntimeError: 当调用失败（状态码非 200）时抛出。
        """
        # 调用 DashScope 重排接口
        response = TextReRank.call(
            model=self.model,
            query=query,
            documents=list(documents),
            top_n=top_n,
            api_key=self.api_key,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"rerank failed: status={response.status_code} "
                f"code={getattr(response, 'code', None)} message={getattr(response, 'message', None)}"
            )
        # 把底层结果转换为 RerankHit 列表
        return [
            RerankHit(index=item.index, score=float(item.relevance_score))
            for item in response.output.results
        ]


def get_reranker(settings: Settings | None = None) -> DashScopeReranker | None:
    """创建 Rerank 器；未配置 API Key 时返回 None。"""
    value = settings or get_settings()
    if not value.dashscope_api_key or value.dashscope_api_key == "请用户自行填写":
        return None
    return DashScopeReranker(model=value.rerank_model, api_key=value.dashscope_api_key)