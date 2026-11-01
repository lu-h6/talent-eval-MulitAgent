# 合成证据材料

本目录全部材料为合成内容，不来自员工档案。C901、C902 是隔离演示数据库中的编号，与生产数据无关。
星河项目、日期、分工和引语均为虚构案例。

项目复盘、述职和面试记录分别作为三个独立来源。材料刻意保留职责说法差异，供事实抽取和冲突检查使用。
它们不能作为任何真实人才决策依据。

`scripts/verify_evidence_pack.py` 将材料载入内存 SQLite，仅作为自动测试和演示的存储替身。默认模型与检索
也使用固定测试替身。`--live-model` 只把这些合成材料发送给项目已配置的模型 API，不读取现有员工数据库、
不调用 Milvus、不读取外部文档。完整业务服务仍使用 PostgreSQL 和 Milvus。

## 文件与生成顺序

`review.md`、`report.md` 和 `interview.md` 是输入材料，本 README 只做说明。验证脚本读取三份材料并创建
`DocumentChunk`，`DemoStore` 再把它们转换成与真实 Milvus 召回结果字段一致的 `EvidenceSearchResult`，
随后调用真实的检索与证据加工模块。

```text
samples/lesson09/*.md
  → scripts/verify_evidence_pack.py
  → app/retrieval/evidence_pack.py
  → artifacts/lesson09-*.json
```

默认生成 `artifacts/lesson09-fixture-model.json`（固定模型抽取结果）；加 `--live-model` 生成
`artifacts/lesson09-live-model.json`（使用项目配置的真实模型）。两个产物都是完整接口响应，只用于
检查运行结果，不会被脚本再次读取。
