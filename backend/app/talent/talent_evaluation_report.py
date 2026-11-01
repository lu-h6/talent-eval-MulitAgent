"""报告合成：单维度评分、跨维度一致性检查、聚合排序与 Markdown 渲染。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.talent.talent_evaluation_dispatch import BranchEvidenceDraft, EvaluationDimension

logger = logging.getLogger(__name__)


def _log_node(node_name: str, direction: str, payload: dict[str, Any]) -> None:
    """统一打印图节点输入/输出日志。"""
    logger.info(
        "node=%s %s %s",
        node_name,
        direction,
        json.dumps(payload, ensure_ascii=False, default=str),
    )


EvidenceStatus = Literal["sufficient", "partial", "missing", "conflicting", "failed"]  # 证据充分度状态
ConfidenceLevel = Literal["high", "medium", "low", "unavailable"]  # 置信等级


class DimensionAssessment(BaseModel):
    """单个维度的评分结果。"""
    task_id: str  # 任务ID
    candidate_id: str  # 候选人工号
    dimension_number: int = Field(ge=1)  # 维度序号
    evidence_status: EvidenceStatus  # 证据充分度
    score: int | None = Field(default=None, ge=0, le=5)  # 0-5 分
    confidence: ConfidenceLevel  # 置信等级
    evidence_refs: list[str] = Field(default_factory=list)  # 证据引用（内部编号）
    missing_items: list[str] = Field(default_factory=list)  # 缺失信息
    conflicts: list[str] = Field(default_factory=list)  # 冲突说明
    rationale: str = Field(min_length=1, max_length=1000)  # 评分理由


class DimensionJudgement(BaseModel):
    """模型对单个维度的评分判断（结构化输出）。"""
    evidence_status: Literal["sufficient", "partial", "missing", "conflicting"] = Field(
        description=(
            "当前维度的证据状态：sufficient 表示证据充分，partial 表示可评分但仍有缺失，"
            "missing 表示无法评分，conflicting 表示关键事实互斥"
        )
    )
    score: int | None = Field(
        default=None,
        ge=0,
        le=5,
        description="依据当前维度评分锚点给出的 0 到 5 分；missing 和 conflicting 必须为 null",
    )
    confidence: ConfidenceLevel = Field(
        description="本次维度判断的置信等级，取 high、medium、low 或 unavailable"
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="支撑本次判断的短证据编号，只能复制输入中的 E1、E2 等编号",
    )
    missing_items: list[str] = Field(
        default_factory=list,
        description="当前证据未覆盖且会影响判断的信息；没有缺失时返回空列表",
    )
    conflicts: list[str] = Field(
        default_factory=list,
        description="关键事实之间的具体矛盾；仅 conflicting 状态填写",
    )
    rationale: str = Field(
        min_length=1,
        max_length=1000,
        description="结合评分锚点与证据事实说明评分或不可评分原因",
    )


class ConsistencyIssue(BaseModel):
    """跨维度的一致性问题。"""
    candidate_id: str = Field(description="存在跨维度逻辑冲突的候选人编号")
    dimension_numbers: list[int] = Field(
        min_length=2,
        max_length=6,
        description="发生逻辑冲突的维度序号，至少包含两个维度",
    )
    evidence_refs: list[str] = Field(
        min_length=1,
        max_length=12,
        description="证明该冲突的短证据编号，只能复制输入中的 E1、E2 等编号",
    )
    message: str = Field(
        min_length=1,
        max_length=1000,
        description="说明哪些已评分结论或事实互相矛盾",
    )


class ConsistencyReview(BaseModel):
    """一致性审查的输出：问题列表。"""
    issues: list[ConsistencyIssue] = Field(
        default_factory=list,
        max_length=20,
        description="已确认的跨维度一致性问题；没有问题时返回空列表",
    )


class CandidateAssessment(BaseModel):
    """单个候选人的聚合评估结果。"""
    candidate_id: str  # 候选人工号
    confirmed_score: float = Field(ge=0, le=100)  # 已确认得分
    possible_score: float = Field(ge=0, le=100)  # 理论上限得分
    evidence_coverage_percent: int = Field(ge=0, le=100)  # 可评分覆盖率
    confidence: Literal["high", "medium", "low"]  # 置信等级
    dimensions: list[DimensionAssessment]  # 各维度评分
    consistency_issues: list[ConsistencyIssue] = Field(default_factory=list)  # 一致性问题


class EvidenceItem(BaseModel):
    """证据项：在内部完整编号与模型短编号间转换。"""
    ref_id: str = Field(min_length=1, description="内部保存的完整 Chunk 编号")
    model_ref: str | None = Field(
        default=None,
        pattern=r"^E[1-9]\d*$",
        description="提供给模型读写的稳定短证据编号",
    )
    candidate_id: str = Field(min_length=1)  # 所属候选人工号
    quote: str = Field(min_length=1)  # 原文引文
    source_label: str = Field(min_length=1)  # 来源标签
    citation_url: str = Field(min_length=1)  # 回引链接


class ReportPoint(BaseModel):
    """报告中的一条关键判断及其证据引用。"""
    text: str = Field(
        min_length=1,
        max_length=500,
        description="报告中的一条关键判断，只能概括已核验事实，不得新增信息",
    )
    evidence_refs: list[str] = Field(
        min_length=1,
        max_length=8,
        description="支撑该判断的短证据编号，只能复制输入中的 E1、E2 等编号",
    )


class CandidateReportDraft(BaseModel):
    """单个候选人的报告草稿。"""
    candidate_id: str = Field(description="候选人编号，顺序必须与输入排名一致")
    summary: str = Field(
        min_length=1,
        max_length=1000,
        description="候选人的综合结论，需同时反映已确认得分、覆盖率和置信度",
    )
    highlights: list[ReportPoint] = Field(
        default_factory=list,
        max_length=8,
        description="候选人的主要匹配依据，每一项必须带有已提供的短证据编号",
    )
    risks: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="已有评估中的缺失、冲突、执行失败或置信度风险，不得推测新风险",
    )


class ReportDraft(BaseModel):
    """整份候选人评估报告的草稿。"""
    title: str = Field(
        min_length=1,
        max_length=120,
        description="整份候选人评估报告的标题",
    )
    overview: str = Field(
        min_length=1,
        max_length=1500,
        description="按程序排名概括候选人总体差异，不得修改分数或排序",
    )
    candidates: list[CandidateReportDraft] = Field(
        min_length=1,
        description="候选人报告列表，数量和顺序必须与输入排名完全一致",
    )


class TalentEvaluationReportState(TypedDict, total=False):
    """报告生成子图的图状态。"""
    dimensions: list[dict[str, Any]]  # 评估维度
    branch_results: list[dict[str, Any]]  # 取证分支结果
    evidence_items: list[dict[str, Any]]  # 证据项列表
    dimension_assessments: list[dict[str, Any]]  # 维度评分结果
    consistency_issues: list[dict[str, Any]]  # 一致性问题
    candidate_assessments: list[dict[str, Any]]  # 候选人聚合评估
    ranking: list[str]  # 候选人排名
    report_draft: dict[str, Any]  # 报告草稿
    report: str  # 最终 Markdown 报告
    status: str  # 当前状态
    errors: list[str]  # 错误列表


DimensionScorer = Callable[
    [EvaluationDimension, BranchEvidenceDraft, list[EvidenceItem]], DimensionJudgement,]  # 维度评分函数
ReportWriter = Callable[[list[CandidateAssessment], list[EvidenceItem]], ReportDraft]  # 报告写入函数
ConsistencyReviewer = Callable[[list[DimensionAssessment], list[EvidenceItem]], ConsistencyReview,]  # 一致性审查函数
ModelProvider = Callable[[], Any]  # 模型提供函数


def branch_evidence_refs(branch: BranchEvidenceDraft) -> list[str]:
    """提取某分支引用的全部证据 chunk_id（跨要求/事实/来源去重保序）。"""
    return list(
        dict.fromkeys(
            source.chunk_id
            for requirement in branch.requirements
            for fact in requirement.facts
            for source in fact.sources
        )
    )


def branch_missing_information(branch: BranchEvidenceDraft) -> list[str]:
    """汇总某分支的全部缺失信息（去重、丢弃空串）。"""
    missing: list[str] = []
    for requirement in branch.requirements:
        missing.extend(requirement.missing_information)
        # 状态为 missing 且未显式给出缺失信息时，用检索查询兜底
        if requirement.status == "missing" and not requirement.missing_information:
            missing.append(requirement.query)
    return list(dict.fromkeys(item for item in missing if item.strip()))


def evidence_items_from_branches(
        branches: list[BranchEvidenceDraft],
) -> list[EvidenceItem]:
    """
    从各分支的证据引用中收集去重后的证据项，并分配模型短编号 E1..En。
    """
    items: dict[str, EvidenceItem] = {}
    for branch in branches:
        # 遍历分支内所有事实来源，按 chunk_id 去重录入
        for requirement in branch.requirements:
            for fact in requirement.facts:
                for source in fact.sources:
                    if source.chunk_id not in items:
                        items[source.chunk_id] = EvidenceItem(
                            ref_id=source.chunk_id,
                            model_ref=f"E{len(items) + 1}",
                            candidate_id=branch.candidate_id,
                            quote=source.quote,
                            source_label=source.source_label,
                            citation_url=(
                                f"/api/evidence/citations/{source.chunk_id}"
                                f"?quote_start={source.quote_start}"
                                f"&quote_end={source.quote_end}"
                            ),
                        )
    return list(items.values())


def _evidence_reference_maps(
        evidence_items: list[EvidenceItem],
) -> tuple[dict[str, str], dict[str, str]]:
    """
    建立 chunk_id 与模型短编号间的双向映射，并校验短编号唯一性。
    """
    chunk_to_model: dict[str, str] = {}
    model_to_chunk: dict[str, str] = {}
    for index, item in enumerate(evidence_items, start=1):
        model_ref = item.model_ref or f"E{index}"
        existing = model_to_chunk.get(model_ref)
        if existing is not None and existing != item.ref_id:
            raise ValueError(f"重复的模型证据编号: {model_ref}")
        chunk_to_model[item.ref_id] = model_ref
        model_to_chunk[model_ref] = item.ref_id
    return chunk_to_model, model_to_chunk


def _restore_evidence_refs(
        evidence_refs: list[str],
        model_to_chunk: dict[str, str],
) -> list[str]:
    """
    将模型返回的短证据编号还原为内部 chunk_id，未知编号则抛错。
    """
    unknown = [ref_id for ref_id in evidence_refs if ref_id not in model_to_chunk]
    if unknown:
        raise ValueError(f"模型返回了未知证据编号: {', '.join(unknown)}")
    return [model_to_chunk[ref_id] for ref_id in evidence_refs]


def _model_evidence_payload(
        evidence_items: list[EvidenceItem],
) -> tuple[list[dict[str, str]], dict[str, str], dict[str, str]]:
    """
    构造喂给模型的证据载荷（使用短编号），并返回双向映射。
    """
    chunk_to_model, model_to_chunk = _evidence_reference_maps(evidence_items)
    payload = [
        {
            "ref_id": chunk_to_model[item.ref_id],
            "candidate_id": item.candidate_id,
            "quote": item.quote,
            "source_label": item.source_label,
        }
        for item in evidence_items
    ]
    return payload, chunk_to_model, model_to_chunk


def _branch_model_payload(
        branch: BranchEvidenceDraft,
        chunk_to_model: dict[str, str],
) -> dict[str, Any]:
    """
    将分支证据转为模型可见的字典（内部编号替换为短编号）。
    """
    return {
        "task_id": branch.task_id,
        "candidate_id": branch.candidate_id,
        "dimension_number": branch.dimension_number,
        "execution_status": branch.execution_status,
        "requirements": [
            {
                "requirement_id": requirement.requirement_id,
                "query": requirement.query,
                "status": requirement.status,
                "missing_information": requirement.missing_information,
                "facts": [
                    {
                        "event": fact.event,
                        "period": fact.period,
                        "claim": fact.claim,
                        "answer": fact.answer,
                        "evidence_refs": [
                            chunk_to_model[source.chunk_id]
                            for source in fact.sources
                            if source.chunk_id in chunk_to_model
                        ],
                    }
                    for fact in requirement.facts
                ],
                "conflicts": requirement.conflicts,
            }
            for requirement in branch.requirements
        ],
    }


DIMENSION_SCORER_PROMPT = """你负责依据单个评估维度的定义、证据要求和 0/3/5 分锚点完成评分。
输入中的证据使用 E1、E2 这类短编号。evidence_refs 只能逐字复制这些短编号，不得缩写、改写或生成编号。
有完整可评分证据时使用 sufficient，存在可评分事实但仍有缺失时使用 partial。
材料无法支持评分时使用 missing，同一事实范围存在互斥说法时使用 conflicting。
missing 和 conflicting 的 score 必须为 null，材料缺失不能解释为 0 分。
score 必须对照输入维度的评分锚点，rationale 需要说明证据事实与锚点的对应关系。
missing_items 只记录影响当前判断的缺失信息，conflicts 只记录已由证据证明的互斥事实。
""".strip()


def build_structured_dimension_scorer(
        model_provider: ModelProvider,
) -> DimensionScorer:
    """构造结构化维度评分器：让模型依据锚点给出维度分数。"""

    def score(
            dimension: EvaluationDimension,
            branch: BranchEvidenceDraft,
            evidence_items: list[EvidenceItem],
    ) -> DimensionJudgement:
        """调用模型对单个候选人的维度打分。"""
        model = model_provider()
        if model is None:
            raise RuntimeError("评分模型未配置")
        # 将证据转为模型短编号载荷
        safe_evidence, chunk_to_model, model_to_chunk = _model_evidence_payload(
            evidence_items
        )
        payload = {
            "dimension": dimension.model_dump(mode="json"),
            "branch": _branch_model_payload(branch, chunk_to_model),
            "evidence_items": safe_evidence,
        }
        result = model.with_structured_output(DimensionJudgement).invoke(
            [
                ("system", DIMENSION_SCORER_PROMPT),
                ("user", json.dumps(payload, ensure_ascii=False)),
            ]
        )
        judgement = DimensionJudgement.model_validate(result)
        # 将模型返回的短证据编号还原为内部编号
        return judgement.model_copy(
            update={
                "evidence_refs": _restore_evidence_refs(
                    judgement.evidence_refs,
                    model_to_chunk,
                )
            }
        )

    return score


REPORT_WRITER_PROMPT = """你负责将已排序的候选人评估结果组织为结构化报告草稿。
保持输入中的候选人顺序，不得修改分数、排名、证据状态或风险结论。
输入中的证据使用 E1、E2 这类短编号。ReportPoint.evidence_refs 只能逐字复制当前候选人已评分结果中的短编号。
title 是整份报告标题，overview 按当前排名概括总体差异，candidates 必须与输入候选人数量和顺序一致。
CandidateReportDraft.summary 概括当前候选人的得分、覆盖率与置信度，highlights 只整理有证据的主要依据。
risks 只整理输入中已有的缺失、冲突、执行失败和置信度风险，不得推测新风险。
报告草稿只负责概述、主要依据和风险表达，Markdown 与可点击链接由程序渲染。
""".strip()


def build_structured_report_writer(
        model_provider: ModelProvider,
) -> ReportWriter:
    """构造结构化报告撰写器：让模型生成报告草稿。"""

    def write(
            candidates: list[CandidateAssessment],
            evidence_items: list[EvidenceItem],
    ) -> ReportDraft:
        """调用模型写出最终报告。"""
        model = model_provider()
        if model is None:
            raise RuntimeError("报告模型未配置")
        safe_evidence, chunk_to_model, model_to_chunk = _model_evidence_payload(
            evidence_items
        )
        candidate_payloads: list[dict[str, Any]] = []
        for candidate in candidates:
            # 将每位候选人的维度/一致性问题证据引用替换为短编号
            candidate_payload = candidate.model_dump(mode="json")
            for dimension in candidate_payload["dimensions"]:
                dimension["evidence_refs"] = [
                    chunk_to_model[ref_id]
                    for ref_id in dimension["evidence_refs"]
                ]
            for issue in candidate_payload["consistency_issues"]:
                issue["evidence_refs"] = [
                    chunk_to_model[ref_id]
                    for ref_id in issue["evidence_refs"]
                ]
            candidate_payloads.append(candidate_payload)
        payload = {
            "candidate_assessments": candidate_payloads,
            "evidence_items": safe_evidence,
        }
        # 调用模型生成报告草稿
        result = model.with_structured_output(ReportDraft).invoke(
            [
                ("system", REPORT_WRITER_PROMPT),
                ("user", json.dumps(payload, ensure_ascii=False)),
            ]
        )
        draft = ReportDraft.model_validate(result)
        # 还原各报告要点中的证据短编号
        restored_candidates = []
        for candidate in draft.candidates:
            restored_candidates.append(
                candidate.model_copy(
                    update={
                        "highlights": [
                            point.model_copy(
                                update={
                                    "evidence_refs": _restore_evidence_refs(
                                        point.evidence_refs,
                                        model_to_chunk,
                                    )
                                }
                            )
                            for point in candidate.highlights
                        ]
                    }
                )
            )
        return draft.model_copy(update={"candidates": restored_candidates})

    return write


CONSISTENCY_REVIEW_PROMPT = """你负责检查同一候选人的多个维度评估是否存在跨维度逻辑冲突。
只检查具有明确证据的矛盾，不得把材料缺失或表达粒度差异判定为冲突。
每个问题必须至少关联两个维度，candidate_id 必须复制输入中的候选人编号。
输入中的证据使用 E1、E2 这类短编号。evidence_refs 只能逐字复制输入中的短编号，不得缩写或改写。
dimension_numbers 记录发生冲突的维度序号，message 说明互相矛盾的已评分结论或事实。
当没有冲突时返回空 issues。
""".strip()


def build_structured_consistency_reviewer(
        model_provider: ModelProvider,
) -> ConsistencyReviewer:
    """构造结构化一致性检查器：让模型发现跨维度逻辑冲突。"""

    def review(
            assessments: list[DimensionAssessment],
            evidence_items: list[EvidenceItem],
    ) -> ConsistencyReview:
        """
        构造结构化一致性检查器：让模型发现跨维度逻辑冲突。
        :param assessments: 待检查的维度评估列表
        :param evidence_items: 证据项列表
        :return: 一致性检查结果
        """
        model = model_provider()
        if model is None:
            raise RuntimeError("一致性检查模型未配置")
        safe_evidence, chunk_to_model, model_to_chunk = _model_evidence_payload(
            evidence_items
        )
        assessment_payloads = []
        for assessment in assessments:
            # 将维度评估中的证据引用替换为短编号
            assessment_payload = assessment.model_dump(mode="json")
            assessment_payload["evidence_refs"] = [
                chunk_to_model[ref_id]
                for ref_id in assessment.evidence_refs
            ]
            assessment_payloads.append(assessment_payload)
        payload = {
            "dimension_assessments": assessment_payloads,
            "evidence_items": safe_evidence,
        }
        # 调用模型检查跨维度一致性
        result = model.with_structured_output(ConsistencyReview).invoke(
            [
                ("system", CONSISTENCY_REVIEW_PROMPT),
                ("user", json.dumps(payload, ensure_ascii=False)),
            ]
        )
        review = ConsistencyReview.model_validate(result)
        # 还原各一致性问题中的证据短编号
        return review.model_copy(
            update={
                "issues": [
                    issue.model_copy(
                        update={
                            "evidence_refs": _restore_evidence_refs(
                                issue.evidence_refs,
                                model_to_chunk,
                            )
                        }
                    )
                    for issue in review.issues
                ]
            }
        )

    return review


def validate_dimension_judgement(
        branch: BranchEvidenceDraft,
        judgement: DimensionJudgement,
) -> None:
    """
    校验收敛的评分判断：引用范围、状态-分数配对、缺失/冲突必填等。
    """
    unavailable_refs = sorted(
        set(judgement.evidence_refs) - set(branch_evidence_refs(branch))
    )
    if unavailable_refs:
        raise ValueError(f"评分结果引用了未提供的证据: {', '.join(unavailable_refs)}")
    # 不可评分状态不允许设置分数
    if judgement.evidence_status in {"missing", "conflicting"} and judgement.score is not None:
        raise ValueError(f"{judgement.evidence_status} 状态不允许设置分数")
    # 可评分状态必须提供分数
    if judgement.evidence_status in {"sufficient", "partial"} and judgement.score is None:
        raise ValueError(f"{judgement.evidence_status} 状态必须设置分数")
    # 除 missing 外都必须保留证据引用
    if judgement.evidence_status in {"sufficient", "partial", "conflicting"} and not judgement.evidence_refs:
        raise ValueError(f"{judgement.evidence_status} 状态必须保留证据引用")
    if judgement.evidence_status == "missing" and not judgement.missing_items:
        raise ValueError("missing 状态必须说明缺失信息")
    if judgement.evidence_status == "conflicting" and not judgement.conflicts:
        raise ValueError("conflicting 状态必须说明冲突内容")


def aggregate_candidate_assessments(
        dimensions: list[EvaluationDimension],
        assessments: list[DimensionAssessment],
        consistency_issues: list[ConsistencyIssue] | None = None,
) -> list[CandidateAssessment]:
    """按加权累计计算每位候选人的已确认/理论上限得分与覆盖率，并确定置信度。"""
    issues_by_candidate: dict[str, list[ConsistencyIssue]] = {}
    for issue in consistency_issues or []:
        issues_by_candidate.setdefault(issue.candidate_id, []).append(issue)
    grouped: dict[str, list[DimensionAssessment]] = {}
    for assessment in assessments:
        grouped.setdefault(assessment.candidate_id, []).append(assessment)

    candidates: list[CandidateAssessment] = []
    for candidate_id, items in grouped.items():
        # 按维度序号对齐各维度评估
        items_by_dimension = {item.dimension_number: item for item in items}
        confirmed_score = 0.0
        possible_score = 0.0
        evidence_coverage = 0
        ordered_items: list[DimensionAssessment] = []
        for dimension_number, dimension in enumerate(dimensions, start=1):
            assessment = items_by_dimension[dimension_number]
            ordered_items.append(assessment)
            # 未评分维度：只能计入理论上限，不计覆盖率
            if assessment.score is None:
                possible_score += dimension.weight_percent
                continue
            # 已评分维度：按权重 × (分数/5) 累计确认分
            contribution = dimension.weight_percent * assessment.score / 5
            confirmed_score += contribution
            evidence_coverage += dimension.weight_percent
            possible_score += contribution

        # 结合一致性冲突、状态集合与覆盖率推导置信度
        statuses = {item.evidence_status for item in ordered_items}
        candidate_issues = issues_by_candidate.get(candidate_id, [])
        if candidate_issues:
            confidence: Literal["high", "medium", "low"] = "low"
        elif statuses == {"sufficient"}:
            confidence: Literal["high", "medium", "low"] = "high"
        elif evidence_coverage >= 70 and not statuses.intersection({"conflicting", "failed"}):
            confidence = "medium"
        else:
            confidence = "low"
        candidates.append(
            CandidateAssessment(
                candidate_id=candidate_id,
                confirmed_score=round(confirmed_score, 2),
                possible_score=round(possible_score, 2),
                evidence_coverage_percent=evidence_coverage,
                confidence=confidence,
                dimensions=ordered_items,
                consistency_issues=candidate_issues,
            )
        )
    return candidates


def rank_candidate_assessments(
        candidates: list[CandidateAssessment],
) -> list[CandidateAssessment]:
    """按（已确认得分降序、覆盖率降序、工号升序）对候选人排序。"""
    return sorted(
        candidates,
        key=lambda item: (
            -item.confirmed_score,
            -item.evidence_coverage_percent,
            item.candidate_id,
        ),
    )


def _receive_input(state: TalentEvaluationReportState) -> dict[str, Any]:
    """
    接收并校验报告输入：解析维度、分支与证据，并校验任务矩阵完整性。
    """
    dimensions = [EvaluationDimension.model_validate(item) for item in state.get("dimensions", [])]
    branches = [BranchEvidenceDraft.model_validate(item) for item in state.get("branch_results", [])]
    evidence_items = evidence_items_from_branches(branches)
    _log_node(
        "receive_input",
        "input",
        {
            "dimension_numbers": list(range(1, len(dimensions) + 1)),
            "branch_results": [item.task_id for item in branches],
            "evidence_items": [
                {"model_ref": item.model_ref, "ref_id": item.ref_id}
                for item in evidence_items
            ],
        },
    )
    if not dimensions:
        raise ValueError("dimensions 不能为空")
    if not branches:
        raise ValueError("branch_results 不能为空")

    # 建立维度序号、候选人与证据的查找索引
    dimensions_by_number = {
        dimension_number: dimension
        for dimension_number, dimension in enumerate(dimensions, start=1)
    }
    evidence_by_id = {item.ref_id: item for item in evidence_items}
    task_ids: set[str] = set()
    candidate_ids: set[str] = set()
    for branch in branches:
        # 校验任务标识唯一性、维度归属与证据归属一致性
        if branch.task_id in task_ids:
            raise ValueError(f"重复的评估任务: {branch.task_id}")
        task_ids.add(branch.task_id)
        candidate_ids.add(branch.candidate_id)
        if branch.dimension_number not in dimensions_by_number:
            raise ValueError(f"未知评估维度: {branch.dimension_number}")
        expected_task_id = f"{branch.candidate_id}:{branch.dimension_number}"
        if branch.task_id != expected_task_id:
            raise ValueError(
                f"评估任务标识不匹配: {branch.task_id}，应为 {expected_task_id}"
            )
        for ref_id in branch_evidence_refs(branch):
            evidence = evidence_by_id.get(ref_id)
            if evidence is None:
                raise ValueError(f"分支引用的证据不存在: {ref_id}")
            if evidence.candidate_id != branch.candidate_id:
                raise ValueError(f"分支引用了其他候选人的证据: {ref_id}")

    # 校验“候选人 × 维度”任务矩阵是否完整
    expected_task_ids = {
        f"{candidate_id}:{dimension_number}"
        for candidate_id in candidate_ids
        for dimension_number in dimensions_by_number
    }
    missing_task_ids = sorted(expected_task_ids - task_ids)
    if missing_task_ids:
        raise ValueError(f"评估任务矩阵不完整: {', '.join(missing_task_ids)}")
    _log_node(
        "receive_input",
        "output",
        {
            "status": "input_validated",
            "candidate_ids": sorted(candidate_ids),
            "task_count": len(task_ids),
        },
    )
    return {
        "evidence_items": [item.model_dump(mode="json") for item in evidence_items],
        "dimension_assessments": [],
        "consistency_issues": [],
        "candidate_assessments": [],
        "ranking": [],
        "report_draft": {},
        "report": "",
        "status": "input_validated",
        "errors": [],
    }


def _score_dimensions(
        dimension_scorer: DimensionScorer,
        *,
        max_concurrency: int = 12,
):
    """
    构造“维度评分”节点：并行对每个取证分支做结构化评分。
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency 必须大于 0")

    def score_dimensions(state: TalentEvaluationReportState) -> dict[str, Any]:
        """
        维度评分节点：并行对每个取证分支做结构化评分。
        """
        dimensions = {
            dimension_number: EvaluationDimension.model_validate(raw)
            for dimension_number, raw in enumerate(state["dimensions"], start=1)
        }
        evidence_by_id = {
            item.ref_id: item
            for item in (
                EvidenceItem.model_validate(raw) for raw in state.get("evidence_items", [])
            )
        }
        _log_node(
            "score_dimensions",
            "input",
            {
                "branch_results": [
                    BranchEvidenceDraft.model_validate(raw).task_id
                    for raw in state["branch_results"]
                ],
                "max_concurrency": max_concurrency,
            },
        )

        # 定义评分函数
        def score_branch(branch: BranchEvidenceDraft) -> DimensionAssessment:
            """
            对单个取证分支做结构化评分。
            :param branch: 待评分的取证分支
            :return: 评分结果
            """
            # 分支执行失败：直接生成 failed 评估
            if branch.execution_status == "failed":
                return DimensionAssessment(
                    task_id=branch.task_id,
                    candidate_id=branch.candidate_id,
                    dimension_number=branch.dimension_number,
                    evidence_status="failed",
                    score=None,
                    confidence="unavailable",
                    evidence_refs=[],
                    missing_items=[branch.error_message or "评估分支执行失败"],
                    conflicts=[],
                    rationale=branch.error_message or "评估分支执行失败",
                )
            evidence_refs = branch_evidence_refs(branch)
            missing_information = branch_missing_information(branch)
            # 无可用证据：返回 missing
            if not evidence_refs:
                missing_items = missing_information or ["未检索到可用证据"]
                return DimensionAssessment(
                    task_id=branch.task_id,
                    candidate_id=branch.candidate_id,
                    dimension_number=branch.dimension_number,
                    evidence_status="missing",
                    score=None,
                    confidence="unavailable",
                    evidence_refs=[],
                    missing_items=missing_items,
                    conflicts=[],
                    rationale="当前材料无法支持该维度评分",
                )

            branch_evidence = [evidence_by_id[ref_id] for ref_id in evidence_refs]
            # 调用模型评分并校验结果合法性
            judgement = dimension_scorer(
                dimensions[branch.dimension_number],
                branch,
                branch_evidence,
            )

            validate_dimension_judgement(branch, judgement)
            _log_node(
                "score_dimensions",
                "scoring",
                {
                    "task_id": branch.task_id,
                    "branch_status": branch.execution_status,
                    "evidence_refs": evidence_refs,
                    "judgement": judgement.model_dump(mode="json"),
                },
            )
            return DimensionAssessment(
                task_id=branch.task_id,
                candidate_id=branch.candidate_id,
                dimension_number=branch.dimension_number,
                evidence_status=judgement.evidence_status,
                score=judgement.score,
                confidence=judgement.confidence,
                evidence_refs=judgement.evidence_refs,
                missing_items=list(
                    dict.fromkeys(missing_information + judgement.missing_items)
                ),
                conflicts=judgement.conflicts,
                rationale=judgement.rationale,
            )

        branches = [
            BranchEvidenceDraft.model_validate(raw)
            for raw in state["branch_results"]
        ]
        # 并行对所有分支执行评分
        worker_count = min(max_concurrency, len(branches))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            assessments = list(executor.map(score_branch, branches))
        _log_node(
            "score_dimensions",
            "output",
            {
                "status": "dimensions_scored",
                "assessments": [
                    {
                        "task_id": item.task_id,
                        "evidence_status": item.evidence_status,
                        "score": item.score,
                        "confidence": item.confidence,
                    }
                    for item in assessments
                ],
            },
        )
        return {
            "dimension_assessments": [item.model_dump(mode="json") for item in assessments],
            "status": "dimensions_scored",
        }

    return score_dimensions


