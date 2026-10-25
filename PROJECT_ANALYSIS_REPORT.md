# 多 Agent 人才评估与推荐系统：整体接手分析报告

分析日期：2026-10-13  
分析对象：`talent-eval-agents` 当前工作树  
分析视角：第一次接手项目的开发者，需要快速理解产品、架构、数据流、课程模块、运行方式和风险

## 1. 先看结论

这是一个面向 HR 和人才评估场景的多 Agent 系统。它不是单一的聊天机器人，而是一条从人才档案入库到证据检索、候选人评估、报告合成和人机协同的完整业务链路。

核心链路可以概括为：

```text
人才基础档案
    ↓
候选人材料上传
    ↓
文件版本与对象存储
    ↓
MinerU / 原生解析器 / Whisper
    ↓
Markdown、结构化 JSON、来源定位
    ↓
Parent-Child Chunk
    ↓
Milvus 向量和 BM25 检索
    ↓
Query Plan 与候选人筛选
    ↓
可信 Evidence Pack
    ↓
LangGraph 任务理解、评估分发、报告合成
    ↓
Human-in-the-Loop 与最终决策
```

项目的主入口和基础服务关系如下：

```text
React/Vite 前端
        ↓ HTTP
FastAPI API ─────────────── PostgreSQL
        │                         ├ 员工、文档、版本、任务、Chunk
        │                         └ 查询计划、工具审计、评估状态
        ├── Redis ─────────────── 异步解析和索引队列
        ├── MinIO ─────────────── 原文件和解析产物
        ├── MinerU ────────────── PDF/图片版面解析
        ├── Milvus ────────────── 向量、BM25、标量过滤
        └── DashScope/模型服务 ── 计划、抽取、评分、报告
```

第一次接手时，最需要先记住三点：

1. PostgreSQL 是业务事实和任务状态的主存储，Milvus 只是检索索引，MinIO 保存文件和解析产物。
2. LangGraph 不是整个系统的入口，它位于检索、人才工具和证据协议之上，负责复杂任务编排。
3. 当前工作树正在进行目录重构；实际运行前必须先处理 `app.*` 与 `backend.app.*` 混用导致的导入问题。

## 2. 项目定位和用户流程

### 2.1 面向的业务问题

系统试图解决以下问题：

- HR 维护员工和候选人的基础信息。
- 将简历、面试记录、项目复盘、绩效、证书和晋升材料集中管理。
- 把 PDF、Word、PPT、表格、网页、Markdown 和音频转成可检索内容。
- 根据岗位要求筛选候选人。
- 用混合检索找到候选人的证据。
- 要求模型只能基于经过校验的证据进行评分和报告。
- 对岗位歧义、空候选人、证据缺失和评分冲突进行人工确认或可恢复处理。

### 2.2 前端当前提供的工作台

前端是一个 React 单页应用，主要包含：

- 员工花名册。
- 档案知识库。
- 文件上传和材料列表。
- 文档详情。
- 原文件、解析结果、Chunk 和元数据查看。
- 证据召回测试台。
- Evidence Pack 查看面板。

前端 API 地址和租户默认值直接配置在入口文件中：[main.tsx](frontend/src/main.tsx:1)。

后端 API 主要包括：

- `/api/employees`
- `/api/knowledge-bases`
- `/api/documents`
- `/api/documents/{id}/parse`
- `/api/documents/{id}/chunks`
- `/api/documents/{id}/evidence-index`
- `/api/evidence/search`
- `/api/evidence/hybrid-search`
- `/api/talent-search`
- `/api/evidence/citations/{chunk_id}`

接口清单见：[README.md](README.md:92)。

## 3. 技术栈和运行环境

