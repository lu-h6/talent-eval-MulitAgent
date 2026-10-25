# talent-eval-agents 代码级详细拆解

> 复核时间：2026-10-18  
> 复核方式：静态阅读当前工作树（`main` 分支，最新提交为 TalentOS 前端设计/计划文档）  
> 说明：仓库中已有一份 [PROJECT_ANALYSIS_REPORT.md](PROJECT_ANALYSIS_REPORT.md)，偏“第一次接手”的总览；本文按代码路径拆到模块、状态、协议与故障边界，并修正了旧报告里已经过时的两个判断。

---

## 0. 先给结论

这是一个**课程型、可运行、端到端闭环**的“人才评估与推荐”系统，核心不是聊天机器人，而是：

```text
文档入库 → 解析 → 父子切片 → 混合检索 → 可信证据包 → 受控工具 → 多 Agent 评估 → 程序聚合 → 可回引报告
```

它真正的技术资产有三块：

1. **证据可信协议**：Evidence Pack 2.0 不允许模型编造引用，所有 `chunk_id`、`quote`、偏移都要回 PostgreSQL 复核。
2. **确定性与模型的分工**：SQL 筛选、加权聚合、排序、报告校验由程序做；模型只做理解、抽取、评分、写作。
3. **LangGraph 的分层编排**：请求理解（含 HITL）→ 候选人 × 维度并行取证 → 评分/一致性/报告，最后合成一张顶层决策图。

它离“可上线产品”还差三件事：**认证与租户边界、跨存储一致性、可复现的测试/部署基线**。这三件事在课程环境里可以接受，在生产环境是阻断项。

---

## 1. 项目定位与仓库形态

### 1.1 业务问题

面向 HR / 人才评估场景：

- 维护员工（候选人）主数据、岗位 JD、知识库。
- 把简历、面试记录、项目复盘、绩效、证书、晋升材料统一入库。
- 支持 PDF、Office、图片、网页、Markdown、音频的解析。
- 用“结构化硬条件 + 语义要求”找候选人，而不是把自然语言直接丢给向量库。
- 评分和报告只能基于可回引的证据；证据不足、冲突、抽取失败都要显式表达。
- 岗位歧义、条件矛盾、空候选集可以中断、修订、取消、恢复。

### 1.2 课程项目属性

README 以第 1～18 课组织，每一课都是一个**可独立验证的增量**：

| 阶段 | 课程 | 交付物 |
|---|---|---|
| 底座 | 1–2 | 业务背景、FastAPI/worker/前端/中间件分层、模型接入 |
| 数据 | 3–4 | 数据模型、基础解析、MinerU、Redis 异步任务 |
| 检索 | 5–10 | 父子切片、Milvus、混合检索、Query Plan、证据包、引用 |
| Agent | 11–13 | LangGraph State、受控工具、MCP 能力服务 |
| 评估 | 14–18 | 任务理解/HITL、维度分发、评分报告、决策引擎整合 |

这不是“18 个 demo 拼盘”：后面的图直接复用前面的 `QueryPlan`、`TalentToolService`、Evidence Pack 和引用协议。

### 1.3 规模与仓库状态

| 项目 | 数量 |
|---|---|
| 后端 Python 模块（`backend/app`） | 约 40 个（含 2 个一次性补丁脚本） |
| 后端测试文件 | 34 个 |
| 测试函数 | 211 个（参数化后 238 个用例节点） |
| 脚本（`backend/scripts`） | 16 个（其中 11 个 `verify_*`） |
| 数据库表 | 13 张 |
| HTTP 路由 | 22 个业务接口 + `/api/health` |
| LangGraph 注册图 | 4 张 |
| 前端 | `main.tsx` 1355 行 + `EvidencePackPanel.tsx` 95 行 |
| 迁移 | `init.sql` + 3 个增量迁移 |

Git 历史很短：从 GitHub 克隆后只有 2 个本地提交，且都是 `docs: ... TalentOS frontend modules`。也就是说，**当前代码是课程基线，前端四模块改造只停留在设计文档阶段**（见 [设计稿](docs/superpowers/specs/2026-10-14-talentos-frontend-modules-design.md) 与 [实施计划](docs/superpowers/plans/2026-10-14-talentos-frontend-modules.md)）。

> 对旧报告的两处修正：
> 1. 旧报告说 `chunk_service.py`、`database.py`、`talent_tools.py` 仍引用 `backend.app.*`，导致 pytest 无法收集。当前树中 `grep "from backend\."` 已无匹配，这个阻塞已不存在。
> 2. 旧报告说“目录重构中的导入问题”是第一排查任务；当前更现实的阻塞是 **测试样本目录缺失** 与 **部分用例失败**（见第 9 节）。

---

## 2. 全局架构

### 2.1 组件拓扑

```text
┌──────────────────────────── 浏览器 ────────────────────────────┐
│  React 18 + Vite（开发 15173 / 发布 nginx 80→15173）            │
│  员工花名册 · 档案资料库 · 文档抽屉 · 召回测试 · 证据包面板      │
└───────────────┬────────────────────────────────────────────────┘
                │ HTTP + X-Tenant-Id / X-Permission-Scopes
┌───────────────▼────────────────────────────────────────────────┐
│ FastAPI（18080）                                                │
│  api/routes.py        22 个接口                                 │
│  documents/           上传、解析、切片、清理、流水线状态         │
│  retrieval/           Query Plan、混合检索、证据包、引用         │
│  talent/              工具、4 张 LangGraph 图、运行时装配         │
│  integrations/        MinerU、Milvus、S3、模型、Rerank、MCP      │
└───┬───────────┬───────────┬───────────┬───────────┬─────────────┘
    │           │           │           │           │
┌───▼────┐ ┌────▼────┐ ┌────▼────┐ ┌────▼─────┐ ┌───▼──────────┐
│Postgres│ │ Redis   │ │ MinIO   │ │ Milvus   │ │ DashScope     │
│15432   │ │16379    │ │19000    │ │19531     │ │ qwen-plus     │
│事实/状态│ │任务队列 │ │原文件/产物│ │向量+BM25 │ │ embedding/rerank│
└────────┘ └────┬────┘ └─────────┘ └──────────┘ └───────────────┘
                │ blpop
         ┌──────▼──────┐        ┌────────────────┐
         │ worker      │        │ MinerU（18001） │
         │ 解析 + 索引 │───────▶│ PDF/图片版面解析 │
         └─────────────┘        └────────────────┘
```

关键事实：

- **PostgreSQL 是事实源**：文档、版本、解析任务、切片、索引任务状态都在这里。
- **MinIO 只存文件**：原始文件 + `content.md` + `structured.json`。
- **Milvus 只是检索索引**：可以重建，不是事实源。
- **Redis 只是队列**：List + `BLPOP`，没有消费者组、没有 ACK。
- **LangGraph 不是入口**：它在检索和工具之上，负责复杂任务编排。

### 2.2 端口与运行模式

| 服务 | 宿主机端口 | 说明 |
|---|---|---|
| PostgreSQL | 15432 | `talent_docs` / `talent` |
| Redis | 16379 | 两个队列 |
| MinIO API / Console | 19000 / 19001 | 桶 `talent-documents` |
| Milvus gRPC / Health | 19531 / 19091 | collection `talent_evidence_v2` |
| MinerU | 18001 | 独立 uv 项目 / 容器 |
| 后端 API | 18080 | `app.main:app` |
| 前端 | 15173 | Vite / nginx |
| LangGraph Agent Server | 2024 | `langgraph dev` |
| MCP Demo Server | 18081 | `/mcp`，无鉴权 |