def validate_consistency_review(
        review: ConsistencyReview,
        assessments: list[DimensionAssessment],
        evidence_items: list[EvidenceItem],
) -> None:
    """
    校验一致性审查结果：维度和证据必须属于对应候选人的已核验范围。
    """
    # 每位候选人拥有（已评估的维度集合、已引用证据集合）
    dimensions_by_candidate = {
        candidate_id: {
            item.dimension_number
            for item in assessments
            if item.candidate_id == candidate_id
        }
        for candidate_id in {item.candidate_id for item in assessments}
    }
    refs_by_candidate = {
        candidate_id: {
            ref_id
            for item in assessments
            if item.candidate_id == candidate_id
            for ref_id in item.evidence_refs
        }
        for candidate_id in dimensions_by_candidate
    }
    evidence_by_id = {item.ref_id: item for item in evidence_items}
    for issue in review.issues:
        # 冲突关联的维度必须全部属于该候选人已评估维度
        allowed_dimensions = dimensions_by_candidate.get(issue.candidate_id)
        if allowed_dimensions is None or not set(issue.dimension_numbers).issubset(allowed_dimensions):
            raise ValueError(f"一致性问题引用了未知维度: {issue.dimension_numbers}")
        # 冲突引用的证据必须属于该候选人且已被核验
        allowed_refs = refs_by_candidate[issue.candidate_id]
        for ref_id in issue.evidence_refs:
            evidence = evidence_by_id.get(ref_id)
            if (
                    ref_id not in allowed_refs
                    or evidence is None
                    or evidence.candidate_id != issue.candidate_id
            ):
                raise ValueError(f"一致性问题引用了未核验证据: {ref_id}")