| 层次 | 技术 | 关键位置 |
|---|---|---|
| 后端 | Python 3.12+、FastAPI、SQLAlchemy | [backend/pyproject.toml](backend/pyproject.toml:1) |
| 数据校验 | Pydantic、Pydantic Settings | [backend/pyproject.toml](backend/pyproject.toml:20) |
| Agent 编排 | LangGraph、LangChain | [backend/pyproject.toml](backend/pyproject.toml:14) |
| 协议服务 | MCP Server、MCP Client | [talent_mcp_server.py](backend/app/integrations/talent_mcp_server.py:1) |
| 前端 | React、Vite、TypeScript | [frontend/package.json](frontend/package.json:1) |
| 关系数据库 | PostgreSQL 15 | [docker-compose.yml](docker-compose.yml:2) |
| 对象存储 | MinIO / S3 API | [object_store.py](backend/app/integrations/object_store.py:1) |
| 消息队列 | Redis List | [document_pipeline.py](backend/app/documents/document_pipeline.py:26) |
| 向量检索 | Milvus 2.6.17 | [docker-compose.yml](docker-compose.yml:62) |
| 版面解析 | MinerU 独立服务 | [mineru_client.py](backend/app/integrations/mineru_client.py:1) |
| 音频转写 | faster-whisper | [document_parsers.py](backend/app/documents/document_parsers.py:146) |
| 模型服务 | DashScope OpenAI 兼容接口 | [model_provider.py](backend/app/integrations/model_provider.py:1) |
| 包管理 | uv、npm | `backend/uv.lock`、`frontend/package-lock.json` |

Docker Compose 启动 PostgreSQL、Redis、MinIO、etcd、Milvus，并可通过 `app` profile 启动 backend、worker 和 frontend：[docker-compose.yml](docker-compose.yml:1)。

## 4. 目录结构：接手时先看哪里

```text
backend/app/main.py                  FastAPI 应用入口
backend/app/worker.py                Redis 队列消费者
backend/app/api/routes.py            HTTP 接口和业务编排入口
backend/app/api/schemas.py           API Schema
backend/app/core/config.py           环境配置
backend/app/core/logging_config.py   日志配置
backend/app/db/database.py           SQLAlchemy Engine/Session
backend/app/db/models.py             ORM 数据模型
backend/app/documents/               上传、解析、切片、清理、流水线
backend/app/integrations/            MinerU、Milvus、模型、对象存储、MCP
backend/app/retrieval/               Query Plan、证据索引、混合检索、引用
backend/app/talent/                  人才工具、决策图、评估和报告
backend/scripts/                     数据导入、验证和演示脚本
backend/tests/                       单元测试、接口测试、图测试
database/init.sql                    全新数据库结构
database/migrations/                 增量迁移
frontend/src/main.tsx                前端主页面和 API 调用
frontend/src/EvidencePackPanel.tsx   证据包展示
README.md                            项目运行说明和课程演进记录
```

后端新目录的职责划分见：[backend/README.md](backend/README.md:5)。

## 5. 一次完整请求如何流转

### 5.1 材料入库

1. 前端向 `POST /api/documents` 上传文件和候选人信息。
2. `upload_document()` 创建 `Document`、`FileObject` 和 `DocumentVersion`。
3. 文件写入 MinIO，数据库保存文件名、MIME、大小和 SHA256。
4. API 创建 `ParseJob`，把任务放入 Redis 的 `talent:parse:queue`。
5. worker 消费任务，调用 `parse_version()`。

关键文件：[routes.py](backend/app/api/routes.py:279)、[services.py](backend/app/documents/services.py:24)、[worker.py](backend/app/worker.py:1)。

### 5.2 文档解析

解析器根据扩展名路由：

| 文件 | 处理方式 |
|---|---|
| DOCX | `python-docx` 读取段落和表格，转换为 Markdown |
| Markdown | 直接读取，生成内容列表 |
| CSV/JSON/HTML | LangChain Loader 处理 |
| PPTX | `python-pptx` 读取文本框、标题和坐标 |
| XLSX | `openpyxl` 读取 sheet 和行数据 |
| PDF/图片 | MinerU 返回 Markdown、content list、页码和 bbox |
| WAV/MP3/M4A | faster-whisper 转录并保存时间段 |

统一结果类型是 `ParseResult`，包含 `parser_name`、`text`、`metadata` 和 `structured`：[document_parsers.py](backend/app/documents/document_parsers.py:17)。

解析任务将 Markdown 和结构化 JSON 存入 MinIO，并在 `parse_artifacts` 中保存索引：[services.py](backend/app/documents/services.py:103)。

### 5.3 Chunk 和索引

Chunk 流程先从解析产物恢复 `ChunkElement`，再根据是否存在标题自动选择 Markdown 层级切分或递归字符切分。

