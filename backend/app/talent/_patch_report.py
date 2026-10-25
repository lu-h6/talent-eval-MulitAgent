# -*- coding: utf-8 -*-
"""一次性补丁脚本：为 talent_evaluation_report.py 补充中文注释。"""
from pathlib import Path

p = Path(r"E:\PythonProjects\talent-eval-agents\talent-eval-agents\backend\app\talent\talent_evaluation_report.py")
text = p.read_text(encoding="utf-8")

pairs = [
    # 类型别名注释
    (
        'EvidenceStatus = Literal["sufficient", "partial", "missing", "conflicting", "failed"]\nConfidenceLevel = Literal["high", "medium", "low", "unavailable"]',
        'EvidenceStatus = Literal["sufficient", "partial", "missing", "conflicting", "failed"]  # 证据充分度状态\nConfidenceLevel = Literal["high", "medium", "low", "unavailable"]  # 置信等级',
    ),
    # 模型类 docstring
    (
        'class DimensionAssessment(BaseModel):\n    task_id: str\n    candidate_id: str',
        'class DimensionAssessment(BaseModel):\n    """单个维度的评分结果。"""\n    task_id: str  # 任务ID\n    candidate_id: str  # 候选人工号',
    ),
    (
        "class DimensionJudgement(BaseModel):\n    evidence_status: Literal[\"sufficient\", \"partial\", \"missing\", \"conflicting\"] = Field(",
        "class DimensionJudgement(BaseModel):\n    \"\"\"模型对单个维度的评分判断（结构化输出）。\"\"\"\n    evidence_status: Literal[\"sufficient\", \"partial\", \"missing\", \"conflicting\"] = Field(",
    ),
    (
        "class ConsistencyIssue(BaseModel):\n    candidate_id: str = Field(",
        "class ConsistencyIssue(BaseModel):\n    \"\"\"跨维度的一致性问题。\"\"\"\n    candidate_id: str = Field(",
    ),
    (
        "class ConsistencyReview(BaseModel):\n    issues: list[ConsistencyIssue] = Field(",
        "class ConsistencyReview(BaseModel):\n    \"\"\"一致性审查的输出：问题列表。\"\"\"\n    issues: list[ConsistencyIssue] = Field(",
    ),
    (
        "class CandidateAssessment(BaseModel):\n    candidate_id: str\n    confirmed_score: float",
        "class CandidateAssessment(BaseModel):\n    \"\"\"单个候选人的聚合评估结果。\"\"\"\n    candidate_id: str\n    confirmed_score: float  # 已确认得分",
    ),
    (
        "class EvidenceItem(BaseModel):\n    ref_id: str = Field(",
        "class EvidenceItem(BaseModel):\n    \"\"\"论文证据项：在内部完整编号与模型短编号间转换。\"\"\"\n    ref_id: str = Field(",
    ),
    (
        "class ReportPoint(BaseModel):\n    text: str = Field(",
        "class ReportPoint(BaseModel):\n    \"\"\"报告中的一条关键判断及其证据引用。\"\"\"\n    text: str = Field(",
    ),
    (
        "class CandidateReportDraft(BaseModel):\n    candidate_id: str = Field(",
        "class CandidateReportDraft(BaseModel):\n    \"\"\"单个候选人的报告草稿。\"\"\"\n    candidate_id: str = Field(",
    ),
    (
        "class ReportDraft(BaseModel):\n    title: str = Field(",
        "class ReportDraft(BaseModel):\n    \"\"\"整份候选人评估报告的草稿。\"\"\"\n    title: str = Field(",
    ),
    # 图状态字段注释
    (
        "class TalentEvaluationReportState(TypedDict, total=False):\n    dimensions: list[dict[str, Any]]",
        "class TalentEvaluationReportState(TypedDict, total=False):\n    \"\"\"报告生成子图的图状态。\"\"\"\n    dimensions: list[dict[str, Any]]  # 评估维度",
    ),
    (
        "    branch_results: list[dict[str, Any]]\n    evidence_items: list[dict[str, Any]]\n    dimension_assessments: list[dict[str, Any]]\n    consistency_issues: list[dict[str, Any]]\n    candidate_assessments: list[dict[str, Any]]\n    ranking: list[str]\n    report_draft: dict[str, Any]\n    report: str\n    status: str\n    errors: list[str]",
        "    branch_results: list[dict[str, Any]]  # 取证分支结果\n    evidence_items: list[dict[str, Any]]  # 证据项列表\n    dimension_assessments: list[dict[str, Any]]  # 维度评分结果\n    consistency_issues: list[dict[str, Any]]  # 一致性问题\n    candidate_assessments: list[dict[str, Any]]  # 候选人聚合评估\n    ranking: list[str]  # 候选人排名\n    report_draft: dict[str, Any]  # 报告草稿\n    report: str  # 最终 Markdown 报告\n    status: str  # 当前状态\n    errors: list[str]  # 错误列表",
    ),
    # 工具函数 docstring
    (
        "def branch_evidence_refs(branch: BranchEvidenceDraft) -> list[str]:\n    return list(",
        "def branch_evidence_refs(branch: BranchEvidenceDraft) -> list[str]:\n    \"\"\"提取某分支引用的全部证据 chunk_id（去重保序）。\"\"\"\n    return list(",
    ),
    (
        "def branch_missing_information(branch: BranchEvidenceDraft) -> list[str]:\n    missing: list[str] = []",
        "def branch_missing_information(branch: BranchEvidenceDraft) -> list[str]:\n    \"\"\"汇总某分支的缺失信息（去重、丢弃空串）。\"\"\"\n    missing: list[str] = []",
    ),
    (
        "    return list(dict.fromkeys(item for item in missing if item.strip()))\n\n\ndef evidence_items_from_branches(",
        "    return list(dict.fromkeys(item for item in missing if item.strip()))\n\n\ndef evidence_items_from_branches(",
    ),
    (
        "    branches: list[BranchEvidenceDraft],\n) -> list[EvidenceItem]:\n    items: dict[str, EvidenceItem] = {}",
        "    branches: list[BranchEvidenceDraft],\n) -> list[EvidenceItem]:\n    \"\"\"从各分支的证据引用中收集去重后的证据项，并分配模型短编号 E1..En。\"\"\"\n    items: dict[str, EvidenceItem] = {}",
    ),
    (
        "    return list(items.values())\n\n\ndef _evidence_reference_maps(",
        "    return list(items.values())\n\n\ndef _evidence_reference_maps(",
    ),
    (
        "    evidence_items: list[EvidenceItem],\n) -> tuple[dict[str, str], dict[str, str]]:\n    chunk_to_model",
        "    evidence_items: list[EvidenceItem],\n) -> tuple[dict[str, str], dict[str, str]]:\n    \"\"\"建立 chunk_id 与模型短编号之间的双向映射，并校验短编号唯一性。\"\"\"\n    chunk_to_model",
    ),
    (
        "def _restore_evidence_refs(\n    evidence_refs: list[str],\n    model_to_chunk: dict[str, str],\n) -> list[str]:\n    unknown",
        "def _restore_evidence_refs(\n    evidence_refs: list[str],\n    model_to_chunk: dict[str, str],\n) -> list[str]:\n    \"\"\"将模型返回的短证据编号还原为内部 chunk_id，未知编号则抛错。\"\"\"\n    unknown",
    ),
    (
        "def _model_evidence_payload(\n    evidence_items: list[EvidenceItem],\n) -> tuple[list[dict[str, str]], dict[str, str], dict[str, str]]:\n    chunk_to_model",
        "def _model_evidence_payload(\n    evidence_items: list[EvidenceItem],\n) -> tuple[list[dict[str, str]], dict[str, str], dict[str, str]]:\n    \"\"\"构造喂给模型的证据载荷（使用短编号）并返回映射。\"\"\"\n    chunk_to_model",
    ),
    (
        "def _branch_model_payload(\n    branch: BranchEvidenceDraft,\n    chunk_to_model: dict[str, str],\n) -> dict[str, Any]:\n    return {",
        "def _branch_model_payload(\n    branch: BranchEvidenceDraft,\n    chunk_to_model: dict[str, str],\n) -> dict[str, Any]:\n    \"\"\"将分支证据转为模型可见的字典（内部编号替换为短编号）。\"\"\"\n    return {",
    ),
    # 评分器
    (
        "def build_structured_dimension_scorer(\n    model_provider: ModelProvider,\n) -> DimensionScorer:\n    def score(",
        "def build_structured_dimension_scorer(\n    model_provider: ModelProvider,\n) -> DimensionScorer:\n    \"\"\"构造结构化维度评分器。\"\"\"\n    def score(",
    ),
    (
        "        model = model_provider()\n        if model is None:\n            raise RuntimeError(\"评分模型未配置\")\n        safe_evidence",
        "        model = model_provider()\n        if model is None:\n            raise RuntimeError(\"评分模型未配置\")\n        # 将证据转为模型短编号载荷\n        safe_evidence",
    ),
    (
        "        judgement = DimensionJudgement.model_validate(result)\n        return judgement.model_copy(",
        "        judgement = DimensionJudgement.model_validate(result)\n        # 将模型返回的短证据编号还原为内部编号\n        return judgement.model_copy(",
    ),
    # 报告撰写器
    (
        "def build_structured_report_writer(\n    model_provider: ModelProvider,\n) -> ReportWriter:\n    def write(",
        "def build_structured_report_writer(\n    model_provider: ModelProvider,\n) -> ReportWriter:\n    \"\"\"构造结构化报告撰写器。\"\"\"\n    def write(",
    ),
    (
        "        result = model.with_structured_output(ReportDraft).invoke(\n            [\n                (\"system\", REPORT_WRITER_PROMPT),",
        "        # 调用模型生成报告草稿\n        result = model.with_structured_output(ReportDraft).invoke(\n            [\n                (\"system\", REPORT_WRITER_PROMPT),",
    ),
    (
        "        draft = ReportDraft.model_validate(result)\n        restored_candidates = []",
        "        draft = ReportDraft.model_validate(result)\n        # 还原各报告要点中的证据短编号\n        restored_candidates = []",
    ),
    # 一致性审查器
    (
        "def build_structured_consistency_reviewer(\n    model_provider: ModelProvider,\n) -> ConsistencyReviewer:\n    def review(",
        "def build_structured_consistency_reviewer(\n    model_provider: ModelProvider,\n) -> ConsistencyReviewer:\n    \"\"\"构造结构化一致性检查器。\"\"\"\n    def review(",
    ),
    (
        "        result = model.with_structured_output(ConsistencyReview).invoke(\n            [\n                (\"system\", CONSISTENCY_REVIEW_PROMPT),",
        "        # 调用模型检查跨维度一致性\n        result = model.with_structured_output(ConsistencyReview).invoke(\n            [\n                (\"system\", CONSISTENCY_REVIEW_PROMPT),",
    ),
    # 校验与聚合
    (
        "def validate_dimension_judgement(\n    branch: BranchEvidenceDraft,\n    judgement: DimensionJudgement,\n) -> None:\n    unavailable_refs",
        "def validate_dimension_judgement(\n    branch: BranchEvidenceDraft,\n    judgement: DimensionJudgement,\n) -> None:\n    \"\"\"校验评分判断的合法性：引用、状态-分数配对、缺失/冲突必填等。\"\"\"\n    unavailable_refs",
    ),
    (
        "def aggregate_candidate_assessments(\n    dimensions: list[EvaluationDimension],\n    assessments: list[DimensionAssessment],\n    consistency_issues: list[ConsistencyIssue] | None = None,\n) -> list[CandidateAssessment]:\n    issues_by_candidate",
        "def aggregate_candidate_assessments(\n    dimensions: list[EvaluationDimension],\n    assessments: list[DimensionAssessment],\n    consistency_issues: list[ConsistencyIssue] | None = None,\n) -> list[CandidateAssessment]:\n    \"\"\"按加权累计计算每位候选人的已确认/理论上限得分与覆盖率，并确定置信度。\"\"\"\n    issues_by_candidate",
    ),
    (
        "    for candidate_id, items in grouped.items():\n        items_by_dimension",
        "    for candidate_id, items in grouped.items():\n        # 按维度序号对齐各维度评估\n        items_by_dimension",
    ),
    (
        "def rank_candidate_assessments(\n    candidates: list[CandidateAssessment],\n) -> list[CandidateAssessment]:\n    return sorted(",
        "def rank_candidate_assessments(\n    candidates: list[CandidateAssessment],\n) -> list[CandidateAssessment]:\n    \"\"\"按（已确认得分降序、覆盖率降序、工号升序）对候选人排序。\"\"\"\n    return sorted(",
    ),
]

for old, new in pairs:
    cnt = text.count(old)
    assert cnt == 1, f"anchor count {cnt} for: {old[:70]!r}"
    text = text.replace(old, new)

p.write_text(text, encoding="utf-8")
print("patched OK; anchors replaced:", len(pairs))