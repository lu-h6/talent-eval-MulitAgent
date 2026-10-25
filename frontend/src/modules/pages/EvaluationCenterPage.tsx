import React, { useState } from "react";
import {
  AlertTriangle,
  FlaskConical,
  GitCompare,
  LoaderCircle,
  Search,
  Timer,
} from "lucide-react";
import { apiRequest, friendlyErrorMessage } from "../api";
import { CitationDrawer } from "../components/CitationDrawer";
import {
  documentTypeLabel,
  formatDuration,
  formatScore,
  permissionLabel,
} from "../format";
import type { EvidenceHit } from "../types";

type TimedResult<T> =
  | { status: "success"; durationMs: number; data: T }
  | { status: "error"; durationMs: number; error: string };

type ColumnState = {
  hits: EvidenceHit[];
  durationMs: number | null;
  error: string;
  running: boolean;
  ran: boolean;
};

type CitationTarget = {
  chunkId: string;
  quoteStart?: number | null;
  quoteEnd?: number | null;
};

export function parseListInput(value: string): string[] | undefined {
  const items = value
    .split(/[,\n，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length > 0 ? Array.from(new Set(items)) : undefined;
}

export async function runTimedSearch<T>(
  operation: () => Promise<T>,
  now: () => number = () => performance.now(),
): Promise<TimedResult<T>> {
  const startedAt = now();
  try {
    const data = await operation();
    return { status: "success", durationMs: now() - startedAt, data };
  } catch (error) {
    return {
      status: "error",
      durationMs: now() - startedAt,
      error: friendlyErrorMessage(error),
    };
  }
}

const EMPTY_COLUMN: ColumnState = {
  hits: [],
  durationMs: null,
  error: "",
  running: false,
  ran: false,
};

function applyResult(previous: ColumnState, result: TimedResult<EvidenceHit[]>): ColumnState {
  if (result.status === "success") {
    return {
      hits: result.data,
      durationMs: result.durationMs,
      error: "",
      running: false,
      ran: true,
    };
  }
  return {
    ...previous,
    durationMs: result.durationMs,
    error: result.error,
    running: false,
    ran: true,
  };
}

function HitCard({
  hit,
  rank,
  onOpenCitation,
}: {
  hit: EvidenceHit;
  rank: number;
  onOpenCitation: (chunkId: string) => void;
}) {
  return (
    <article className="hitCard">
      <header className="hitHead">
        <span className="rankBadge">#{rank}</span>
        <b>{hit.candidate_name || hit.candidate_id}</b>
        <span className="scoreBadge">Score {formatScore(hit.score)}</span>
      </header>
      <div className="hitMeta">
        <span>{documentTypeLabel(hit.document_type)}</span>
        <span>{permissionLabel(hit.permission_scope)}</span>
        {hit.page_start != null && (
          <span>
            第 {hit.page_start}
            {hit.page_end != null && hit.page_end !== hit.page_start ? `-${hit.page_end}` : ""} 页
          </span>
        )}
      </div>
      <p className="hitTitle">{hit.document_title || "未关联材料标题"}</p>
      <p className="hitContent">{hit.content}</p>
      <footer className="hitFooter">
        <code>{hit.material_no || hit.chunk_id}</code>
        <button className="ghost small" onClick={() => onOpenCitation(hit.chunk_id)}>
          查看引用
        </button>
      </footer>
    </article>
  );
}

function ComparisonColumn({
  title,
  badge,
  state,
  onOpenCitation,
}: {
  title: string;
  badge: string;
  state: ColumnState;
  onOpenCitation: (chunkId: string) => void;
}) {
  return (
    <section className="panel compareColumn">
      <div className="panelHead">
        <div>
          <h2>{title}</h2>
          <p>{badge}</p>
        </div>
        <div className="compareStats">
          <span>
            <Timer />
            {formatDuration(state.durationMs)}
          </span>
          <span>{state.hits.length} 条命中</span>
        </div>
      </div>

      {state.running && (
        <div className="loadingRow" role="status">
          <LoaderCircle className="spin" />
          检索中…
        </div>
      )}

      {state.error && (
        <div className="inlineAlert compact" role="alert">
          <AlertTriangle />
          <div>
            <b>本列请求失败</b>
            <p>{state.error}</p>
            {state.hits.length > 0 && <p className="secondaryText">下方保留上一次成功结果。</p>}
          </div>
        </div>
      )}

      {!state.running && state.ran && state.hits.length === 0 && !state.error && (
        <div className="empty compact">没有命中证据</div>
      )}

      {state.hits.length > 0 && (
        <div className="compareHits">
          {state.hits.map((hit, index) => (
            <HitCard
              key={`${hit.chunk_id}-${index}`}
              hit={hit}
              rank={index + 1}
              onOpenCitation={onOpenCitation}
            />
          ))}
        </div>
      )}

      {!state.running && !state.ran && (
        <div className="empty compact">
          <FlaskConical />
          <h3>尚未执行</h3>
          <p>提交同一查询后，这里会展示本列的耗时、命中数和排序结果。</p>
        </div>
      )}
    </section>
  );
}

export function EvaluationCenterPage() {
  const [query, setQuery] = useState("");
  const [candidateIds, setCandidateIds] = useState("");
  const [documentTypes, setDocumentTypes] = useState("");
  const [limit, setLimit] = useState(10);
  const [ef, setEf] = useState(80);
  const [rrfK, setRrfK] = useState(60);
  const [rerankTopN, setRerankTopN] = useState(20);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [vector, setVector] = useState<ColumnState>(EMPTY_COLUMN);
  const [hybrid, setHybrid] = useState<ColumnState>(EMPTY_COLUMN);
  const [citation, setCitation] = useState<CitationTarget | null>(null);

  function validate() {
    if (!query.trim()) return "请输入检索查询";
    if (limit < 1 || limit > 100) return "limit 必须在 1–100 之间";
    if (ef < 10 || ef > 1000) return "ef 必须在 10–1000 之间";
    if (rrfK < 1 || rrfK > 1000) return "rrf_k 必须在 1–1000 之间";
    if (rerankTopN < 1 || rerankTopN > 100) return "rerank_top_n 必须在 1–100 之间";
    return "";
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = validate();
    if (message) {
      setError(message);
      return;
    }

    const candidates = parseListInput(candidateIds);
    const types = parseListInput(documentTypes);
    const baseBody = {
      query: query.trim(),
      limit,
      ef,
      ...(candidates ? { candidate_ids: candidates } : {}),
      ...(types ? { document_types: types } : {}),
    };

    setBusy(true);
    setError("");
    setVector((current) => ({ ...current, running: true, error: "" }));
    setHybrid((current) => ({ ...current, running: true, error: "" }));

    const vectorPromise = runTimedSearch(() =>
      apiRequest<EvidenceHit[]>("/evidence/search", {
        method: "POST",
        evidence: true,
        body: baseBody,
      }),
    ).then((result) => {
      setVector((current) => applyResult(current, result));
    });

    const hybridPromise = runTimedSearch(() =>
      apiRequest<EvidenceHit[]>("/evidence/hybrid-search", {
        method: "POST",
        evidence: true,
        body: {
          ...baseBody,
          rrf_k: rrfK,
          rerank_top_n: rerankTopN,
        },
      }),
    ).then((result) => {
      setHybrid((current) => applyResult(current, result));
    });

    await Promise.all([vectorPromise, hybridPromise]);
    setBusy(false);
  }

  return (
    <>
      <div className="pageHead">
        <div>
          <h1>评测中心</h1>
          <p>同一查询并行执行向量检索与混合检索，独立记录耗时、命中与失败。</p>
        </div>
        <div className="actions">
          <span className="pageBadge">
            <GitCompare />
            召回对比
          </span>
        </div>
      </div>

      <section className="panel formPanel">
        <form onSubmit={submit}>
          <label className="field full">
            <span>检索查询</span>
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              rows={3}
              placeholder="例如：负责推荐系统升级并带来显著收益"
              aria-label="检索查询"
            />
          </label>

          <div className="fieldGrid">
            <label className="field">
              <span>候选人工号</span>
              <input
                value={candidateIds}
                onChange={(event) => setCandidateIds(event.target.value)}
                placeholder="C001, C002（逗号或换行分隔）"
              />
            </label>
            <label className="field">
              <span>材料类型</span>
              <input
                value={documentTypes}
                onChange={(event) => setDocumentTypes(event.target.value)}
                placeholder="resume, project（可留空）"
              />
            </label>
            <label className="field">
              <span>limit</span>
              <input
                type="number"
                min={1}
                max={100}
                value={limit}
                onChange={(event) => setLimit(Number(event.target.value) || 1)}
              />
            </label>
            <label className="field">
              <span>ef</span>
              <input
                type="number"
                min={10}
                max={1000}
                value={ef}
                onChange={(event) => setEf(Number(event.target.value) || 10)}
              />
            </label>
            <label className="field">
              <span>rrf_k</span>
              <input
                type="number"
                min={1}
                max={1000}
                value={rrfK}
                onChange={(event) => setRrfK(Number(event.target.value) || 1)}
              />
            </label>
            <label className="field">
              <span>rerank_top_n</span>
              <input
                type="number"
                min={1}
                max={100}
                value={rerankTopN}
                onChange={(event) => setRerankTopN(Number(event.target.value) || 1)}
              />
            </label>
          </div>

          <p className="hint">
            向量检索只依赖 Embedding；混合检索还需要 Reranker。任一列失败不会清空另一列的结果。
          </p>

          <div className="formActions">
            <button className="primary" type="submit" disabled={busy}>
              {busy ? <LoaderCircle className="spin" /> : <Search />}
              {busy ? "对比中…" : "执行对比"}
            </button>
          </div>
        </form>

        {error && (
          <div className="inlineAlert" role="alert">
            <AlertTriangle />
            <div>
              <b>{error}</b>
            </div>
          </div>
        )}
      </section>

      <div className="comparisonGrid">
        <ComparisonColumn
          title="向量检索"
          badge="Dense · /evidence/search"
          state={vector}
          onOpenCitation={(chunkId) => setCitation({ chunkId })}
        />
        <ComparisonColumn
          title="混合检索"
          badge="Dense + BM25 + RRF + Rerank · /evidence/hybrid-search"
          state={hybrid}
          onOpenCitation={(chunkId) => setCitation({ chunkId })}
        />
      </div>

      {citation && (
        <CitationDrawer
          chunkId={citation.chunkId}
          quoteStart={citation.quoteStart}
          quoteEnd={citation.quoteEnd}
          onClose={() => setCitation(null)}
        />
      )}
    </>
  );
}