结果保存为 Parent-Child Chunk，并包含标题路径、页码、时间段、Markdown 偏移和来源定位：[chunk_service.py](backend/app/documents/chunk_service.py:111)。

索引流程把 Child Chunk 转为 Evidence Record，调用 Embedding 写入 Milvus；PostgreSQL 记录 `EvidenceIndexJob` 状态，便于重试和展示：[evidence_index_service.py](backend/app/retrieval/evidence_index_service.py:1)。

### 5.4 人才检索

人才检索不是直接把自然语言交给向量库，而是分成两层：

```text
自然语言需求
    ↓
Query Plan
    ├── 结构化硬条件
    ├── 语义要求
    ├── 偏好
    └── 待澄清条件
    ↓
SQL 筛选候选人
    ↓
候选人范围内的证据检索
    ↓
向量 / BM25 / RRF / Rerank
    ↓
可信 Chunk 和 Evidence Pack
```

Query Plan 和确定性 SQL Builder 位于：[query_plan.py](backend/app/retrieval/query_plan.py:1)。混合检索位于：[hybrid_search_service.py](backend/app/retrieval/hybrid_search_service.py:1)。

### 5.5 评估与决策

后续 LangGraph 使用前面生成的 `TalentRequest`、`QueryPlan`、候选人集合和证据包：

```text
任务理解
  → 岗位匹配与澄清
  → 候选人筛选
  → 动态评估维度
  → 候选人 × 维度并行评估
  → 证据评分和冲突检查
  → 候选人聚合排序
  → Markdown 报告
  → HITL 恢复或最终决策
```

主图和子图注册见：[langgraph.json](backend/langgraph.json:1)。

## 6. 数据模型总览

### 6.1 核心实体

```text
EmployeeProfile
      │
      ├── Document ── KnowledgeBase
      │       │
      │       └── DocumentVersion ── FileObject
      │               │
      │               └── ParseJob ── ParseArtifact
      │                       │
      │                       └── ChunkingRun ── DocumentChunk
      │                               │
      │                               └── EvidenceIndexJob → Milvus
      │
      └── JobDescription → 人才任务理解和岗位匹配
```

### 6.2 主要表的职责

| 表 | 作用 | 接手时关注点 |
|---|---|---|
| `employee_profiles` | 员工基础档案 | 当前偏主表，履历明细主要仍在文档中 |
| `job_descriptions` | 岗位 JD | 被第 12、14 课工具和任务理解使用 |
| `knowledge_bases` | 档案资料库 | 保存租户和权限范围 |
| `documents` | 业务文档 | 关联候选人、员工、知识库和权限 |
| `file_objects` | 物理文件索引 | 保存对象键、大小、MIME 和 SHA256 |
| `document_versions` | 文件版本 | 每个业务文档的当前版本和历史版本 |
| `parse_jobs` | 解析任务状态 | 保存解析器、进度、错误和重试次数 |
| `parse_artifacts` | 解析产物索引 | 产物实际内容在 MinIO |
| `document_metadata` | 扩展元数据 | Key-Value JSONB，当前约束较弱 |
| `chunking_runs` | 切片运行记录 | 保存策略、参数和切片器版本 |
| `document_chunks` | 证据单元 | 同时支持父子、前后链和来源定位 |
| `evidence_index_jobs` | 向量索引任务 | 记录 PostgreSQL 与 Milvus 状态 |
| `tool_call_audits` | 人才工具审计 | 只保存参数键和运行摘要 |

ORM 模型位于：[models.py](backend/app/db/models.py:46)，数据库结构位于：[init.sql](database/init.sql:9)。

## 7. 第 1 到第 18 课在项目中的位置

下面的课程表是项目导航，不是进度评估。每一课对应一组代码职责，并通过输入输出连接到下一层。

