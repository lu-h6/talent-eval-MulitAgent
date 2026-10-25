# 后端服务

本目录是独立的 uv Python 项目，包含 FastAPI 应用、解析器、Chunk 流水线、Milvus 证据索引、数据库模型和自动化测试

## 目录结构

`app/` 按职责划分为 7 个功能包：

| 路径 | 用途 |
|---|---|
| `app/api/` | HTTP 路由与请求、响应 Schema |
| `app/core/` | 应用配置与日志配置 |
| `app/db/` | 数据库连接与 ORM 模型 |
| `app/documents/` | 文档上传、解析、切片、流水线与清理 |
| `app/integrations/` | MinerU、Milvus、模型、对象存储与人才 MCP 适配 |
| `app/retrieval/` | Query Plan、混合检索、证据索引、证据包与引用 |
| `app/talent/` | 人才工具、决策与评估图、运行时和课程演示数据 |

`main.py`、`worker.py` 和 `__init__.py` 保留在 `app/` 根目录，API 与 worker 启动入口仍为 `app.main:app` 和 `python -m app.worker`。所有应用代码仍位于 `app/` 内，`tests/` 与 `scripts/` 保持在 `app/` 外

## 主要模块

| 模块 | 用途 |
|---|---|
| `app/documents/chunking.py` | Markdown 层级切分与递归兜底切分 |
| `app/documents/chunk_service.py` | 解析结果对齐、策略自动判断与 Parent-Child 持久化 |
| `app/documents/document_parsers.py` | DOCX、PPTX、Markdown 和音频转录的归一化文本解析 |
| `app/integrations/milvus_store.py` | Milvus Collection Schema、索引、Upsert、过滤和检索 |
| `app/retrieval/evidence_index_service.py` | Chunk 向量化、索引任务执行与证据记录映射 |
| `app/integrations/model_provider.py` | 百炼 Chat 与 Embedding 模型配置 |
| `app/talent/talent_evaluation_dispatch.py` | 动态维度、候选人 × 维度分发、要求级并发 Branch Worker 与 Requirement Evidence 协议 |
| `app/talent/talent_evaluation_runtime.py` | 装配第 15 课分发图与第 16 课报告图，分别导出 `graph` 和 `report_graph` |
| `app/talent/talent_evaluation_report.py` | 12 路单维度评分、候选人级一致性检查、短引用映射、候选人聚合、排序和报告校验渲染 |
| `scripts/verify_talent_evaluation_report.py` | 读取第 15 课分发结果并调用正式 `report_graph` 验证评分、聚合和报告链路 |
| `app/talent/talent_request_graph.py` | 第 14 课输入编译图；第 17 课扩展 `filter_candidates`、`resolve_conditions`、`resolve_empty_candidates` 节点，支持空候选 leave-one-out 诊断、条件修订写回、带错重问、取消与局部重跑 |
| `scripts/verify_talent_hitl_resume.py` | 第 17 课 HITL 链路验证：直达、空候选放宽恢复、矛盾条件修订、过期提交重问、取消和 Time Travel 分叉 |

第 16 课报告图默认最多并发执行 12 个候选人 × 维度评分任务，并按候选人并发执行跨维度一致性检查。模型上下文使用 `E1`、`E2` 形式的短证据编号，图 State 保留短编号到完整 Chunk ID 的映射，模型返回后恢复完整 ID 再执行白名单和候选人归属校验。报告正文显示短编号，引用链接仍使用完整 Chunk ID 与原文偏移

## 环境初始化

```bash
uv sync --dev
```

## 运行测试

```bash
uv run pytest tests -q
```

运行需要外部 Chat Model 的真实 Evidence Pack 抽取测试：

```bash
RUN_LIVE_MODEL_TESTS=1 uv run --no-sync pytest \
  tests/test_talent_evaluation_branch_agent_live.py -q -s
```

运行第 15 课真实分发链路并输出结构化诊断日志：

```bash
uv run --no-sync python -m scripts.verify_talent_evaluation_dispatch \
  --log-level INFO
```

`--max-concurrency` 默认值为 6，用于控制 LangGraph 候选人 × 维度分支的并发上限。每个分支内部并发执行当前维度的全部 `evidence_requirements`，并按原始要求顺序聚合结果。

日志使用 `task_id`、`call_id`、`requirement_id` 和 `chunk_id` 连接图节点、工具调用、混合检索、原文回表、事实抽取和来源校验，不记录履历原文或模型 Prompt

运行第 17 课 HITL 可恢复链路验证：

```bash
uv run --no-sync python -m scripts.verify_talent_hitl_resume
```

`build_talent_request_graph(candidate_filter=None)` 保持第 14 课图形状；`langgraph.json` 注册的 `talent_request` 图挂载 `database_candidate_filter`，编译出可执行计划后继续执行候选人筛选，空候选或条件矛盾时进入 interrupt 等待修订

## 启动 API

在 `backend/` 目录下执行：

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 18080
```

该命令依赖当前工作目录：`app` 是源码包，`uvicorn` 必须在 `backend/` 下启动。若在仓库根目录执行，改用 `uv run --project backend uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 18080`（或运行根目录的 `run-backend.ps1`）。