开发模式：Compose 只跑中间件，backend/worker/frontend 本机跑，保留热更新。  
发布模式：`--profile app` 把 backend、worker、frontend 一起容器化。

### 2.3 目录职责

```text
backend/app/
├── main.py                 FastAPI 应用工厂、CORS、请求日志、/api/health
├── worker.py               Redis 消费者：解析成功后自动切片 + 入队索引
├── api/routes.py           全部业务接口（794 行）
├── core/config.py          pydantic-settings，按文件位置锚定 .env
├── core/logging_config.py  控制台 + 按日滚动 + error 独立文件
├── db/models.py            13 张表的 ORM
├── documents/              解析器路由、DOCX/PPTX/XLSX/音频、切片、删除
├── retrieval/              Query Plan、Milvus 索引、混合检索、证据包、引用
├── integrations/           MinerU/S3/Milvus/模型/Rerank/MCP
└── talent/                 工具、请求图、分派图、报告图、决策引擎、运行时
```

---

## 3. 数据层

### 3.1 十三张表

| 分组 | 表 | 作用 |
|---|---|---|
| 人才主数据 | `employee_profiles` | 工号、姓名、出生日期、地区、岗位、职级、年限、部门、在职状态 |
| 人才主数据 | `job_descriptions` | 岗位 JD，按租户隔离，供任务理解/岗位匹配 |
| 知识库 | `knowledge_bases` | 资料库，带 `permission_scope` |
| 文档 | `documents` | 逻辑文档，关联员工/知识库/租户/权限 |
| 文档 | `file_objects` | 物理文件：桶、对象键、MIME、大小、SHA256 |
| 文档 | `document_versions` | 版本；`is_current` 上有部分唯一索引 |
| 文档 | `document_metadata` | 扩展 KV（ORM 存在，API 基本未使用） |
| 解析 | `parse_jobs` | 解析任务状态、解析器、进度、错误、重试 |
| 解析 | `parse_artifacts` | 产物索引；内容在 MinIO |
| 切片 | `chunking_runs` | 一次切片运行：策略、参数、器版本、状态 |
| 切片 | `document_chunks` | 证据单元：父子、前后、页码、时间、Markdown 偏移、定位器 |
| 索引 | `evidence_index_jobs` | PostgreSQL ↔ Milvus 的一致性状态 |
| 审计 | `tool_call_audits` | 工具调用摘要，只存参数键、不存参数值 |

### 3.2 关系图

```text
employee_profiles 1 ───── n documents n ───── 1 knowledge_bases
                             │
                             └─ 1 ─ n document_versions 1 ─ n parse_jobs
                                          │                    │
                                          │                    ├─ n parse_artifacts
                                          │                    └─ n chunking_runs
                                          │                              │
                                          │                              └─ n document_chunks
                                          │                                   ├─ parent_chunk_id
                                          │                                   ├─ previous_chunk_id
                                          │                                   └─ next_chunk_id
                                          └─ 1 ─ n evidence_index_jobs
job_descriptions（独立）
tool_call_audits（独立）
```

几个容易忽略的设计：

- `documents.candidate_id` 是**字符串工号**，不是外键；它被冗余到 `document_chunks.candidate_id` 和 Milvus 记录里，用于检索过滤。`employee_id` 才是外键，且可空。
- `document_chunks` 同时存父块和子块；父块 `content` 由子块拼接累积，`position` 与子块共用，`chunk_level` 区分。
- `stable_key = sha256(version_id + strategy + 内容/路径)[:32]`，用于跨次切片的稳定定位；同一 `chunking_run` 内唯一。
- `source_locators` 是 JSON 数组：PDF 存 `page_region + bbox`，PPT 存 `slide`，音频存 `time_range`，Markdown/DOCX 通常为空。

### 3.3 状态机

```text
解析：pending → running → succeeded | failed
                     └──→ partially_succeeded（枚举存在，代码从不写入）

切片：pending → running → succeeded | failed

索引：pending → running → succeeded | failed

流水线汇总（优先级从高到低）：
failed → ready → indexing → chunking → chunked → parsing → parsed → uploaded
```

`partially_succeeded` 是“预留但未使用”的状态；解析器要么整体成功、要么整体失败。

### 3.4 租户与权限模型

- 大多数表都有 `tenant_id`，但**没有 PostgreSQL RLS**。
- API 层只有检索类接口读取 `X-Tenant-Id` / `X-Permission-Scopes`。
- `/api/employees`、`/api/knowledge-bases`、`/api/documents` 这些 CRUD 接口**不读租户头，也不按租户过滤**。
- `upload_document()` 的 `tenant_id` 默认 `"course-demo"`，调用方没有传租户的入口。
- Milvus 过滤表达式强制带 `tenant_id` + `permission_scope IN (...)` + `is_active == true`，这是检索侧最硬的一道边界。

结论：**检索侧有多租户过滤，管理侧没有**。课程演示可以，生产必须补统一认证和租户注入。

---

## 4. 链路 A：材料从上传到可检索

### 4.1 上传

