from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol, Sequence

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker


class MilvusClientProtocol(Protocol):
    """Milvus 客户端的协议接口（便于测试时注入 Mock）。"""
    def has_collection(self, collection_name: str) -> bool: ...

    def create_schema(self, **kwargs: Any) -> Any: ...

    def prepare_index_params(self) -> Any: ...

    def create_collection(self, **kwargs: Any) -> Any: ...

    def upsert(self, **kwargs: Any) -> Any: ...

    def search(self, **kwargs: Any) -> Any: ...

    def hybrid_search(self, **kwargs: Any) -> Any: ...

    def delete(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class EvidenceRecord:
    """一条待写入向量库的证据记录。"""
    chunk_id: str  # 分块 ID（主键）
    candidate_id: str  # 候选人标识
    document_version_id: str  # 文档版本 ID
    tenant_id: str  # 租户标识
    document_type: str  # 文档类型
    permission_scope: str  # 权限范围
    embedding_model: str  # 向量化使用的模型
    content_hash: str  # 内容哈希（区别于重复向量）
    content: str  # 原文内容
    embedding: list[float]  # 稠密向量
    parent_chunk_id: str | None  # 父分块 ID（层级分块用）
    page_start: int | None  # 起始页码
    page_end: int | None  # 结束页码
    is_active: bool = True  # 是否生效（软删除标志）


@dataclass(frozen=True)
class EvidenceFilter:
    """证据检索的过滤条件。"""
    tenant_id: str  # 租户标识（必填）
    permission_scopes: list[str]  # 权限范围列表（必填，至少一个）
    candidate_ids: list[str] | None = None  # 限定的候选人 ID 列表
    document_types: list[str] | None = None  # 限定的文档类型列表
    document_version_id: str | None = None  # 限定某一文档版本


@dataclass(frozen=True)
class EvidenceSearchResult:
    """一条检索命中的结果。"""
    chunk_id: str
    candidate_id: str
    content: str  # 命中内容
    score: float  # 相似度得分
    metadata: dict[str, Any]  # 该条的完整元信息


def _literal(value: str) -> str:
    """把字符串编码为 Milvus 过滤表达式中的字面量。"""
    return json.dumps(value, ensure_ascii=False)


def _in_expression(field: str, values: Sequence[str]) -> str:
    """构造形如 "field in [v1, v2]" 的 IN 表达式。"""
    return f"{field} in [{', '.join(_literal(value) for value in values)}]"


def build_filter_expression(filters: EvidenceFilter) -> str:
    """把结构化过滤条件拼装为 Milvus 支持的过滤表达式字符串。

    必含：租户精确匹配、权限范围 IN 匹配、生效标志（is_active == true）；
    可选追加：候选人、文档类型、文档版本过滤。

    Returns:
        可传给 Milvus 的 filter 表达式。

    Raises:
        ValueError: 当缺少租户或权限范围时抛出。
    """
    if not filters.tenant_id:
        raise ValueError("tenant_id is required")
    if not filters.permission_scopes:
        raise ValueError("permission_scopes must not be empty")
    parts = [
        f"tenant_id == {_literal(filters.tenant_id)}",  # 租户隔离
        _in_expression("permission_scope", filters.permission_scopes),  # 权限过滤
    ]
    if filters.candidate_ids:
        parts.append(_in_expression("candidate_id", filters.candidate_ids))
    if filters.document_types:
        parts.append(_in_expression("document_type", filters.document_types))
    if filters.document_version_id:
        parts.append(f"document_version_id == {_literal(filters.document_version_id)}")
    parts.append("is_active == true")  # 只检索生效中的证据
    return " and ".join(parts)


class MilvusEvidenceStore:
    """证据向量库的存储与检索封装（基于 Milvus）。

    维护一个集合（collection），支持稠密向量 + 稀疏（BM25）混合检索，
    以及集合内数据的写入、检索与按版本删除。
    """

    # 检索时需要返回的所有字段
    output_fields = [
        "chunk_id",
        "candidate_id",
        "document_version_id",
        "tenant_id",
        "document_type",
        "permission_scope",
        "embedding_model",
        "content_hash",
        "content",
        "parent_chunk_id",
        "page_start",
        "page_end",
    ]

    def __init__(
        self,
        *,
        client: MilvusClientProtocol,
        collection_name: str,
        dimension: int,
    ) -> None:
        self.client = client  # Milvus 客户端
        self.collection_name = collection_name  # 集合名
        self.dimension = dimension  # 稠密向量维度

    @classmethod
    def connect(cls, *, uri: str, token: str, collection_name: str, dimension: int) -> "MilvusEvidenceStore":
        """按连接参数创建并返回 MilvusEvidenceStore 实例。"""
        return cls(
            client=MilvusClient(uri=uri, token=token),
            collection_name=collection_name,
            dimension=dimension,
        )

    def ensure_collection(self) -> None:
        """确保集合存在，不存在则按既定 schema 与索引创建。

        集合包含稠密向量（HNSW/COSINE）与稀疏向量（BM25 倒排索引），
        并在 content 字段上定义 BM25 函数用于稀疏检索。
        """
        if self.client.has_collection(self.collection_name):
            return
        # 定义集合 schema：关闭自增主键与动态字段
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=36)
        schema.add_field(field_name="candidate_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="document_version_id", datatype=DataType.VARCHAR, max_length=36)
        schema.add_field(field_name="tenant_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="document_type", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="permission_scope", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="embedding_model", datatype=DataType.VARCHAR, max_length=128)
        schema.add_field(field_name="content_hash", datatype=DataType.VARCHAR, max_length=64)
        # 正文内容：启用中文分词器，用于 BM25 稀疏检索
        schema.add_field(
            field_name="content",
            datatype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
        )
        schema.add_field(field_name="parent_chunk_id", datatype=DataType.VARCHAR, max_length=36, nullable=True)
        schema.add_field(field_name="page_start", datatype=DataType.INT64, nullable=True)
        schema.add_field(field_name="page_end", datatype=DataType.INT64, nullable=True)
        schema.add_field(field_name="is_active", datatype=DataType.BOOL)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=self.dimension)  # 稠密向量
        schema.add_field(field_name="content_sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)  # 稀疏向量（BM25）
        # 注册 BM25 函数：从 content 生成稀疏向量
        schema.add_function(
            Function(
                name="content_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["content"],
                output_field_names=["content_sparse"],
            )
        )

        # 分别为稠密向量与稀疏向量建立索引
        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_name="evidence_embedding_hnsw",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 128},
        )
        index_params.add_index(
            field_name="content_sparse",
            index_name="evidence_content_bm25",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        # 创建集合（使用有界一致性以平衡性能与一致性）
        self.client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
            consistency_level="Bounded",
        )

    def upsert(self, records: Sequence[EvidenceRecord]) -> int:
        """批量写入（更新或插入）证据记录，返回写入条数。

        会校验每条记录的向量维度是否与集合维度一致。

        Raises:
            ValueError: 当向量维度不匹配或集合未初始化时抛出。
        """
        if not records:
            return 0
        for record in records:
            if len(record.embedding) != self.dimension:
                raise ValueError(
                    f"embedding dimension {len(record.embedding)} does not match collection dimension {self.dimension}"
                )
        self.client.upsert(collection_name=self.collection_name, data=[asdict(record) for record in records])
        return len(records)

    def _to_results(self, rows: Any) -> list[EvidenceSearchResult]:
        """把 Milvus 原始返回行转换为统一的 EvidenceSearchResult 列表。"""
        results: list[EvidenceSearchResult] = []
        for hit in rows[0] if rows else []:
            entity = dict(hit.get("entity") or {})
            results.append(
                EvidenceSearchResult(
                    chunk_id=str(hit.get("id") or entity.get("chunk_id", "")),
                    candidate_id=str(entity.get("candidate_id", "")),
                    content=str(entity.get("content", "")),
                    score=float(hit.get("distance", 0.0)),
                    metadata=entity,
                )
            )
        return results

    def search(
        self,
        query_vector: list[float],
        *,
        filters: EvidenceFilter,
        limit: int = 10,
        ef: int = 80,
        consistency_level: str = "Bounded",
    ) -> list[EvidenceSearchResult]:
        """基于稠密向量的相似度检索（余弦相似度，HNSW）。

        Args:
            query_vector: 查询向量（维度须与集合一致）。
            filters: 过滤条件（租户/权限/候选人等）。
            limit: 返回结果条数，默认 10。
            ef: HNSW 搜索时的探索宽度，越大召回越准但越慢，默认 80。
            consistency_level: Milvus 一致性级别，默认 Bounded。

        Returns:
            命中的证据结果列表。

        Raises:
            ValueError: 当查询向量维度与集合维度不一致时抛出。
        """
        if len(query_vector) != self.dimension:
            raise ValueError(
                f"query dimension {len(query_vector)} does not match collection dimension {self.dimension}"
            )
        rows = self.client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            anns_field="embedding",
            filter=build_filter_expression(filters),
            limit=limit,
            output_fields=self.output_fields,
            search_params={"metric_type": "COSINE", "params": {"ef": ef}},
            consistency_level=consistency_level,
        )
        return self._to_results(rows)

    def sparse_search(
        self,
        query_text: str,
        *,
        filters: EvidenceFilter,
        limit: int = 10,
        consistency_level: str = "Bounded",
    ) -> list[EvidenceSearchResult]:
        """纯稀疏（BM25）检索。

        Args:
            query_text: 查询文本（用于 BM25 关键词匹配）。
            filters: 过滤条件。
            limit: 返回结果条数，默认 10。
            consistency_level: 一致性级别，默认 Bounded。

        Returns:
            命中的证据结果列表。
        """
        rows = self.client.search(
            collection_name=self.collection_name,
            data=[query_text],
            anns_field="content_sparse",
            filter=build_filter_expression(filters),
            limit=limit,
            output_fields=self.output_fields,
            search_params={"metric_type": "BM25"},
            consistency_level=consistency_level,
        )
        return self._to_results(rows)

    def hybrid_search(
        self,
        query_text: str,
        query_vector: list[float],
        *,
        filters: EvidenceFilter,
        limit: int = 10,
        ef: int = 80,
        rrf_k: int = 60,
        consistency_level: str = "Bounded",
    ) -> list[EvidenceSearchResult]:
        """混合检索：同时跑稠密 + 稀疏两种请求，用 RRF 融合排序。

        分别构造稠密（embedding/COSINE）与稀疏（content_sparse/BM25）请求，
        各自取 top-N（limit*2），再经 RRF（k=rrf_k）融合后返回 limit 条。

        Args:
            query_text: 查询文本（用于 BM25 稀疏检索）。
            query_vector: 查询向量（用于稠密检索，维度须与集合一致）。
            filters: 过滤条件。
            limit: 最终返回结果条数，默认 10。
            ef: 稠密检索的 HNSW 探索宽度，默认 80。
            rrf_k: 倒数排名融合参数 k，默认 60。
            consistency_level: 一致性级别，默认 Bounded。

        Returns:
            融合排序后的命中结果列表。

        Raises:
            ValueError: 当查询向量维度与集合维度不一致时抛出。
        """
        if len(query_vector) != self.dimension:
            raise ValueError(
                f"query dimension {len(query_vector)} does not match collection dimension {self.dimension}"
            )
        filter_expression = build_filter_expression(filters)
        # 稠密检索请求
        dense_req = AnnSearchRequest(
            data=[query_vector],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": ef}},
            limit=limit * 2,
            filter=filter_expression,
        )
        # 稀疏检索请求
        sparse_req = AnnSearchRequest(
            data=[query_text],
            anns_field="content_sparse",
            param={"metric_type": "BM25"},
            limit=limit * 2,
            filter=filter_expression,
        )
        rows = self.client.hybrid_search(
            collection_name=self.collection_name,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=rrf_k),
            limit=limit,
            output_fields=self.output_fields,
            consistency_level=consistency_level,
        )
        return self._to_results(rows)

    def delete_version(self, *, tenant_id: str, document_version_id: str) -> None:
        """删除某个文档版本的全部向量记录（集合存在时）。"""
        if not self.client.has_collection(self.collection_name):
            return
        expression = (
            f"tenant_id == {_literal(tenant_id)} and "
            f"document_version_id == {_literal(document_version_id)}"
        )
        self.client.delete(collection_name=self.collection_name, filter=expression)