| 课程 | 主题 | 在项目中的落点 | 依赖关系 |
|---|---|---|---|
| 第 1 课 | 课程导论、业务背景、多 Agent 架构选择 | README 的产品定位、整体目录、FastAPI/worker/frontend/基础设施划分 | 全项目总览 |
| 第 2 课 | LLM、Prompt 和 Agent 工作流 | `model_provider.py`、LangGraph 运行时、Query Plan、人才工具和评估图 | 为后续图和工具提供模型能力 |
| 第 3 课 | 人才档案数据建模与基础文档解析 | `db/models.py`、`init.sql`、`document_parsers.py`、`parsers.py` | 为所有检索和评估提供原始数据 |
| 第 4 课 | MinerU 复杂文档解析与异步任务 | `mineru_client.py`、`documents/services.py`、`document_pipeline.py`、Redis worker | 第 3 课的解析入口扩展 |
| 第 5 课 | Chunk 设计与证据单元 | `chunking.py`、`chunk_service.py`、`005_chunking.sql` | 消费 Markdown 和结构化解析产物 |
| 第 6 课 | 人才证据索引与向量检索 | `milvus_store.py`、`evidence_index_service.py`、`007_milvus_evidence_index.sql` | 消费 Child Chunk |
| 第 7 课 | 混合检索与精排 | `hybrid_search_service.py`、`reranker.py` | 组合 dense、BM25、RRF 和 rerank |
| 第 8 课 | 复合人才检索与 Query Plan | `query_plan.py`、`/talent-search/*`、`test_talent_search_api.py` | 先结构化筛选，再语义检索 |
| 第 9 课 | 可信证据输出 | `evidence_pack.py`、Evidence Pack 2.0、引用校验 | 把模型输出限制在可信来源内 |
| 第 10 课 | 检索链路、引用展示和验收台 | `evidence_citations.py`、前端召回测试台、引用接口 | 将 Chunk 和原文重新绑定展示 |
| 第 11 课 | LangGraph State 和人才决策主图 | `talent_decision_graph.py`、`talent_decision_runtime.py` | 定义决策输入、状态、节点和路由 |
| 第 12 课 | 人才数据工具和统一证据协议 | `talent_tools.py`、`012_talent_tools.sql`、工具审计 | 将数据库和检索能力包装为受控工具 |
| 第 13 课 | MCP 人才能力服务 | `talent_mcp_server.py`、`talent_mcp_client.py`、MCP 验证脚本 | 对外暴露工具、Resource 和 Prompt |
| 第 14 课 | 任务理解、条件澄清和执行计划 | `talent_request_graph.py`、`lesson14_demo_data.py` | 将用户请求编译成 TalentRequest 和 QueryPlan |
| 第 15 课 | 动态评估维度与 Subagent 分发 | `talent_evaluation_dispatch.py`、`talent_evaluation_runtime.py` | 候选人 × 维度并行生成评估结果 |
| 第 16 课 | 证据评估、冲突检查和报告合成 | `talent_evaluation_report.py`、`report_graph` | 对 Evidence Pack 评分、聚合和渲染报告 |
| 第 17 课 | Human-in-the-Loop 和可恢复执行 | `talent_request_graph.py` 的中断恢复逻辑、`verify_talent_hitl_resume.py` | 处理岗位确认、空候选、修订、取消和分叉 |
| 第 18 课 | 人才评估子图与决策引擎整合 | `talent_decision_engine.py`、`talent_decision_runtime.py` | 串联任务理解、评估分发和报告子图 |

### 7.1 课程之间的输入输出关系

```text
第 1/2 课：架构与模型能力
       ↓
第 3/4 课：人才数据、文件、解析任务和解析产物
       ↓
第 5/6/7 课：Chunk、向量索引、混合检索和精排
       ↓
第 8/9/10 课：Query Plan、候选人范围、可信证据和引用展示
       ↓
第 11/12/13 课：State、工具协议、MCP 能力服务
       ↓
第 14/17 课：任务理解、澄清、修改和恢复
       ↓
第 15/16 课：动态评估、证据评分、冲突检查和报告
       ↓
第 18 课：决策引擎总编排
```

## 8. Agent 和评估层的理解方式

### 8.1 Query Plan 不是最终评估

`QueryPlan` 只回答“要找哪些人、需要哪些语义证据”。它不是评分结果，也不是最终报告。

它包含结构化过滤条件、语义要求、用户偏好、待澄清条件和任务类型。候选人筛选使用确定性 SQL Builder，模型不能直接生成原始 SQL：[query_plan.py](backend/app/retrieval/query_plan.py:1)。