入口：[`POST /api/documents`](backend/app/api/routes.py#L300) → [`upload_document()`](backend/app/documents/services.py#L24)

```text
前端 multipart
  → 读全量 bytes（空文件 400）
  → 校验 employee_id / knowledge_base_id 存在
  → 猜 MIME、算 SHA256
  → 建 Document（material_no = MAT-<uuid 前 8 位>）
  → 写 MinIO（key = tenant/doc_id/v1/filename）
  → 建 FileObject + DocumentVersion(v1, is_current)
  → commit
  → 建 ParseJob(pending) + RPUSH talent:parse:queue
  → 返回 document_id / parse_job_id / pipeline_status=parsing
```

设计点：

- 文件内容先写对象存储，再建 `FileObject`，最后 commit；**没有跨存储事务**。
- `sha256` 只建普通索引，不唯一；重复上传会产生两份文档、两份对象。
- 版本表已建好，但**上传永远只创建 v1**，没有“新增版本”的 API。
- `material_no` 取 UUID 前 8 位十六进制，碰撞概率低但没有冲突重试。

### 4.2 解析

入口：worker 消费 `talent:parse:queue` → [`parse_version()`](backend/app/documents/services.py#L84)

解析器路由在 [`choose_parser()`](backend/app/documents/router.py#L32)：

| 扩展名 | 解析器 | 产物特点 |
|---|---|---|
| `.docx` | `python-docx` 自定义转 Markdown | 标题样式转 `#`，表格转 `|` 文本 |
| `.md` | 直接读 | `content_list` 按空行切块 |
| `.csv/.json/.html` | LangChain Loader | JSON 用 jq `.`，HTML 去标签 |
| `.pptx` | OOXML 解析 | 每页文本 + 坐标/幻灯片定位 |
| `.xlsx` | openpyxl | 每个 sheet 一份结构化数据 |
| `.pdf/.png/.jpg/.jpeg` | MinerU | Markdown + `content_list` + 页码 + bbox |
| `.wav/.mp3/.m4a` | faster-whisper tiny | 纯文本 + `segments` 时间段 |

流程：

```text
ParseJob → running(10%)
  → 从 MinIO 取原文件 → 写临时文件
  → MinerU 或本地解析
  → markdown / structured_json 写 MinIO
  → ParseArtifact 登记
  → succeeded(100%) / failed
```

几个细节：

- PDF/图片如果本地解析失败，会**回退到 MinerU**；但 `parse_document` 对 PDF/图片本来就抛 `ValueError`，所以实际总是走 MinerU。
- MinerU 的 `httpx.post` 超时是 **900 秒**；大文件会长时间占用 worker。
- 解析失败不会抛给 worker，而是把 `ParseJob` 标成 `failed` 后正常返回；worker 再判断 `status == succeeded` 才继续流水线。
- `parser_name` 最终会被解析器真实名称覆盖（`mineru_pipeline_3.4.4`、`python_docx_markdown` 等）。

### 4.3 切片

入口：worker 在解析成功后调用 [`continue_document_pipeline()`](backend/app/documents/document_pipeline.py#L72) → [`create_chunking_run()`](backend/app/documents/chunk_service.py#L179)

```text
读取最新 succeeded 的 ParseJob
  → 读 Markdown + structured_json
  → elements_from_artifacts()
       ├─ 有 content_list：按 Markdown 空行块对齐 content_list，补 page/bbox/时间
       └─ 无 content_list：直接按 segs/文本块生成
  → infer_chunk_strategy()
       ├─ 有 heading → MARKDOWN
       └─ 否则 → RECURSIVE
  → chunk_elements()
  → 每个 child 建 DocumentChunk
  → 按 heading_path 逐级建 parent（同路径复用）
  → parent.content 累积所有子块，parent.markdown_start/end 取 min/max
  → 前后兄弟指针
```

切片算法（[`chunking.py`](backend/app/documents/chunking.py#L155)）：

- **Markdown 策略**：按 `#` 标题维护 `heading_path` 栈；标题不进正文，作为元数据；每个 section 内用 `_pack_elements` 按 `chunk_size` 打包；单个元素超长时用 `RecursiveCharacterTextSplitter` 二次切，并回填 `markdown_start/end`。
- **递归策略**：所有元素顺序打包，不建标题父子关系；适用于纯文本/转写。
- 分隔符优先中文标点：`\n\n`、`\n`、`。`、`！`、`？`、`；`、`，`、空格。
- 默认 `chunk_size=800`、`overlap=100`；前端只允许改这两个值，策略由后端判定。

父子块不是“检索父块、返回父块”那种 small-to-big 实现；当前检索只索引 child，父块在引用展开时作为**同版本上下文**附加（≤8000 字符）。

### 4.4 索引

入口：切片完成后 `RPUSH talent:index:queue` → worker → [`run_index_job()`](backend/app/retrieval/evidence_index_service.py#L94)

```text
job → running
  → 找该版本最新 succeeded 的 chunking_run
  → 取全部 child
  → embed_documents([content...])（DashScope text-embedding-v3，1024 维）
  → 组装 EvidenceRecord
  → ensure_collection()
  → delete_version(tenant, version_id)   ← 先删旧向量
  → upsert(records)                      ← 再写新向量
  → succeeded / failed
```

Milvus schema（[`milvus_store.py`](backend/app/integrations/milvus_store.py#L151)）：

- 主键 `chunk_id`（VARCHAR 36）。
- 标量：`candidate_id`、`document_version_id`、`tenant_id`、`document_type`、`permission_scope`、`embedding_model`、`content_hash`、`content`、`parent_chunk_id`、`page_start/end`、`is_active`。
- 稠密向量：`embedding`，HNSW + COSINE，`M=16`、`efConstruction=128`。
- 稀疏向量：`content_sparse`，`SPARSE_INVERTED_INDEX` + BM25，由 Milvus 原生 Function 从 `content` 生成；`content` 开启 `chinese` 分析器。
- 一致性：默认 `Bounded`。

风险点：**先删后写**。如果删除成功、embedding 或 upsert 失败，该版本在 Milvus 中会暂时为空，直到重试成功。

### 4.5 流水线状态与删除

- [`summarize_pipeline_status()`](backend/app/documents/document_pipeline.py#L106) 用“解析/切片/索引”三个最新状态推导总状态，供列表和详情展示。
- 删除入口：[`DELETE /api/documents`](backend/app/api/routes.py#L459) → [`delete_document_bundle()`](backend/app/documents/document_cleanup.py#L21)
  - 先删 Milvus 各版本向量
  - 再删 MinIO 原文件 + 产物
  - 再按子表→父表删 PostgreSQL 记录
  - 最后删文档主记录
- 同样是跨存储删除，**没有补偿事务**；对象已删、数据库失败会留下“记录在、文件没了”的孤儿。

---

## 5. 链路 B：检索与可信证据

### 5.1 Query Plan：自然语言 → 可执行计划

核心在 [`query_plan.py`](backend/app/retrieval/query_plan.py#L68)。

```python
QueryPlan(
    task_type: find_talent | lookup_profile,
    filters: [FilterCondition(field, operator, value)],
    semantic_requirements: [SemanticRequirement(requirement_id="S1", query, required)],
    preferences: [str],
    clarifications: [Clarification(expression, reason)],
)
```

- 硬条件字段白名单只有 7 个：`age`、`region`、`department`、`job_level`、`years_of_experience`、`current_position`、`employment_status`。
- 操作符：`eq/in/lt/lte/gt/gte`；范围运算符只允许数值字段。
- `age` 不能直接比 `birth_date`，编译时把“年龄 < X”反转成“出生日期 > X 年前的边界”，并处理 2/29。
- 模型只输出 `QueryPlan`，**不生成 SQL**；租户和权限不在模型可见字段里。
- 有 `clarifications` 时 `executable == False`，接口返回 422，要求用户澄清。

提示词在 [`QUERY_PLAN_SYSTEM_PROMPT`](backend/app/retrieval/query_plan.py#L293)，明确要求：不补充用户未提供的阈值、不把“优先”改写成硬条件、不提取权限/租户/密级。

### 5.2 SQL 候选筛选

[`build_candidate_statement()`](backend/app/retrieval/query_plan.py#L144)：

```sql
WHERE tenant_id = :tenant
  AND employment_status = 'active'
  AND <每个 filter 的编译表达式>
ORDER BY employee_no
```

- 强制租户隔离 + 只在职。
- 每个字段的编译器来自 `FILTER_FIELD_REGISTRY`；未知字段/非法操作符直接 `ValueError`。
- 空候选集时直接返回，不调用 Milvus，避免空列表退化成全库搜索。

### 5.3 混合检索

[`hybrid_search_evidence()`](backend/app/retrieval/hybrid_search_service.py#L63)：

```text
query → embed_query → 1024 维
  → Milvus hybrid_search:
       dense: embedding / COSINE / ef
       sparse: content_sparse / BM25
       两路各取 limit*2
       RRFRanker(k=rrf_k)
  → 取 rerank_top_n 条
  → DashScope gte-rerank-v2 精排
  → 截断 limit
```

- RRF 由 Milvus 原生 `RRFRanker` 做，不是手写融合。
- `limit` 是最终返回数；`rerank_top_n` 是进入精排的候选数，默认 20。
- 过滤表达式 [`build_filter_expression()`](backend/app/integrations/milvus_store.py#L78) 强制 `tenant_id`、`permission_scope IN`、`is_active`，可选 `candidate_ids`、`document_types`、`document_version_id`。

### 5.4 Evidence Pack 2.0：把“模型判断”变成“可复核材料”

核心在 [`evidence_pack.py`](backend/app/retrieval/evidence_pack.py#L216)。

协议：

```json
{
  "schema_version": "2.0",
  "candidate_id": "C001",
  "requirements": [
    {
      "requirement_id": "S1",
      "query": "企业知识库建设经验",
      "status": "sufficient | partial | missing | conflicting",
      "reason": "...",
      "extraction_status": "succeeded | failed | not_run",
      "facts": [
        {
          "event": "星河项目",
          "period": "2025-01/2025-06",
          "claim": "担任项目总负责人",
          "answer": "yes",
          "sources": [
            {"citation_id": "...", "chunk_id": "...", "quote": "...", "quote_start": 12, "quote_end": 35}
          ]
        }
      ],
      "conflicts": [[0, 1]],
      "missing_information": ["..."],
      "citations": [...]
    }
  ]
}
```

关键机制：

1. **短别名**：给模型看 `source_1`、`source_2`，回来再还原真实 `chunk_id`，避免模型截断 UUID。
2. **来源校验**：`chunk_id` 必须来自输入；`quote` 必须是对应 Chunk 的连续原文；偏移由后端 `find()` 计算，**不接受模型生成的位置**。
3. **去重合并**：按 `event + period + claim + answer` 合并重复事实，保留全部已验证来源。
4. **冲突推导**：同一 `event + period + claim` 下出现 yes/no，标记 `conflicting`，不自动裁决。
5. **状态判定**：冲突优先；有 yes 且 fully_supported 且无缺失 → `sufficient`；有事实 → `partial`；无事实 → `missing`。
6. **失败降级**：模型/校验失败时 `status=partial`、`reason=extraction_failed`，保留原始 citations，不返回未核验事实。

`period` 有严格正则：`unknown`、`YYYY`、`YYYY-MM/YYYY-MM`；不规范的 period 会触发 schema 校验失败，而不是进入冲突比较。

### 5.5 引用回表

[`resolve_citation()`](backend/app/retrieval/evidence_citations.py#L7)：

```text
chunk_id → DocumentChunk → DocumentVersion → Document → KnowledgeBase（可选）
  → 校验：文档 active、租户匹配、文档权限命中、Chunk 权限命中、candidate_id 一致
  → 校验知识库 active / 租户 / 权限
  → 校验 quote 坐标成对且 0 <= start < end <= len(content)
  → 返回原文、heading_path、markdown 偏移、页码/时间、版本状态
```

- 历史版本仍可访问时返回 `version_state=historical`；权限撤销/材料不可用统一 404，避免泄露存在性。
- `load_pack_sources()` 从 PostgreSQL 重读命中文本，**不使用 Milvus 里的 content**；同版本父块 ≤8000 字符时附加为上下文。
- 这一步是整个“可信证据”链路的最后一道闸门。

### 5.6 自动优化（有实现、有测试、但入口不通）

`choose_optimization_strategy()` 支持三种策略：

| 条件 | 策略 |
|---|---|
| 首轮有命中 | 不优化 |
| 无命中且要求数 > 1 | `decompose` |
| 无命中且表达含糊 | `rewrite` |
| 其他无命中 | `multi_query` |

但 [`search_talent()`](backend/app/api/routes.py#L616) 调用时只传了 `requirement`、`search_query`、`model`，没有传 `requirement_count` 和 `expression_is_vague`，所以：

```python
requirement_count=1, expression_is_vague=False  # 默认值
```

**在 `/api/talent-search` 中，`decompose` 和 `rewrite` 永远不会被选中，只会走 `multi_query`。** 单测 `test_query_optimization_is_triggered_by_failure_type` 只测了纯函数，没有覆盖这个接线缺口。这是一个明确的 P1 级逻辑缺陷。

---

## 6. Agent 编排

### 6.1 四张图的总览

`langgraph.json` 注册：

| 图 | 入口 | 作用 |
|---|---|---|
| `talent_request` | `talent_request_graph.py:graph` | 任务理解、岗位确认、Query Plan、候选人筛选、HITL |
| `talent_evaluation_dispatch` | `talent_evaluation_runtime.py:graph` | 生成维度、候选人 × 维度并行取证 |
| `talent_evaluation_report` | `talent_evaluation_runtime.py:report_graph` | 评分、一致性、聚合、排名、报告 |
| `talent_decision_engine` | `talent_decision_runtime.py:graph` | 串联前三张子图 |

```text
request_subgraph                 evaluation_subgraph                 report_subgraph
┌──────────────────┐   ready   ┌──────────────────────┐  branches  ┌────────────────────┐
│ receive          │──────────▶│ receive              │───────────▶│ receive            │
│ interpret        │           │ generate dimensions  │            │ score dimensions   │
│ lookup/confirm   │           │ validate weights     │            │ consistency review │
│ build plan       │           │ retrieve candidates  │            │ aggregate + rank   │
│ filter candidates│           │ prepare work items   │            │ compose report     │
│ resolve (HITL)   │           │ Send → branches      │            │ validate + render  │
└──────────────────┘           │ finalize             │            └────────────────────┘
```

### 6.2 `talent_request`：任务理解与可恢复澄清

文件：[`talent_request_graph.py`](backend/app/talent/talent_request_graph.py#L640)，762 行。

State 关键字段：

```text
request_text, input_mode, draft, job_matches, selected_job,
talent_request, query_plan, candidate_ids,
condition_revisions, plan_version, clarifications, status, errors
```

两条输入路径：

```text
detailed_requirement → interpret_input → build_plan
job_name             → interpret_input → lookup_jobs
                         ├─ 唯一 exact → select_exact_job → compile_selected_job → build_plan
                         ├─ 多个/模糊 → confirm_job（interrupt）→ compile_selected_job → build_plan
                         └─ 无匹配 → no_job_match → END
```

候选人筛选与 HITL（只在传入 `candidate_filter` 时挂载）：

```text
build_plan
  ├─ plan_ready → filter_candidates
  │                 ├─ 有候选 → END(status=candidates_ready)
  │                 └─ 空集 → resolve_requirements（interrupt）
  └─ clarification_required → resolve_requirements（interrupt）
```

`resolve_requirements` 的恢复协议：

```json
{"interaction_id": "rr-v0", "action": "revise", "revision_text": "新的完整要求"}
{"interaction_id": "rr-v0", "action": "cancel"}
```

- 载荷里带完整 `request_text`，前端用它初始化编辑框。
- 恢复时校验 `interaction_id`，过期/不匹配会**带着 `resume_error` 重新 interrupt**，而不是让节点崩溃。
- 修订后 `plan_version += 1`，写 `condition_revisions`，覆盖 `request_text`，回到 `interpret_input` 重新编译。
- 空候选时还会做 leave-one-out 诊断：逐个移除硬条件，返回 `candidates_without_condition`，帮助用户判断哪个条件导致空集。

**代码里的两个“孤儿节点”**：

- [`_resolve_conditions()`](backend/app/talent/talent_request_graph.py#L492)：针对 `clarification_required` 的专用修订节点。
- [`_resolve_empty_candidates()`](backend/app/talent/talent_request_graph.py#L579)：针对空候选集的放宽节点。

它们在 `build_talent_request_graph()` 中**没有被 `add_node`，也没有被路由引用**。当前实际使用的是统一的 `resolve_requirements`（README 第 17 课也这么描述）。这是重构后留下的死代码，测试也没有直接引用。

### 6.3 `talent_evaluation_dispatch`：候选人 × 维度并行取证

文件：[`talent_evaluation_dispatch.py`](backend/app/talent/talent_evaluation_dispatch.py#L838)，884 行。

流程：

```text
receive_input（校验 tenant/permission/talent_request/query_plan）
  → generate_dimensions（模型输出 EvaluationDimensionPlan）
  → validate_dimensions（权重合计必须 = 100）
  → retrieve_candidates（若主图已传 candidate_ids 则跳过）
  → prepare_work_items（候选人 × 维度笛卡尔积；> max_work_items 直接失败）
  → Send("run_assessment_branch", ...) × N
  → finalize（branches_ready / branches_ready_with_failures）
```

维度模型：

```text
EvaluationDimension(
  name, definition, weight_percent, evidence_requirements[],
  score_anchors (0/3/5), retrieval_hints[], source
)
```

- 模型不生成 `dimension_number`；程序按列表顺序编号 `1..N`，避免重复。
- `max_work_items=24`（运行时装配），超过直接 `capacity_exceeded`，不静默截断。
- 每个分支内部，按 `evidence_requirements` **再并发**：`ThreadPoolExecutor(max_workers=len(requirements))`，Future 按提交顺序聚合，所以完成顺序不影响输出顺序。
- 每个 requirement 调一次 `search_candidate_evidence`，工具超时单独放宽到 **90 秒**（`ToolPolicy(max_attempts=1, timeout_seconds=90)`），因为真实模型抽取可能 60 秒级。
- 工具返回 `Evidence Pack 2.0`；分支把 `execution_status`、`status`、`extraction_status` 分开保存，避免“抽取失败”被误写成“分支失败”。

失败隔离：

- 工具返回 `ok=False` → 该 requirement 记 `missing`，reason 用错误码，分支 `degraded`。
- 证据包缺失 → 该 requirement 记 `missing`，reason `evidence_pack_missing`。
- 分支抛 `TimeoutError` / `BranchExecutionError` → `run_assessment_branch` 生成 `failed` 结果，不拖垮其他分支。
- 未知异常仍然向上抛（会让该分支失败，可能影响整图），这是有意的“不吞异常”。

### 6.4 `talent_evaluation_report`：评分、聚合与报告

文件：[`talent_evaluation_report.py`](backend/app/talent/talent_evaluation_report.py#L1172)，1211 行。

流程：

```text
receive_input
  → 从 branch_results 的事实来源构建 evidence_items
  → 分配 E1..En 短编号
  → 校验任务矩阵完整（候选人 × 维度不能缺项）
  → score_dimensions（最多 12 并发）
  → review_consistency（每个候选人一次，最多 12 并发）
  → aggregate_candidates（程序聚合 + 排名）
  → compose_report（模型写报告草稿）
  → validate_report（白名单校验 + 渲染 Markdown）
```

短编号协议：

- 模型只看到 `E1`、`E2`，看不到 UUID 和 URL。
- 模型返回后，程序先把短编号还原成 `chunk_id`，再校验：
  - 是否在证据白名单内；
  - 是否属于该候选人；
  - 报告引用的证据是否在该候选人的已评分证据集合内。
- 报告正文显示短编号，链接用完整 `chunk_id + quote_start/end` 渲染。

评分规则：

- `sufficient/partial` 必须有分数；`missing/conflicting` 分数必须为 `null`。
- `missing` 必须说明缺失信息；`conflicting` 必须说明冲突。
- `sufficient/partial/conflicting` 必须保留证据引用。
- 未评分维度不计 0 分；聚合时只把“已评分维度”的权重计入覆盖率。

聚合公式：

```text
confirmed_score = Σ (weight_i × score_i / 5)        # 只算有分数的维度
possible_score  = confirmed_score + Σ weight_j       # 未评分维度按满分潜力计入
coverage        = Σ weight_i                         # 有分数的维度权重和
```

置信度：

```text
有跨维度一致性问题 → low
所有维度都 sufficient → high
coverage >= 70 且无 conflicting/failed → medium
其他 → low
```

排名：`confirmed_score` 降序 → `coverage` 降序 → `candidate_id` 升序。**模型不参与排名**。

报告校验（[`validate_report_draft()`](backend/app/talent/talent_evaluation_report.py#L1064)）：

- 报告里的候选人顺序必须与程序排名一致；
- 每条 highlight 引用的证据必须已核验、且属于该候选人；
- 不满足就抛错，不渲染。

### 6.5 `talent_decision_engine`：顶层编排

文件：[`talent_decision_engine.py`](backend/app/talent/talent_decision_engine.py#L76)，106 行。

```text
START → request_subgraph
          ├─ status == candidates_ready → evaluation_subgraph
          └─ 其他 → END（取消/无候选/澄清未完成）
       → evaluation_subgraph
          ├─ branches_ready / branches_ready_with_failures → report_subgraph
          └─ 其他 → END
       → report_subgraph → END
```

- 顶层 State 是三张子图 State 的并集；输出 schema 只保留 `status/candidate_ids/ranking/report/errors`。
- `DecisionContext(tenant_id, permission_scopes)` 由 Agent Server 注入，不进入模型输入。
- 子图默认使用各自的一次性持久化，运行时继承父图 checkpointer；因此 `talent_request` 里的 `interrupt()` 可以在主图中暂停/恢复。
- 分派图用 `candidates_preselected` 区分“主图已传候选人”和“独立调用需要自己查”，避免重复 SQL。

### 6.6 工具层：受控能力，而不是裸 SQL

文件：[`talent_tools.py`](backend/app/talent/talent_tools.py#L261)，482 行。

四个只读工具：

| 工具 | 作用 | 边界 |
|---|---|---|
| `lookup_job_descriptions` | 按名称查 JD | 租户 + active；exact/contains/fuzzy |
| `filter_candidates` | 结构化筛选 | 复用 Query Plan SQL Builder |
| `search_candidate_evidence` | 候选人范围内混合检索 + 证据包 | 必须传 candidate_ids；协议版本 2.0 |
| `get_candidate_profiles` | 批量读基础档案 | 租户 + active |

执行器 [`ToolExecutor`](backend/app/talent/talent_tools.py#L106)：

- 默认 `max_attempts=2`、`timeout_seconds=3`、指数退避。
- 只对 `TransientToolError` / `FutureTimeoutError` 重试；`ValueError`/`PermissionError` 立即失败。
- 每个工具可单独覆盖策略（证据检索 90 秒）。
- 连续失败达到阈值打开熔断，恢复期后半开放行。
- 审计只写 `argument_keys`，不写参数值；`SqlAuditSink` 落 `tool_call_audits`。
- 超时通过“单独线程 + `future.result(timeout)`”实现；超时后 `shutdown(wait=False)`，**底层调用可能仍在跑**，这是 README 明确记录过的取舍。

上下文 `TalentToolContext` 只从可信来源构造（LangGraph runtime / MCP token），模型参数里没有 `tenant_id`。

### 6.7 MCP：把能力发布出去

文件：[`talent_mcp_server.py`](backend/app/integrations/talent_mcp_server.py#L64)。

- 4 个 Tool（与上面同名）。
- 2 个 Resource Template：`talent://jobs/{job_code}`、`talent://candidates/{candidate_id}/profile`。
- 1 个固定 Resource：`talent://policies/evaluation/current`。
- 1 个 Prompt：`talent_assessment`。
- `AuthSettings` + 可注入 `TokenVerifier`；token claims 里的 `tenant_id`、`permission_scopes` 映射为 `TalentToolContext`；`clientInfo` 和工具参数不参与身份判定。
- 本机 Demo 入口 [`run_talent_mcp_http.py`](backend/scripts/run_talent_mcp_http.py) 使用临时 SQLite、固定 `tenant-a`、`stateless_http=True`、无鉴权，仅用于 Inspector 联调。

MCP Client 侧 [`build_mcp_tool_graph()`](backend/app/integrations/talent_mcp_client.py#L11) 把发现的工具塞进 LangGraph `ToolNode`，是一个最小可执行图。

---

## 7. 前端

### 7.1 现状

[`frontend/src/main.tsx`](frontend/src/main.tsx#L527) 是一个 1355 行的单文件应用：

- 左侧深绿侧边栏 + 顶部面包屑。
- 两个页面：`员工花名册`、`档案资料库`。
- 花名册：搜索、分页（每页 10）、新建员工、关联材料数。
- 资料库：知识库切换/新建/删除、材料列表、批量删除、上传。
- 文档抽屉：原始文件、解析结果、切片预览、文件元数据。
- 切片面板：生成切片、创建索引、查看父子/兄弟/页码/时间/偏移。
- 召回测试台：纯向量检索 + 权限/候选人/材料类型过滤。
- 证据包面板：`/talent-search` + Evidence Pack + 引用抽屉。

API 常量写死：

```ts
const API = "http://127.0.0.1:18080/api";
const TENANT_ID = "course-demo";
```

- 只有检索类请求带 `X-Tenant-Id` / `X-Permission-Scopes`。
- 上传、员工、知识库、文档 CRUD 不带租户头。
- 分页完全是前端切片，后端 `GET /documents` 返回全部。
- 没有 router、没有状态管理、没有请求缓存；页面切换靠 `useState`。

### 7.2 与设计文档的差距

[设计稿](docs/superpowers/specs/2026-10-14-talentos-frontend-modules-design.md) 计划拆成四个模块：

```text
#dashboard   #profiles   #recommendation   #evaluation
```

并引入：

- hash 导航 + 浏览器前进/后退；
- 共享 API client（统一错误、租户头、权限头）；
- Dashboard 指标（员工/KB/文档/就绪/处理中/失败）；
- Recommendation 页（`/talent-search` 的完整可视化）；
- Evaluation Center（纯向量 vs 混合检索并排对比 + 耗时）；
- Vitest + React Testing Library。

[实施计划](docs/superpowers/plans/2026-10-14-talentos-frontend-modules.md) 有 621 行、按任务拆到文件级，但复选框全部未执行。当前前端仍是两模块工作台，没有 Dashboard / Recommendation / Evaluation Center。

### 7.3 前端细节问题

- `package.json` 所有依赖都是 `"latest"`；虽然有 `package-lock.json`，但 `npm install` 与 `npm ci` 行为不同，跨时间构建不可复现。
- `permission_scope` 选项是 `hr_private / department / internal`，设计与 README 里又出现 `company_internal`；命名不统一。
- 生产镜像 nginx 只做静态托管，没有 `/api` 反向代理；前端仍请求 `127.0.0.1:18080`，所以“发布模式”实际仍要求浏览器所在机器能直连后端端口。
- 没有错误边界、没有加载骨架、没有可访问性测试。

---

## 8. 工程设计亮点

1. **“模型只提假设，程序负责验证”**：Evidence Pack 的 `chunk_id` + 连续 `quote` + 后端计算偏移，是整条链路最值得复用的设计。
2. **确定性 SQL Builder**：模型只填白名单字段和操作符，SQL 由 `FILTER_FIELD_REGISTRY` 生成；年龄这种“反向边界”也在程序里处理。
3. **短编号协议**：`source_1`、`E1` 这类短 ID 降低了模型复制 UUID 的失败率；还原后再做白名单/归属校验。
4. **程序聚合、模型写作**：排名、覆盖率、置信度、报告顺序全部由程序定；模型只写叙述。
5. **失败可表达**：`missing / partial / conflicting / extraction_failed / branch_timeout` 分开建模，不把“没证据”伪装成 0 分。
6. **审计不存内容**：`tool_call_audits` 只存参数键和摘要，避免把履历/查询原文写进审计表。
7. **幂等迁移**：`012` 等迁移使用 `IF NOT EXISTS`，可重复执行；`init.sql` 是全新环境的最新结构。
8. **课程式验证脚本**：16 个脚本（其中 11 个 `verify_*`）覆盖图、HITL、工具、MCP、Milvus、混合检索，比单纯单测更接近真实链路。

---

## 9. 测试、验证与当前基线

### 9.1 覆盖面

34 个测试文件、211 个测试函数，集中在：

| 域 | 代表文件 | 覆盖点 |
|---|---|---|
| 解析/切片 | `test_document_parsers.py`、`test_chunking.py`、`test_chunk_service.py`、`test_chunk_pipeline.py` | 格式路由、标题层级、偏移、父子、时间定位 |
| 检索 | `test_query_plan.py`、`test_hybrid_search_service.py`、`test_milvus_store.py` | 字段白名单、SQL 参数化、RRF、BM25、过滤 |
| 证据 | `test_evidence_pack.py`、`test_evidence_citations.py` | 伪造引用、冲突、权限、历史版本 |
| 工具/MCP | `test_talent_tools.py`、`test_talent_mcp_server.py`、`test_talent_mcp_client.py` | 租户边界、重试、熔断、token claims |
| 图 | `test_talent_request_graph.py`、`test_talent_request_hitl.py`、`test_talent_evaluation_dispatch.py`、`test_talent_evaluation_report.py`、`test_talent_decision_engine.py` | 路由、interrupt/resume、Send 并发、矩阵完整性、聚合 |
| API | `test_api.py`、`test_evidence_api.py`、`test_talent_search_api.py` | OpenAPI、过滤头、优化检索、证据包开关 |

另有一个 `live_model` 标记的真实模型测试，默认跳过；设置 `RUN_LIVE_MODEL_TESTS=1` 才会把测试证据发到外部模型。

### 9.2 当前可见的失败

`.pytest_cache/v/cache/lastfailed` 记录最近一次运行有 **12 个失败**：

- `test_document_parsers.py`：8 个（DOCX/CSV/JSON/HTML/Markdown/PPTX/XLSX/音频）。
- `test_chunk_pipeline.py`：3 个（真实 DOCX、DOCX/PPTX 办公文档）。
- `test_talent_tools.py::test_timeout_policy_changes_slow_operation_result`：1 个（时序敏感）。

前 11 个的共同原因是测试样本目录不存在：

```text
backend/tests/../../sample-data/generated
```

即仓库根目录的 `sample-data/`。`.gitignore` 把 `sample-data/` 整个忽略了，所以全新克隆一定跑不了这些用例。

第 12 个是超时策略测试，依赖真实线程调度，容易受机器负载影响；它失败不代表工具逻辑一定错，但说明这套测试对时序敏感。

README 里记录的“230 passed, 1 skipped”是作者本机、样本齐全时的结果；**当前工作树不具备同等基线**。

### 9.3 没有的东西

- 没有 `.github/workflows`，没有 CI。
- 没有 ruff / mypy / eslint / prettier 配置。
- 没有前端测试（Vitest 只在设计文档里）。
- 没有 `conftest.py` 统一 fixture；测试样本路径靠相对路径硬编码。
- 没有覆盖率门槛。

### 9.4 本次复核的边界

- 静态阅读，没有启动 Compose、PostgreSQL、Milvus、MinIO、MinerU。
- 没有执行 pytest / npm build：当前会话的 Windows 沙箱在给工作区授权时报 `SetNamedSecurityInfoW failed`，所有 `pwsh` 命令在启动阶段就被挡住。因此测试结论来自 `.pytest_cache` 与 README，不是本次实跑。
- 因此本文对“行为”的描述以代码路径为准；对“跑通”的判断请以你本地实际执行为准。

---

## 10. 风险清单

### P0：生产化阻断项

| # | 问题 | 证据 | 影响 | 建议 |
|---|---|---|---|---|
| 1 | `.env.example` 里有一个 `sk-` 开头、形似真实的 DashScope Key | [`.env.example`](.env.example#L8) | 密钥已进入版本库；公开仓库等于泄漏 | 立刻轮换 Key；把示例改成占位符；如已推送，清理历史 |
| 2 | 没有认证/授权中间件，直接信任 `X-Tenant-Id` / `X-Permission-Scopes` | [`main.py`](backend/app/main.py#L18) 只有 CORS + 日志 | 任意客户端可伪造租户/权限，读全量员工、文档、原文 | 接 JWT/OIDC 或网关注入；服务端只信签名上下文 |
| 3 | 管理类接口完全不按租户过滤 | [`/api/employees`](backend/app/api/routes.py#L216)、[`/api/documents`](backend/app/api/routes.py#L270) | 跨租户读取员工、知识库、文档；与检索侧的多租户承诺不一致 | 所有查询强制 `tenant_id`；上传从可信上下文取租户 |
| 4 | 文档详情/预签名 URL 无权限校验 | [`GET /api/documents/{id}`](backend/app/api/routes.py#L330) | 知道 UUID 就能拿原文件 15 分钟下载链接和 Markdown 全文 | 与引用接口一样做租户/权限/知识库校验 |

### P1：上线前必须处理

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 5 | 上传/解析/索引/删除跨 PostgreSQL、MinIO、Milvus，无事务/补偿 | `upload_document`、`run_index_job`、`delete_document_bundle` | 孤儿对象、孤儿记录、索引空窗 |
| 6 | Redis List 队列无 ACK/重试/死信；单 worker 串行 | [`worker.py`](backend/app/worker.py#L19) | worker 崩溃丢任务；一个 15 分钟的 MinerU 解析会阻塞后面的索引任务 |
| 7 | 索引“先删后写” | [`run_index_job`](backend/app/retrieval/evidence_index_service.py#L94) | 删除成功、embedding 失败 → 该版本暂时不可检索 |
| 8 | 自动优化策略参数没传，`decompose`/`rewrite` 不可达 | [`routes.py:682`](backend/app/api/routes.py#L682) | 首轮无命中时只会 multi_query，优化能力打折 |
| 9 | 测试样本缺失 + 12 个 lastfailed，README 基线不可复现 | `sample-data/` 不存在；`.pytest_cache` | 新人无法判断“改坏了没有” |
| 10 | 部署形态不闭环：前端硬编码 `127.0.0.1:18080`，nginx 无 `/api` 代理 | [`main.tsx:23`](frontend/src/main.tsx#L23)、[`nginx.conf`](frontend/nginx.conf#L1) | 发布模式只能本机访问；换机器/域名就断 |
| 11 | 缺 Key 时 `talent_evaluation_runtime` 导入即抛错 | [`build_runtime_graph()`](backend/app/talent/talent_evaluation_runtime.py#L31) | `langgraph dev` 在无模型 Key 环境直接起不来；四个图一起加载 |
| 12 | 上传全量读入内存，无大小/类型白名单 | [`create_document`](backend/app/api/routes.py#L300) | 大 PDF 可能 OOM；任意扩展名进入解析器路由后报错 |

### P2：工程质量与一致性问题

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 13 | 两个死代码节点 | `_resolve_conditions`、`_resolve_empty_candidates` | 误导读代码的人；README 描述与图结构不一致 |
| 14 | 一次性补丁脚本留在源码包 | [`_patch_tail.py`](backend/app/talent/_patch_tail.py#L1)、[`_patch_report.py`](backend/app/talent/_patch_report.py#L1) | 含硬编码 `E:\...` 路径；误执行会改源码 |
| 15 | `/documents` N+1 查询 + 每请求 `ObjectStore()` 做 `head_bucket` | `list_documents` 循环 `build_document_pipeline_payload` | 文档多时接口明显变慢 |
| 16 | `partially_succeeded` 永不写入；`document_metadata` 无 API | 枚举/ORM 存在 | 状态模型有“死枚举”，扩展元数据是空壳 |
| 17 | 引用 `quote` 用 `find()` 只取首次出现 | `_validate_fact_sources` | 同一句话在 Chunk 中出现多次时，偏移可能定位到第一处 |
| 18 | 多路结果合并按“首次出现”而非分数 | `merge_query_results` | 不同 requirement 的排序不可比，合并结果可能不是全局最优 |
| 19 | 前端 1355 行单文件；四模块计划未实施 | `main.tsx` | 维护成本高；设计与代码脱节 |
| 20 | 依赖 `latest`（前端、MinIO 镜像） | `package.json`、`docker-compose.yml` | 构建漂移；MinIO 行为变化不可控 |
| 21 | `model_provider.py` 末尾保留 `__main__` 调试入口 | [`model_provider.py`](backend/app/integrations/model_provider.py#L41) | 生产模块里混入调试代码 |
| 22 | `pyproject` 未显式声明 `langchain-text-splitters` | `chunking.py` 直接 import | 依赖传递，升级 LangChain 时可能断 |

### 需要确认的产品/架构问题

1. `EmployeeProfile` 是否长期只做“主数据 + 文档证据”，还是要拆教育/任职/项目/技能子表？
2. `company_internal` 与 `internal` 到底哪个是正式权限码？当前前后端/文档不一致。
3. 版本能力是否要真正开放（上传新版本、历史版本检索、引用历史版本）？现在只建 v1。
4. 评估报告是否要进入前端主流程（Recommendation 页），还是继续只做 LangGraph/脚本验证？
5. 真实模型调用的成本/超时/并发上限由谁负责（当前是 12 路评分、24 个工作项、90 秒证据工具）？

---

## 11. 运行手册（按当前代码校正）

### 11.1 准备

```bash
cp .env.example .env
# 把 DASHSCOPE_API_KEY 换成你自己的，不要用示例里的值
```

README 没有写 `cp .env` 这一步，但 `docker compose --profile app` 的 `env_file: .env` 是硬依赖。

### 11.2 中间件

```bash
docker compose -p talent-eval-agents-course up -d
```

只启动 PostgreSQL、Redis、MinIO、etcd、Milvus。

### 11.3 MinerU

```bash
uv sync --project backend/mineru-runtime
uv run --project backend/mineru-runtime mineru-api --host 127.0.0.1 --port 18001
```

或使用 `docker-compose-mineru.yml` 的 `mineru` 服务。

### 11.4 后端 + worker + 前端

```bash
# 后端（仓库根目录）
uv sync --project backend --dev
uv run --project backend uvicorn app.main:app --app-dir backend --reload --reload-dir backend --host 0.0.0.0 --port 18080

# worker（必须能 import app）
cd backend
uv run python -m app.worker

# 前端
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:15173`。

### 11.5 LangGraph

```bash
cd backend
uv run langgraph dev --no-browser --no-reload --port 2024
```

前提：`.env` 里有可用的 `DASHSCOPE_API_KEY`，因为 `talent_evaluation_runtime` 在导入时就会装配模型、Embedding、Rerank 和 Milvus。

### 11.6 验证

```bash
cd backend
uv run pytest tests -q

# 样本齐全时
uv run --no-sync pytest -q tests/test_query_plan.py tests/test_evidence_pack.py tests/test_evidence_citations.py
```

图级验证：

```bash
uv run --no-sync python -m scripts.verify_talent_request_graph
uv run --no-sync python -m scripts.verify_talent_hitl_resume
uv run --no-sync python -m scripts.verify_talent_evaluation_dispatch --log-level INFO
uv run --no-sync python -m scripts.verify_talent_evaluation_report --input samples/lesson16/talent_evaluation_dispatch_result_20260921_205347.json
uv run --no-sync python -m scripts.verify_talent_decision_events
```

---

## 12. 建议的接手顺序

### 第 1 天：建立可运行基线

1. 修 `.env`（轮换示例 Key），确认 PG/Redis/MinIO/Milvus 健康。
2. 跑 `uv run pytest tests -q`，记录失败；补齐 `sample-data/` 或给测试加 `skipif`。
3. 跑 `GET /api/health`，再上传一份 Markdown，确认 `parse → chunk → index` 三状态到 `ready`。
4. 用召回测试台查一次，确认 Milvus `talent_evidence_v2` 有数据。

### 第 2–3 天：数据与检索

1. 读 `models.py`、`init.sql`、`005/007/012` 迁移，画一遍 ER 图。
2. 手动查 `documents / document_versions / parse_jobs / parse_artifacts / document_chunks / evidence_index_jobs`。
3. 对照 `query_plan.py → hybrid_search_service.py → evidence_pack.py → evidence_citations.py` 走一遍检索。
4. 用 `verify_evidence_pack.py` 看“模型伪造引用”如何被拦。

### 第 1 周：Agent 图

1. 先读 `talent_request_graph.py` 的 State 和 `interrupt` 协议。
2. 再读 `talent_evaluation_dispatch.py` 的 `Send` 扇出和 `branch_worker`。
3. 再读 `talent_evaluation_report.py` 的短编号、聚合公式、报告校验。
4. 最后读 `talent_decision_engine.py`，确认三张子图的 State 契约。
5. 给每张图记录：输入 State、关键节点、输出 State、外部依赖、失败终态。

### 第 2 周：产品化补齐

优先级：认证/租户 → 跨存储补偿 → 队列可靠性 → 前端四模块 → CI/测试基线。

---

## 13. 一句话总结

**这是一个“证据驱动”的多 Agent 人才评估系统：底座是文档 RAG，中间是受控工具和证据协议，上层是四张 LangGraph 图。代码里最值得学的是“模型只提假设、程序负责验证”的工程化思路；最需要补的是认证、租户、一致性和可复现基线。**

---

## 附录 A：HTTP 接口清单

| 方法 | 路径 | 作用 | 租户/权限 |
|---|---|---|---|
| GET | `/api/health` | 健康检查 | 无 |
| GET | `/api/employees` | 员工列表 + 材料数 | 无 |
| POST | `/api/employees` | 新建员工 | 无（默认 course-demo） |
| GET | `/api/knowledge-bases` | 知识库列表 + 文件数 | 无 |
| POST | `/api/knowledge-bases` | 新建知识库 | 无 |
| DELETE | `/api/knowledge-bases/{id}` | 删除空知识库（非空 409） | 无 |
| GET | `/api/documents` | 文档列表 + 流水线状态 | 无 |
| POST | `/api/documents` | 上传 + 入队解析 | 无 |
| GET | `/api/documents/{id}` | 详情、产物、任务 | 无 |
| POST | `/api/documents/{id}/parse` | 异步/同步解析 | 无 |
| GET | `/api/documents/{id}/chunks` | 最新切片 + 父子关系 | 无 |
| POST | `/api/documents/{id}/chunks` | 触发切片 | 无 |
| POST | `/api/documents/{id}/evidence-index` | 入队索引 | 无 |
| DELETE | `/api/documents` | 批量删除（跨存储） | 无 |
| POST | `/api/evidence/search` | 纯向量检索 | 必需 |
| POST | `/api/evidence/hybrid-search` | 混合检索 + RRF + Rerank | 必需 |
| POST | `/api/talent-search/plan` | 自然语言 → Query Plan | 无 |
| POST | `/api/talent-search/candidates` | Query Plan → 候选人 | 必需 |
| POST | `/api/talent-search` | 计划 → 筛选 → 检索 → 可选证据包 | 必需 |
| POST | `/api/jobs/{id}/retry` | 重试解析 | 无 |
| POST | `/api/index-jobs/{id}/retry` | 重试失败的索引 | 无 |
| GET | `/api/jobs/{id}/artifacts` | 解析产物 URL | 无 |
| GET | `/api/evidence/citations/{chunk_id}` | 引用回表 + 权限校验 | 必需 |

---

## 附录 B：18 课与代码落点

| 课 | 主题 | 主要落点 |
|---|---|---|
| 1 | 架构与业务 | README、目录分层 |
| 2 | LLM/Prompt/Agent | `model_provider.py`、各图提示词 |
| 3 | 数据建模与解析 | `models.py`、`document_parsers.py`、`init.sql` |
| 4 | MinerU 与异步任务 | `mineru_client.py`、`worker.py`、`document_pipeline.py` |
| 5 | Chunk | `chunking.py`、`chunk_service.py`、`005` |
| 6 | 向量索引 | `milvus_store.py`、`evidence_index_service.py`、`007` |
| 7 | 混合检索/精排 | `hybrid_search_service.py`、`reranker.py` |
| 8 | Query Plan | `query_plan.py`、`/talent-search/*` |
| 9 | 可信证据 | `evidence_pack.py`、`verify_evidence_pack.py` |
| 10 | 引用展示 | `evidence_citations.py`、`EvidencePackPanel.tsx` |
| 11 | State/主图 | `talent_decision_graph.py` |
| 12 | 工具与审计 | `talent_tools.py`、`012` |
| 13 | MCP | `talent_mcp_server.py`、`talent_mcp_client.py` |
| 14 | 任务理解 | `talent_request_graph.py`、`lesson14_demo_data.py` |
| 15 | 维度分发 | `talent_evaluation_dispatch.py`、`talent_evaluation_runtime.py` |
| 16 | 评分/报告 | `talent_evaluation_report.py` |
| 17 | HITL | `talent_request_graph.py` 的 interrupt 恢复 |
| 18 | 决策引擎 | `talent_decision_engine.py`、`talent_decision_runtime.py` |

---

## 附录 C：关键文件索引

| 文件 | 作用 |
|---|---|
| [`backend/app/main.py`](backend/app/main.py) | 应用工厂 |
| [`backend/app/api/routes.py`](backend/app/api/routes.py) | 全部 HTTP 接口 |
| [`backend/app/db/models.py`](backend/app/db/models.py) | ORM |
| [`database/init.sql`](database/init.sql) | 建表 |
| [`backend/app/documents/services.py`](backend/app/documents/services.py) | 上传/解析 |
| [`backend/app/documents/chunk_service.py`](backend/app/documents/chunk_service.py) | 切片落库 |
| [`backend/app/documents/chunking.py`](backend/app/documents/chunking.py) | 切片算法 |
| [`backend/app/documents/document_pipeline.py`](backend/app/documents/document_pipeline.py) | 队列与状态 |
| [`backend/app/worker.py`](backend/app/worker.py) | 消费者 |
| [`backend/app/retrieval/query_plan.py`](backend/app/retrieval/query_plan.py) | Query Plan |
| [`backend/app/retrieval/hybrid_search_service.py`](backend/app/retrieval/hybrid_search_service.py) | 混合检索 |
| [`backend/app/integrations/milvus_store.py`](backend/app/integrations/milvus_store.py) | Milvus |
| [`backend/app/retrieval/evidence_pack.py`](backend/app/retrieval/evidence_pack.py) | 证据包 |
| [`backend/app/retrieval/evidence_citations.py`](backend/app/retrieval/evidence_citations.py) | 引用 |
| [`backend/app/talent/talent_tools.py`](backend/app/talent/talent_tools.py) | 工具层 |
| [`backend/app/talent/talent_request_graph.py`](backend/app/talent/talent_request_graph.py) | 请求图 + HITL |
| [`backend/app/talent/talent_evaluation_dispatch.py`](backend/app/talent/talent_evaluation_dispatch.py) | 分派图 |
| [`backend/app/talent/talent_evaluation_report.py`](backend/app/talent/talent_evaluation_report.py) | 报告图 |
| [`backend/app/talent/talent_decision_engine.py`](backend/app/talent/talent_decision_engine.py) | 顶层图 |
| [`backend/app/talent/talent_evaluation_runtime.py`](backend/app/talent/talent_evaluation_runtime.py) | 运行时装配 |
| [`frontend/src/main.tsx`](frontend/src/main.tsx) | 前端主文件 |
| [`frontend/src/EvidencePackPanel.tsx`](frontend/src/EvidencePackPanel.tsx) | 证据包面板 |
| [`docker-compose.yml`](docker-compose.yml) | 中间件/发布编排 |
| [`backend/langgraph.json`](backend/langgraph.json) | 图注册 |
