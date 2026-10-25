import React, { useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  FileText,
  ListChecks,
  Route,
  ScrollText,
  Users,
} from "lucide-react";
import {
  answerLabel,
  documentTypeLabel,
  FIELD_LABELS,
  formatFilterValue,
  formatScore,
  metaString,
  OPERATOR_LABELS,
  packStatusLabel,
  permissionLabel,
  TASK_TYPE_LABELS,
} from "../format";
import type {
  CandidateProfile,
  EvidencePack,
  EvidencePackRequirement,
  TalentChunk,
  TalentSearchResponse,
} from "../types";

type TabId = "plan" | "candidates" | "trace" | "evidence" | "packs";

type RecommendationResultsProps = {
  result: TalentSearchResponse;
  profiles: Record<string, CandidateProfile>;
  onOpenCitation: (chunkId: string, quoteStart?: number | null, quoteEnd?: number | null) => void;
};

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: "plan", label: "查询计划", icon: <ListChecks /> },
  { id: "candidates", label: "候选人", icon: <Users /> },
  { id: "trace", label: "检索轨迹", icon: <Route /> },
  { id: "evidence", label: "命中证据", icon: <FileText /> },
  { id: "packs", label: "证据包", icon: <ScrollText /> },
];

function candidateName(chunk: TalentChunk, profiles: Record<string, CandidateProfile>) {
  return profiles[chunk.candidate_id]?.name || chunk.candidate_id;
}

function ChunkCard({
  chunk,
  rank,
  profiles,
  onOpenCitation,
}: {
  chunk: TalentChunk;
  rank: number;
  profiles: Record<string, CandidateProfile>;
  onOpenCitation: (chunkId: string, quoteStart?: number | null, quoteEnd?: number | null) => void;
}) {
  const documentType = metaString(chunk.metadata, "document_type");
  const permission = metaString(chunk.metadata, "permission_scope");
  const pageStart = metaString(chunk.metadata, "page_start");
  const pageEnd = metaString(chunk.metadata, "page_end");

  return (
    <article className="hitCard">
      <header className="hitHead">
        <span className="rankBadge">#{rank}</span>
        <b>{candidateName(chunk, profiles)}</b>
        <span className="scoreBadge">Score {formatScore(chunk.score)}</span>
      </header>
      <div className="hitMeta">
        <span>{candidateTypeLabel(documentType)}</span>
        <span>{permissionLabel(permission)}</span>
        {pageStart && <span>第 {pageStart}{pageEnd && pageEnd !== pageStart ? `-${pageEnd}` : ""} 页</span>}
        {chunk.requirement_ids.map((id) => (
          <span className="requirementChip" key={id}>
            {id}
          </span>
        ))}
      </div>
      <p className="hitContent">{chunk.content}</p>
      <footer className="hitFooter">
        <code>{chunk.chunk_id}</code>
        <button className="ghost small" onClick={() => onOpenCitation(chunk.chunk_id)}>
          查看引用
        </button>
      </footer>
    </article>
  );
}

function candidateTypeLabel(value: string) {
  return value ? documentTypeLabel(value) : "未标注类型";
}

