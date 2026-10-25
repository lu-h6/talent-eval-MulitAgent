# 第 9 课合成材料

本目录全部材料为课程编写时新建的虚构内容，不来自员工档案。C901、C902 是隔离演示数据库中的编号，与生产数据无关。星河项目、日期、分工和引语均为合成案例。

项目复盘、述职和面试记录分别作为三个独立来源。材料保留职责说法差异，供事实抽取和冲突检查使用。它们不能作为任何真实人才决策依据。

`verify_evidence_pack.py` 将材料载入内存 SQLite，仅作为自动测试和演示的存储替身。默认模型与检索也使用固定测试替身。`--live-model` 只把这些合成材料发送给项目已配置的模型 API，不读取现有员工数据库、不调用 Milvus、不读取外部文档。完整业务服务仍使用 PostgreSQL 和 Milvus。

## 文件与生成顺序

`review.md`、`report.md` 和 `interview.md` 是输入材料，当前目录的 `README.md` 只做说明。验证脚本读取三份材料并创建 `DocumentChunk`，`DemoStore` 再把它们转换成与真实 Milvus 召回结果字段一致的 `EvidenceSearchResult`，随后调用真实的 `/api/talent-search` 和证据加工模块。

```text
samples/lesson09/*.md
  → scripts/verify_evidence_pack.py
  → app/retrieval/evidence_pack.py
  → artifacts/lesson09-*.json
```

默认命令生成 `lesson09-fixture-model.json`，使用固定的模型抽取结果。增加 `--live-model` 后生成 `lesson09-live-model.json`，使用项目配置的真实模型抽取相同的合成材料。两个 artifact 都是完整接口响应，只用于检查运行结果，不会被脚本再次读取。

Milvus 可以保存这些合成材料。第 9 课固定召回列表，用于隔离验证证据加工；完整的材料导入、Milvus 召回和前端引用展示在第 10 课串联。