### 8.2 Talent Tools 是受控能力层

第 12 课将数据访问封装为四类只读工具：查询岗位 JD、筛选候选人、搜索候选人证据、查询候选人档案。工具上下文中注入租户、权限、身份和运行编号，工具审计只记录必要摘要：[talent_tools.py](backend/app/talent/talent_tools.py:1)。

### 8.3 MCP 是能力发布层

第 13 课把人才工具进一步发布为 MCP Server，提供 Tools、Resources、Prompt 和 Access Token Context。MCP Client 再把外部 MCP 工具适配到 LangGraph ToolNode：[talent_mcp_client.py](backend/app/integrations/talent_mcp_client.py:1)。

### 8.4 评估报告是多阶段流水线

第 15 到第 16 课依次执行动态评估维度生成、候选人和维度任务分发、证据搜索、事实抽取、维度评分、跨维度冲突检查、程序聚合排序和 Markdown 报告渲染。[talent_evaluation_report.py](backend/app/talent/talent_evaluation_report.py:1)

## 9. 本地运行手册

### 9.1 启动基础设施

```bash
docker compose -p talent-eval-agents-course up -d
```

主要端口：PostgreSQL `15432`、Redis `16379`、MinIO API `19000`、MinIO Console `19001`、Milvus gRPC `19531`、Milvus Health `19091`。[README.md](README.md:743)

### 9.2 启动 MinerU、后端、worker 和前端

```bash
uv sync --project backend/mineru-runtime
uv run --project backend/mineru-runtime mineru-api --host 127.0.0.1 --port 18001

cd backend
uv sync --dev
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 18080
uv run python -m app.worker

cd frontend
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:15173`。LangGraph 开发服务使用：

```bash
cd backend
uv run langgraph dev --no-browser --no-reload --port 2024
```

图注册在：[langgraph.json](backend/langgraph.json:1)。

## 10. 当前接手时的关键风险

### 10.1 目录重构的导入路径不一致

当前测试和 README 使用 `app.*`，但以下文件仍引用 `backend.app.*`：

- [chunk_service.py](backend/app/documents/chunk_service.py:12)
- [database.py](backend/app/db/database.py:4)
- [talent_tools.py](backend/app/talent/talent_tools.py:17)

按 README 进入 `backend` 后运行 pytest，实际出现 `ModuleNotFoundError: No module named 'backend'`。这是接手后的第一项排查任务。

### 10.2 测试样本路径不完整

`test_document_parsers.py` 使用 `backend/sample-data/generated`，当前工作树没有这个目录，单独运行解析测试会出现 `IndexError`：[test_document_parsers.py](backend/tests/test_document_parsers.py:5)。

### 10.3 权限头和真实认证的边界需要确认

证据搜索和引用接口读取 `X-Tenant-Id`、`X-Permission-Scopes`：[routes.py](backend/app/api/routes.py:442)。主应用中可见的是 CORS 和日志中间件，没有看到统一 HTTP 认证中间件：[main.py](backend/app/main.py:22)。如果 API 直接暴露给客户端，需要由 JWT/OIDC 或网关注入可信租户和权限上下文。

### 10.4 数据一致性和幂等需要重点关注

上传流程同时涉及 MinIO 和 PostgreSQL，但两个系统没有本地事务。文件 SHA256 目前是索引而不是唯一键；重复上传、对象存储成功但数据库失败、数据库成功但队列入队失败都需要补偿策略：[services.py](backend/app/documents/services.py:24)、[init.sql](database/init.sql:90)。

### 10.5 领域模型仍偏“员工主表 + 文档证据”

`EmployeeProfile` 主要保存姓名、岗位、职级、部门和工作年限；教育、任职、项目、技能、证书、绩效和面试信息主要依赖文档。接手后要先确认这是有意的“文档优先”模型，还是后续需要拆分的领域模型：[models.py](backend/app/db/models.py:46)。

### 10.6 依赖版本存在漂移风险

前端 `package.json` 多数依赖使用 `latest`，Compose 中 MinIO 也使用 `latest`：[package.json](frontend/package.json:6)、[docker-compose.yml](docker-compose.yml:19)。重新安装或构建可能得到不同版本，建议固定版本后再做联调。