function RequirementBlock({
  requirement,
  onOpenCitation,
}: {
  requirement: EvidencePackRequirement;
  onOpenCitation: (chunkId: string, quoteStart?: number | null, quoteEnd?: number | null) => void;
}) {
  return (
    <article className={`packRequirement pack-${requirement.status}`}>
      <header className="packRequirementHead">
        <b>{requirement.requirement_id}</b>
        <span>{requirement.query}</span>
        <em>{packStatusLabel(requirement.status)}</em>
      </header>
      {requirement.extraction_status === "failed" && (
        <p className="hint">抽取失败，保留原始材料，可手工核查引用。</p>
      )}
      {requirement.facts.length > 0 && (
        <div className="factList">
          {requirement.facts.map((fact, index) => (
            <div className="fact" key={`${requirement.requirement_id}-${index}`}>
              <p>
                <b>{fact.event || "未记录事件"}</b>
                {fact.period ? ` · ${fact.period}` : ""} · {answerLabel(fact.answer)} {fact.claim}
              </p>
              <div className="citationButtons">
                {fact.sources.map((source, sourceIndex) => (
                  <button
                    className="ghost small"
                    key={`${source.citation_id}-${sourceIndex}`}
                    onClick={() =>
                      onOpenCitation(source.chunk_id || source.citation_id, source.quote_start, source.quote_end)
                    }
                  >
                    原文：{source.quote}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {requirement.conflicts.length > 0 && (
        <p className="hint warn">
          待核查冲突：事实 {requirement.conflicts.map((pair) => pair.map((n) => n + 1).join(" 与 ")).join("；")}
        </p>
      )}
      {requirement.missing_information.length > 0 && (
        <p className="hint">待补充：{requirement.missing_information.join("、")}</p>
      )}
      {requirement.citations.length > 0 && (
        <details className="citationDetails">
          <summary>本轮原始证据（{requirement.citations.length}）</summary>
          <div className="citationButtons">
            {requirement.citations.map((citation) => (
              <button
                className="ghost small"
                key={citation.citation_id}
                onClick={() => onOpenCitation(citation.chunk_id || citation.citation_id)}
              >
                {citation.document_title || citation.source_label || citation.citation_id}
                {citation.version_no ? ` · v${citation.version_no}` : ""}
              </button>
            ))}
          </div>
        </details>
      )}
    </article>
  );
}

function PackCard({
  pack,
  profiles,
  onOpenCitation,
}: {
  pack: EvidencePack;
  profiles: Record<string, CandidateProfile>;
  onOpenCitation: (chunkId: string, quoteStart?: number | null, quoteEnd?: number | null) => void;
}) {
  const profile = profiles[pack.candidate_id];
  return (
    <section className="packCard">
      <header className="packHead">
        <div>
          <b>{profile?.name || pack.candidate_id}</b>
          <small>
            {pack.candidate_id}
            {profile?.department ? ` · ${profile.department}` : ""}
          </small>
        </div>
        <span className="panelBadge">{pack.requirements.length} 项要求</span>
      </header>
      {pack.requirements.map((requirement) => (
        <RequirementBlock
          requirement={requirement}
          key={`${pack.candidate_id}-${requirement.requirement_id}`}
          onOpenCitation={onOpenCitation}
        />
      ))}
    </section>
  );
}

export function RecommendationResults({ result, profiles, onOpenCitation }: RecommendationResultsProps) {
  const [tab, setTab] = useState<TabId>("plan");
  const plan = result.query_plan;
  const packs = result.evidence_packs ?? [];
  const counts = {
    plan: plan.filters.length + plan.semantic_requirements.length,
    candidates: result.candidate_ids.length,
    trace: result.searches.length,
    evidence: result.chunks.length,
    packs: packs.length,
  };

  return (
    <section className="resultsPanel">
      <div className="summaryStrip">
        <span>
          任务类型 <b>{TASK_TYPE_LABELS[plan.task_type] || plan.task_type}</b>
        </span>
        <span>
          候选人 <b>{result.candidate_ids.length}</b>
        </span>
        <span>
          命中片段 <b>{result.chunks.length}</b>
        </span>
        <span>
          证据状态 <b>{result.evidence_status || "not_requested"}</b>
        </span>
      </div>

      <div className="resultTabs">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={tab === item.id ? "active" : ""}
            onClick={() => setTab(item.id)}
          >
            {item.icon}
            {item.label}
            <em>{counts[item.id]}</em>
          </button>
        ))}
      </div>

      {tab === "plan" && (
        <div className="tabBody">
          {plan.clarifications.length > 0 && (
            <div className="inlineAlert" role="alert">
              <AlertTriangle />
              <div>
                <b>需要补充或澄清条件</b>
                {plan.clarifications.map((item, index) => (
                  <p key={`${item.expression}-${index}`}>
                    {item.expression}：{item.reason}
                  </p>
                ))}
              </div>
            </div>
          )}
          <h3>结构化过滤</h3>
          {plan.filters.length === 0 ? (
            <p className="secondaryText">没有结构化硬条件。</p>
          ) : (
            <div className="chipRow">
              {plan.filters.map((filter, index) => (
                <span className="chip" key={`${filter.field}-${index}`}>
                  {FIELD_LABELS[filter.field] || filter.field} {OPERATOR_LABELS[filter.operator] || filter.operator}{" "}
                  {formatFilterValue(filter.value)}
                </span>
              ))}
            </div>
          )}

          <h3>语义要求</h3>
          {plan.semantic_requirements.length === 0 ? (
            <p className="secondaryText">没有语义检索要求。</p>
          ) : (
            <div className="requirementList">
              {plan.semantic_requirements.map((requirement) => (
                <article className="requirementItem" key={requirement.requirement_id}>
                  <b>{requirement.requirement_id}</b>
                  <span>{requirement.query}</span>
                  <em>{requirement.required ? "必须" : "可选"}</em>
                </article>
              ))}
            </div>
          )}

          {plan.preferences.length > 0 && (
            <>
              <h3>偏好</h3>
              <div className="chipRow">
                {plan.preferences.map((item, index) => (
                  <span className="chip" key={`${item}-${index}`}>
                    {item}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {tab === "candidates" && (
        <div className="tabBody">
          {result.candidate_ids.length === 0 ? (
            <div className="empty compact">
              <Users />
              <h3>没有候选人</h3>
              <p>当前结构化条件下没有命中候选人，可以换一种需求描述再试。</p>
            </div>
          ) : (
            <div className="candidateGrid">
              {result.candidate_ids.map((candidateId) => {
                const profile = profiles[candidateId];
                return (
                  <article className="candidateCard" key={candidateId}>
                    <span className="candidateAvatar">{(profile?.name || candidateId).slice(-2)}</span>
                    <div>
                      <b>{profile?.name || "未找到员工档案"}</b>
                      <small>{candidateId}</small>
                      <p>
                        {[profile?.department, profile?.current_position, profile?.region]
                          .filter(Boolean)
                          .join(" · ") || "暂无结构化画像"}
                      </p>
                      <em>
                        {profile?.job_level ? `${profile.job_level} · ` : ""}
                        {profile?.years_of_experience != null ? `${profile.years_of_experience} 年经验` : "年限未记录"}
                      </em>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      )}

      {tab === "trace" && (
        <div className="tabBody">
          {result.searches.length === 0 ? (
            <p className="secondaryText">没有检索轨迹。</p>
          ) : (
            <div className="traceList">
              <div className="traceRow traceHead">
                <span>要求</span>
                <span>策略</span>
                <span>检索查询</span>
                <span>命中</span>
              </div>
              {result.searches.map((trace) => (
                <div className="traceRow" key={trace.requirement_id}>
                  <span>{trace.requirement_id}</span>
                  <span>{trace.strategy || "标准"}</span>
                  <span className="traceQueries">
                    {trace.queries.map((query, index) => (
                      <em key={`${query}-${index}`}>{query}</em>
                    ))}
                  </span>
                  <span>{trace.chunk_count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "evidence" && (
        <div className="tabBody">
          {result.chunks.length === 0 ? (
            <div className="empty compact">
              <FileText />
              <h3>没有命中片段</h3>
              <p>可以调整需求描述、放宽结构化条件，或确认材料已完成索引。</p>
            </div>
          ) : (
            <div className="hitList">
              {result.chunks.map((chunk, index) => (
                <ChunkCard
                  key={chunk.chunk_id}
                  chunk={chunk}
                  rank={index + 1}
                  profiles={profiles}
                  onOpenCitation={onOpenCitation}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "packs" && (
        <div className="tabBody">
          {packs.length === 0 ? (
            <div className="empty compact">
              <BookOpen />
              <h3>没有证据包</h3>
              <p>
                {result.evidence_status === "no_candidates"
                  ? "当前条件下没有候选人，未生成证据包。"
                  : "本次请求未勾选「生成证据包」，或没有可整理的命中证据。"}
              </p>
            </div>
          ) : (
            <div className="packList">
              {packs.map((pack) => (
                <PackCard
                  key={pack.candidate_id}
                  pack={pack}
                  profiles={profiles}
                  onOpenCitation={onOpenCitation}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
