# 多 Agent 人才评估与推荐系统（智鉴 TalentOS）

围绕“人才档案 → 证据检索 → 多 Agent 评估 → 报告与评测”的完整链路：后端用 FastAPI + LangGraph
编排请求理解、候选人筛选、动态维度分发、证据抽取、评分与报告合成；前端提供档案管理、评估与推荐
对话、评测中心三个工作面。

## 目录结构

```
backend/
├── app/
│   ├── api/             HTTP 路由与请求模型
│   ├── core/            配置与日志
│   ├── db/              SQLAlchemy 模型与会话
│   ├── documents/       解析、切块、上传等文档流水线
│   ├── evaluation/      评测用例、Judge、评测 HTTP 接口
│   ├── integrations/    Milvus / 对象存储 / 模型 / rerank / MCP
│   ├── retrieval/       查询计划、混合检索、证据包与引用
│   └── talent/          请求理解图、评估分发、报告、运行与会话
├── scripts/             验收脚本与数据导入脚本
├── samples/             合成样例数据
└── tests/               pytest 用例
database/                init.sql 与增量迁移
frontend/                React + Vite 前端
docs/                    设计文档
```

## 环境依赖

| 组件 | 端口 | 用途 |
|---|---|---|
| PostgreSQL | 15432 | 业务表、Run/会话、评测用例、LangGraph checkpoint |
| MinIO | 19000 | 原始文件与解析产物 |
| Redis | 16379 | worker 任务队列 |
| Milvus（+etcd） | 19531 | 证据向量索引与检索 |
| MinerU（可选） | 18001 | PDF 解析 |

```bash
cp .env.example .env      # 填 DASHSCOPE_API_KEY
docker compose up -d postgres minio redis etcd milvus
```

### 数据库初始化

- 新库：`init.sql` 已包含全部业务表，随 compose 首次启动自动执行。
- 已有库：按需执行 `database/migrations/*.sql`（`020`–`024` 为 Run 历史、会话与评测相关表，均幂等）。

## 启动

```bash
# 后端
cd backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 18080

# worker（解析 / 切块 / 索引任务）
uv run python -m app.worker

# 前端
cd frontend
npm install
npm run dev        # http://127.0.0.1:15173
```

健康检查：`GET /api/health` 返回 `talent_runtime=ready|unavailable`。后者表示 Milvus 或 Postgres
未就绪——此时服务仍可启动，但 `/api/runs` 等依赖运行图的接口不可用。

## 开发步骤

| 步骤 | 主题 | 主要落点 |
|---|---|---|
| 1–3 | FastAPI 骨架、上传与对象存储、解析任务 | `app/api`、`app/documents` |
| 4–5 | 文档解析归一化、切块与 Parent-Child 持久化 | `documents/parsers.py`、`documents/chunking.py`、`migrations/005` |
| 6 | Milvus 证据索引与检索存储 | `integrations/milvus_store.py`、`migrations/007` |
| 7–8 | 查询计划 DSL、复合人才筛选 | `retrieval/query_plan.py`、`talent/talent_tools.py` |
| 9–10 | 证据包、引用与检索验收台 | `retrieval/evidence_pack.py`、`retrieval/evidence_citations.py` |
| 11 | LangGraph 状态模型与决策主图 | `talent/talent_decision_graph.py` |
| 12 | 人才数据工具与统一证据协议 | `talent/talent_tools.py`、`migrations/012` |
| 13 | MCP 人才能力服务 | `integrations/talent_mcp_server.py` |
| 14 | 任务理解与执行计划编译 | `talent/talent_request_graph.py` |
| 15 | 动态评估维度与分发执行 | `talent/talent_evaluation_dispatch.py` |
| 16 | 证据评估与报告合成 | `talent/talent_evaluation_report.py` |
| 17 | 人机协同（HITL）与可恢复执行 | `talent/talent_request_graph.py` |
| 18 | 子图整合为决策引擎 | `talent/talent_decision_engine.py` |
| 19 | 运行流式输出（SSE）与事件投影 | `talent/run_stream.py`、`talent/event_projection.py` |
| 20–21 | 运行历史与会话读模型 | `talent/run_history.py`、`talent/run_api.py`、`migrations/020`–`021` |
| 22–24 | 评测用例采集、数据集、Judge 与回放 | `evaluation/`、`migrations/022`–`024` |

## 测试

```bash
cd backend
uv run pytest tests -q                     # 单元与契约测试
uv run python -m scripts.verify_talent_hitl_resume      # HITL 链路
uv run python -m scripts.verify_talent_run_live         # 需要真实库 + Milvus + 模型
```

```bash
cd frontend
npm test
npm run build
```

## 主要接口

| 分组 | 路径 |
|---|---|
| 档案 | `/api/employees`、`/api/knowledge-bases`、`/api/documents` |
| 检索 | `/api/evidence/search`、`/api/documents/{id}/chunks` |
| 运行与会话 | `/api/runs`、`/api/runs/{id}/events`、`/api/runs/history`、`/api/conversations` |
| 评测 | `/api/evaluation/cases`、`/api/evaluation/judge`、`/api/evaluation/replay`、`/api/evaluation/feedback` |
| 看板 | `/api/dashboard/metrics` |

## 约定

- 租户与权限由 `DecisionContext` 注入，不从用户文本或模型输出中提取。
- 模型输出只作为候选，落库前统一走 Pydantic 校验与白名单校验。
- 证据引用使用短编号（`E1`…）渲染，图状态保留到完整 Chunk ID 的映射。