def _review_consistency(
        consistency_reviewer: ConsistencyReviewer,
        *,
        max_concurrency: int = 12,
):
    """
    构造“一致性审查”节点：按候选人分组并行检查跨维度冲突。
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency 必须大于 0")

    def review_consistency(state: TalentEvaluationReportState) -> dict[str, Any]:
        """
        一致性审查节点：按候选人分组并行检查跨维度冲突。
        """
        assessments = [
            DimensionAssessment.model_validate(item)
            for item in state["dimension_assessments"]
        ]
        evidence_items = [
            EvidenceItem.model_validate(item) for item in state.get("evidence_items", [])
        ]
        _log_node(
            "review_consistency",
            "input",
            {
                "dimension_assessments": [
                    item.task_id for item in assessments
                ],
                "evidence_items": [item.ref_id for item in evidence_items],
                "max_concurrency": max_concurrency,
            },
        )
        # 将评估与证据分别按候选人分组
        assessments_by_candidate: dict[str, list[DimensionAssessment]] = {}
        for assessment in assessments:
            assessments_by_candidate.setdefault(
                assessment.candidate_id,
                [],
            ).append(assessment)
        evidence_by_candidate: dict[str, list[EvidenceItem]] = {}
        for evidence in evidence_items:
            evidence_by_candidate.setdefault(evidence.candidate_id, []).append(evidence)

        def review_candidate(
                item: tuple[str, list[DimensionAssessment]],
        ) -> list[ConsistencyIssue]:
            # 对单个候选人的多维度评估做一致性检查并校验
            """对单个候选人做跨维度一致性检查。"""
            candidate_id, candidate_assessments = item
            review = ConsistencyReview.model_validate(
                consistency_reviewer(
                    candidate_assessments,
                    evidence_by_candidate.get(candidate_id, []),
                )
            )
            validate_consistency_review(
                review,
                candidate_assessments,
                evidence_by_candidate.get(candidate_id, []),
            )
            return review.issues

        # 并行检查各候选人
        candidate_groups = list(assessments_by_candidate.items())
        worker_count = min(max_concurrency, len(candidate_groups))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            issues_by_candidate = list(
                executor.map(review_candidate, candidate_groups)
            )
        # 合并所有候选人的一致性问题
        review = ConsistencyReview(
            issues=[
                issue
                for candidate_issues in issues_by_candidate
                for issue in candidate_issues
            ]
        )
        validate_consistency_review(review, assessments, evidence_items)
        _log_node(
            "review_consistency",
            "output",
            {
                "status": "consistency_reviewed",
                "issues": [issue.model_dump(mode="json") for issue in review.issues],
            },
        )
        return {
            "consistency_issues": [
                item.model_dump(mode="json") for item in review.issues
            ],
            "status": "consistency_reviewed",
        }

    return review_consistency


def _aggregate_candidates(state: TalentEvaluationReportState) -> dict[str, Any]:
    """“聚合候选人”节点：把维度评分聚合成候选人评估并排名。"""
    dimensions = [EvaluationDimension.model_validate(item) for item in state["dimensions"]]
    assessments = [
        DimensionAssessment.model_validate(item) for item in state["dimension_assessments"]
    ]
    consistency_issues = [
        ConsistencyIssue.model_validate(item)
        for item in state.get("consistency_issues", [])
    ]
    _log_node(
        "aggregate_candidates",
        "input",
        {
            "dimensions": [
                (dimension_number, item.weight_percent)
                for dimension_number, item in enumerate(dimensions, start=1)
            ],
            "dimension_assessments": [item.task_id for item in assessments],
            "consistency_issues": [item.message for item in consistency_issues],
        },
    )
    ranked = rank_candidate_assessments(
        aggregate_candidate_assessments(dimensions, assessments, consistency_issues)
    )  # 聚合并按综合得分排名
    _log_node(
        "aggregate_candidates",
        "output",
        {
            "status": "candidates_ranked",
            "ranking": [item.candidate_id for item in ranked],
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "confirmed_score": item.confirmed_score,
                    "possible_score": item.possible_score,
                    "evidence_coverage_percent": item.evidence_coverage_percent,
                    "confidence": item.confidence,
                }
                for item in ranked
            ],
        },
    )
    return {
        "candidate_assessments": [item.model_dump(mode="json") for item in ranked],
        "ranking": [item.candidate_id for item in ranked],
        "status": "candidates_ranked",
    }


def _compose_report(report_writer: ReportWriter):
    """构造“撰写报告”节点：调用报告撰写器生成报告草稿。"""

    def compose_report(state: TalentEvaluationReportState) -> dict[str, Any]:
        """聚合评分、排序并渲染报告。"""
        candidates = [
            CandidateAssessment.model_validate(item)
            for item in state["candidate_assessments"]
        ]
        evidence_items = [
            EvidenceItem.model_validate(item) for item in state.get("evidence_items", [])
        ]
        _log_node(
            "compose_report",
            "input",
            {
                "ranking": [item.candidate_id for item in candidates],
                "evidence_items": [item.ref_id for item in evidence_items],
            },
        )
        draft = report_writer(candidates, evidence_items)
        _log_node(
            "compose_report",
            "output",
            {
                "status": "report_drafted",
                "title": draft.title,
                "candidates": [item.candidate_id for item in draft.candidates],
            },
        )
        return {
            "report_draft": ReportDraft.model_validate(draft).model_dump(mode="json"),
            "status": "report_drafted",
        }

    return compose_report


def validate_report_draft(
        draft: ReportDraft,
        candidates: list[CandidateAssessment],
        evidence_items: list[EvidenceItem],
) -> None:
    """校验报告草稿：候选人顺序一致、证据属于对应候选人且已核验。"""
    expected_candidates = [item.candidate_id for item in candidates]
    actual_candidates = [item.candidate_id for item in draft.candidates]
    if actual_candidates != expected_candidates:
        raise ValueError("报告候选人顺序必须与程序排名一致")

    evidence_by_id = {item.ref_id: item for item in evidence_items}
    # 每位候选人被赋权保留的证据集合
    refs_by_candidate = {
        candidate.candidate_id: {
            ref_id
            for dimension in candidate.dimensions
            for ref_id in dimension.evidence_refs
        }
        for candidate in candidates
    }
    for candidate in draft.candidates:
        for point in candidate.highlights:
            for ref_id in point.evidence_refs:
                # 报告引用的证据必须已存在且属于该候选人已核验范围
                if ref_id not in evidence_by_id or ref_id not in refs_by_candidate[candidate.candidate_id]:
                    raise ValueError(f"报告引用了未核验的证据: {ref_id}")
                if evidence_by_id[ref_id].candidate_id != candidate.candidate_id:
                    raise ValueError(f"报告引用了其他候选人的证据: {ref_id}")


def render_report_markdown(
        draft: ReportDraft,
        candidates: list[CandidateAssessment],
        evidence_items: list[EvidenceItem],
) -> str:
    """将报告草稿渲染为 Markdown，并把证据引用转成可点击回引链接。"""
    validate_report_draft(draft, candidates, evidence_items)
    evidence_by_id = {item.ref_id: item for item in evidence_items}
    candidate_by_id = {item.candidate_id: item for item in candidates}
    lines = [f"# {draft.title}", "", draft.overview]
    for rank, candidate_draft in enumerate(draft.candidates, start=1):
        candidate = candidate_by_id[candidate_draft.candidate_id]
        # 输出每位候选人的标题、得分概要与综合结论
        lines.extend(
            [
                "",
                f"## {rank}. {candidate.candidate_id}",
                "",
                (
                    f"已确认得分 **{candidate.confirmed_score:.1f}** / 100，"
                    f"理论上限 **{candidate.possible_score:.1f}** / 100，"
                    f"可评分覆盖率 **{candidate.evidence_coverage_percent}%**"
                ),
                "",
                candidate_draft.summary,
            ]
        )
        if candidate_draft.highlights:
            # 逐条渲染主要依据，证据编号转为可点击链接
            lines.extend(["", "### 主要依据", ""])
            for point in candidate_draft.highlights:
                citations = " ".join(
                    (
                        f"[{evidence_by_id[ref_id].model_ref or ref_id}]"
                        f"({evidence_by_id[ref_id].citation_url})"
                    )
                    for ref_id in point.evidence_refs
                )
                lines.append(f"- {point.text} {citations}")
        if candidate_draft.risks:
            lines.extend(["", "### 风险与缺失", ""])
            lines.extend(f"- {item}" for item in candidate_draft.risks)
    return "\n".join(lines).strip()


def _validate_and_render_report(state: TalentEvaluationReportState) -> dict[str, Any]:
    """“渲染报告”节点：校验草稿并渲染为最终 Markdown。"""
    draft = ReportDraft.model_validate(state["report_draft"])
    candidates = [
        CandidateAssessment.model_validate(item) for item in state["candidate_assessments"]
    ]
    evidence_items = [
        EvidenceItem.model_validate(item) for item in state.get("evidence_items", [])
    ]
    _log_node(
        "validate_report",
        "input",
        {
            "report_draft": state["report_draft"].get("title"),
            "candidates": [item.candidate_id for item in candidates],
        },
    )
    report = render_report_markdown(draft, candidates, evidence_items)
    _log_node(
        "validate_report",
        "output",
        {
            "status": "completed",
            "report_chars": len(report),
        },
    )
    return {
        "report": report,
        "status": "completed",
    }


def build_talent_evaluation_report_graph(
        *,
        dimension_scorer: DimensionScorer,
        consistency_reviewer: ConsistencyReviewer | None = None,
        report_writer: ReportWriter,
        max_scoring_concurrency: int = 12,
        max_consistency_concurrency: int = 12,
):
    """装配报告生成子图：评分 → 一致性审查 → 聚合 → 撰写 → 渲染。"""
    # 未提供一致性检查器时退化为空审查
    reviewer = consistency_reviewer or (
        lambda assessments, evidence_items: ConsistencyReview()
    )
    builder = StateGraph(TalentEvaluationReportState)
    builder.add_node("receive_input", _receive_input) # 接收输入
    builder.add_node("score_dimensions",_score_dimensions(dimension_scorer, max_concurrency=max_scoring_concurrency, ), ) # 评分维度
    builder.add_node("review_consistency",_review_consistency(reviewer, max_concurrency=max_consistency_concurrency, ), ) # 一致性审查
    builder.add_node("aggregate_candidates", _aggregate_candidates) # 聚合候选人
    builder.add_node("compose_report", _compose_report(report_writer)) # 撰写报告
    builder.add_node("validate_report", _validate_and_render_report) # 校验渲染报告
    builder.add_edge(START, "receive_input") # 从开始节点到接收输入节点
    builder.add_edge("receive_input", "score_dimensions") # 从接收输入节点到评分维度节点
    builder.add_edge("score_dimensions", "review_consistency") # 从评分维度节点到一致性审查节点
    builder.add_edge("review_consistency", "aggregate_candidates") # 从一致性审查节点到聚合候选人节点
    builder.add_edge("aggregate_candidates", "compose_report") # 从聚合候选人节点到撰写报告节点
    builder.add_edge("compose_report", "validate_report") # 从撰写报告节点到校验渲染报告节点
    builder.add_edge("validate_report", END) # 从校验渲染报告节点到结束节点
    return builder.compile()