## 11. 验证记录和限制

执行过的检查：

```text
rg --files
git status --short
git diff --stat
uv run pytest tests/test_document_parsers.py tests/test_chunking.py tests/test_chunk_service.py tests/test_models.py tests/test_document_pipeline.py -q
uv run pytest -q --disable-warnings --maxfail=20
uv run pytest tests/test_document_parsers.py -q --disable-warnings --maxfail=1
```

观察结果：

- 后端测试首先被 `backend.app` 导入错误阻断，完整测试至少有 13 个模块无法收集。
- 解析测试在导入问题之外还受到测试样本目录缺失影响。
- 没有启动 Docker Compose，因此 PostgreSQL、Redis、MinIO、Milvus、etcd 和 MinerU 的联调状态未验证。
- 没有运行前端 `npm run build`，前端生产构建状态未验证。
- 没有运行真实模型、真实音频转写、真实向量检索和真实 MCP 外部连接。
- 当前仓库存在大量未提交重构变更，不能把当前工作树直接视为稳定基线；变更状态可通过 `git status --short` 复核。

## 12. 接手顺序建议

### 第一天：建立可运行基线

1. 阅读根目录 [README.md](README.md:1) 和 [backend/README.md](backend/README.md:1)。
2. 统一 `backend/app` 内的导入风格为 `app.*`。
3. 执行 `uv run python -c "from app.main import app; print(app.title)"`。
4. 恢复解析测试 fixture。
5. 启动 PostgreSQL、Redis、MinIO，确认 `/api/health`。
6. 跑通一份 Markdown 或 DOCX 的上传、解析和 Chunk 流程。

### 第三天：理解数据和检索

1. 阅读 [models.py](backend/app/db/models.py:46) 和 [database/init.sql](database/init.sql:9)。
2. 创建一个员工、一个知识库和一份材料。
3. 查看 `documents`、`document_versions`、`parse_jobs`、`parse_artifacts`、`document_chunks` 的记录。
4. 运行 Milvus 索引和证据检索验证脚本。
5. 对照 `query_plan.py`、`hybrid_search_service.py` 和 `evidence_pack.py` 理解检索结果如何变成可信证据。

### 第一周：理解 Agent 主流程

1. 先读第 11 到第 13 课对应的 State、Tool 和 MCP 层。
2. 再读第 14、17 课的任务理解、澄清和恢复逻辑。
3. 最后读第 15、16、18 课的评估分发、报告合成和决策引擎。
4. 用 `langgraph.json` 中的图名逐个启动或运行验证脚本。
5. 给每张图记录输入 State、关键节点、输出 State、外部依赖和失败终态。

## 13. 需要继续确认的问题

1. 当前目录重构的目标基线是什么，哪些删除和新增是有意变更？
2. API 的租户和权限头是否由外部认证网关注入？
3. 实际部署是否允许前端直接访问 MinIO 预签名 URL？
4. 当前数据库是否已经执行 `005`、`007`、`012` 等迁移？
5. PostgreSQL、MinIO、Milvus 和 Redis 的一致性由谁负责重试和补偿？
6. `EmployeeProfile` 是否长期只保存主数据，还是需要增加教育、项目、技能和绩效子表？
7. 前端单文件入口是否计划拆分，还是作为课程演示工作台长期保留？
8. 第 15 到第 18 课的真实模型调用是否有成本、超时、并发和降级要求？

## 总结

接手这个项目时，可以把它理解成四层：

1. **数据层：** 员工、岗位、文档、版本、解析产物、Chunk 和任务状态。
2. **检索层：** Query Plan、SQL 候选筛选、Milvus、BM25、RRF、Rerank 和引用校验。
3. **Agent 层：** LangGraph State、人才工具、MCP、任务理解、动态评估和报告合成。
4. **产品层：** React 工作台、知识库管理、材料详情、召回验收和后续人才评估入口。

最快的理解路线不是从每一个 Agent 文件开始，而是先跑通“上传一份材料 → 生成解析产物 → 生成 Chunk → 建索引 → 召回证据”，再沿着 Query Plan、Talent Tools 和 LangGraph State 进入第 8 到第 18 课的高级流程。
