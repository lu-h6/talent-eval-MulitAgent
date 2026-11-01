# 后端服务

独立 uv Python 项目，包含 FastAPI 应用、文档解析与切块流水线、Milvus 证据索引、LangGraph 评估流程、
数据库模型和自动化测试。

## 主要模块

| 模块 | 用途 |
|---|---|
| `app/api/` | HTTP 路由、请求模型与看板指标 |
| `app/core/` | 配置与日志 |
| `app/db/` | SQLAlchemy 模型与会话 |
| `app/documents/` | 文档解析、切块、对象存储与上传流水线 |
| `app/evaluation/` | 评测用例采集、Judge、评测 HTTP 接口 |
| `app/integrations/` | Milvus、模型、rerank、MCP 客户端与服务端 |
| `app/retrieval/` | 查询计划、混合检索、证据包与引用 |
| `app/talent/` | 请求理解图、评估分发、报告合成、运行流与会话 |
| `scripts/` | 验收脚本与演示数据导入 |
| `samples/` | 合成样例数据 |

## 关键流程

- **文档流水线**：上传 → 解析 → 切块（Parent-Child）→ 证据索引，任务由 worker 消费。
- **评估流程**：请求理解 → 候选人筛选 → 动态维度分发 → 证据抽取 → 单维度评分 → 一致性检查 → 报告合成。
- **运行与会话**：`TalentRunManager` 直接驱动决策主图，事件投影为前端可消费的 SSE；运行历史与会话读模型落 PostgreSQL。
- **评测**：从真实图调用边界采集用例，支持人工标注、Judge 评分、数据集固化与回放。

## 环境初始化

```bash
uv sync --dev
```

## 运行测试

```bash
uv run pytest tests -q
```

需要外部 Chat Model 的真实抽取测试：

```bash
RUN_LIVE_MODEL_TESTS=1 uv run --no-sync pytest \
  tests/test_talent_evaluation_branch_agent_live.py -q -s
```

## 验收脚本

| 脚本 | 需要 |
|---|---|
| `scripts/verify_talent_hitl_resume.py` | 无外部依赖（内存图） |
| `scripts/verify_talent_evaluation_dispatch.py` | PostgreSQL + Milvus + 模型 |
| `scripts/verify_talent_run_live.py` | PostgreSQL + Milvus + 模型 |
| `scripts/verify_evidence_pack.py` | 默认离线；`--live-model` 需模型 |

## 启动 API

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 18080
```

启动时若 Milvus / PostgreSQL 未就绪，进程仍会启动并在日志中给出提示；`/api/health` 的
`talent_runtime` 字段会显示 `unavailable`，此时运行类接口不可用。
