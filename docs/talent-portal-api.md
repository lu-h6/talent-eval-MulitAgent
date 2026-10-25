# 评估与推荐页接口（Talent Portal API）

面向“评估与推荐”页的后端契约，覆盖岗位 JD、候选人画像、会话运行、SSE、评估执行与报告。

## 1. 启动依赖

评估会话与报告由 LangGraph Agent Server 执行，FastAPI 只做 REST/SSE 网关：

```bash
cd backend
# Windows 需要 UTF-8，否则 langgraph_api 读取 openapi.json 会按 GBK 解码失败
# PowerShell: $env:PYTHONUTF8="1"；bash: export PYTHONUTF8=1
uv run langgraph dev --no-browser --no-reload --port 2024
```

FastAPI 默认连接 `http://127.0.0.1:2024`，使用的图是 `talent_decision_engine`
（`backend/langgraph.json` 已注册）。配置项：

```dotenv
LANGGRAPH_URL=http://127.0.0.1:2024
LANGGRAPH_ASSISTANT_ID=talent_decision_engine
LANGGRAPH_TIMEOUT_SECONDS=30
```

Agent Server 未启动时，会话/评估接口返回 `503`，档案接口不受影响。

## 2. 通用请求头

所有接口都需要：

```text
X-Tenant-Id: course-demo
X-Permission-Scopes: hr_private,department,internal
```

权限范围必须使用库里的实际值（`hr_private`、`department`、`internal`）。

## 3. 接口清单

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/job-descriptions?query=&limit=` | 查询/列出岗位 JD |
| GET | `/api/job-descriptions/{job_code}` | 读取单个 JD |

演示岗位可用 `cd backend && uv run python -m scripts.seed_lesson14_jobs` 导入（幂等）。
| POST | `/api/candidates/profiles` | 按工号批量读取候选人画像 |
| POST | `/api/talent/threads` | 创建会话 |
| GET | `/api/talent/threads` | 列出当前租户会话 |
| DELETE | `/api/talent/threads/{thread_id}` | 删除会话 |
| POST | `/api/talent/threads/{thread_id}/runs` | 发起一轮运行（SSE） |
| POST | `/api/talent/threads/{thread_id}/resume` | 恢复 interrupt（SSE） |
| GET | `/api/talent/threads/{thread_id}/state` | 读取状态、下一节点、interrupt |
| POST | `/api/talent/evaluations` | 创建完整评估运行（202） |
| GET | `/api/talent/evaluations/{evaluation_id}` | 轮询评估状态 |
| GET | `/api/talent/evaluations/{evaluation_id}/report` | 读取评估报告 |

`evaluation_id` 就是 `thread_id`，便于前端用同一个 id 轮询状态和取报告。

## 4. 典型流程

### 4.1 创建会话

```http
POST /api/talent/threads
Content-Type: application/json

{"metadata": {"source": "web"}}
```

响应 `201`：

```json
{
  "thread_id": "01a17266-...",
  "status": "idle",
  "metadata": {"tenant_id": "course-demo", "kind": "talent_chat", "source": "web"},
  "created_at": "...",
  "updated_at": "..."
}
```

### 4.2 发起运行并消费 SSE

```http
POST /api/talent/threads/{thread_id}/runs
Content-Type: application/json

{"request_text": "帮我找上海地区、5 年以上经验的 AI 应用工程师"}
```

响应 `text/event-stream`，事件类型包括：

- `metadata`：`run_id`
- `updates|...`：节点增量（含子图）
- `values|...`：完整状态快照
- `__interrupt__`：需要前端处理的暂停点
- `error`：Agent Server 运行期错误

前端按 `event:` / `data:` 解析即可，不需要自己拼接 LangGraph。

### 4.3 处理 interrupt

`GET /api/talent/threads/{thread_id}/state` 返回：

```json
{
  "thread_id": "...",
  "status": "interrupted",
  "state_status": "clarification_required",
  "values": {...},
  "next": ["resolve_requirements"],
  "interrupts": [
    {"value": {"type": "requirement_revision", "interaction_id": "rr-v0", "...": "..."}}
  ]
}
```

四类载荷与恢复值：

| type | 恢复 payload |
|---|---|
| `job_selection` | `{"action": "select", "job_code": "JD-AI-002"}` 或 `{"action": "cancel"}` |
| `condition_clarification` | `{"interaction_id": "cc-v0", "action": "revise", "filters": [...], "semantic_requirements": [...]}` 或 cancel |
| `requirement_revision` | `{"interaction_id": "rr-v0", "action": "revise", "revision_text": "..."}` 或 cancel |
| `empty_candidate_set` | `{"interaction_id": "ec-v0", "action": "relax", "relaxations": [{"field": "region", "op": "remove"}]}` 或 cancel |

恢复请求：

```http
POST /api/talent/threads/{thread_id}/resume
Content-Type: application/json

{"payload": {"interaction_id": "rr-v0", "action": "revise", "revision_text": "上海、3 年以上 AI 经验"}}
```

同样返回 SSE。

### 4.4 创建评估并轮询

```http
POST /api/talent/evaluations
Content-Type: application/json

{"request_text": "按照 JD-AI-001 评估候选人的大模型应用能力"}
```

响应 `202`：

```json
{
  "evaluation_id": "01a17266-...",
  "thread_id": "01a17266-...",
  "run_id": "01a17266-...",
  "status": "pending"
}
```

轮询：

```http
GET /api/talent/evaluations/{evaluation_id}
```

返回与线程状态一致的结构，并额外带 `evaluation_id`。如果图在请求理解阶段暂停，`interrupts` 会给出下一步载荷。

报告：

```http
GET /api/talent/evaluations/{evaluation_id}/report
```

未完成：

```json
{
  "evaluation_id": "...",
  "status": "interrupted",
  "state_status": "candidates_empty",
  "available": false,
  "next": ["resolve_requirements"],
  "interrupts": [...],
  "message": "评估尚未完成，请继续轮询状态或处理 interrupt"
}
```

已完成：

```json
{
  "evaluation_id": "...",
  "status": "idle",
  "state_status": "completed",
  "available": true,
  "report": "# 人才评估报告 ...",
  "ranking": ["C001", "C002"],
  "candidate_ids": ["C001", "C002"],
  "dimension_assessments": [...],
  "candidate_assessments": [...],
  "consistency_issues": [...],
  "errors": []
}
```

## 5. 错误约定

- `503`：Agent Server 未启动或不可达。
- `404`：会话/JD 不存在，或不属于当前租户。
- `422`：请求体校验失败。
- `502`：Agent Server 内部错误。
- SSE 已开始后出错：返回 `event: error`，`data` 含 `code` 与 `message`。

## 6. 注意事项

- Agent Server 当前使用 `langgraph dev` 的**内存 checkpointer**，重启后会话丢失；生产需要持久化 checkpointer。
- 评估运行是异步的：`POST /evaluations` 只保证运行已创建，结果通过轮询或 SSE 获取。
- 前端只消费本文件的 DTO，不直接依赖 LangGraph 内部 State。
- `langgraph dev` 在 Windows 下必须设置 `PYTHONUTF8=1`，否则会在启动时因 GBK 解码失败退出。
