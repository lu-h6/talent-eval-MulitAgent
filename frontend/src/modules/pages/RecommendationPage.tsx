import React, { useState } from "react";
import {
  AlertTriangle,
  FileSearch,
  LoaderCircle,
  Search,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";
import { apiRequest, clarificationItems } from "../api";
import { CitationDrawer } from "../components/CitationDrawer";
import { RecommendationResults } from "../components/RecommendationResults";
import type { CandidateProfile, CandidateProfilesResponse, TalentSearchResponse } from "../types";

type CitationTarget = {
  chunkId: string;
  quoteStart?: number | null;
  quoteEnd?: number | null;
};

type FormValues = {
  query: string;
  retrievalMode: "standard" | "auto_optimize";
  includeEvidence: boolean;
  limit: number;
  ef: number;
  rrfK: number;
  rerankTopN: number;
};

const DEFAULTS: FormValues = {
  query: "",
  retrievalMode: "standard",
  includeEvidence: true,
  limit: 10,
  ef: 80,
  rrfK: 60,
  rerankTopN: 20,
};

export function RecommendationPage() {
  const [form, setForm] = useState<FormValues>(DEFAULTS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [clarifications, setClarifications] = useState<{ expression: string; reason: string }[]>([]);
  const [result, setResult] = useState<TalentSearchResponse | null>(null);
  const [profiles, setProfiles] = useState<Record<string, CandidateProfile>>({});
  const [citation, setCitation] = useState<CitationTarget | null>(null);

  function update<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function validate() {
    if (!form.query.trim()) return "请输入人才需求";
    if (form.limit < 1 || form.limit > 100) return "Top K 必须在 1–100 之间";
    if (form.ef < 10 || form.ef > 1000) return "ef 必须在 10–1000 之间";
    if (form.rrfK < 1 || form.rrfK > 1000) return "rrf_k 必须在 1–1000 之间";
    if (form.rerankTopN < 1 || form.rerankTopN > 100) return "rerank_top_n 必须在 1–100 之间";
    return "";
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = validate();
    if (message) {
      setError(message);
      return;
    }

    setBusy(true);
    setError("");
    setClarifications([]);
    try {
      const data = await apiRequest<TalentSearchResponse>("/talent-search", {
        method: "POST",
        evidence: true,
        body: {
          query: form.query.trim(),
          retrieval_mode: form.retrievalMode,
          include_evidence_pack: form.includeEvidence,
          limit: form.limit,
          ef: form.ef,
          rrf_k: form.rrfK,
          rerank_top_n: form.rerankTopN,
        },
      });
      setResult(data);

      const candidateIds = data.candidate_ids;
      if (candidateIds.length > 0) {
        try {
          const profileData = await apiRequest<CandidateProfilesResponse>("/candidates/profiles", {
            method: "POST",
            evidence: true,
            body: { candidate_ids: candidateIds },
          });
          const entries: [string, CandidateProfile][] = profileData.items.map((item) => [
            item.candidate_id,
            item,
          ]);
          setProfiles(Object.fromEntries(entries));
        } catch {
          setProfiles({});
        }
      } else {
        setProfiles({});
      }
    } catch (err) {
      const items = clarificationItems(err);
      if (items.length > 0) {
        setClarifications(items);
        setError("需求需要补充或澄清后再执行");
      } else {
        setError(err instanceof Error ? err.message : "人才检索失败");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="pageHead">
        <div>
          <h1>评估与推荐</h1>
          <p>把自然语言用人需求编译成查询计划，筛选候选人并召回可核验的材料证据。</p>
        </div>
        <div className="actions">
          <span className="pageBadge">
            <Sparkles />
            检索式评估
          </span>
        </div>
      </div>

      <section className="panel formPanel">
        <form onSubmit={submit}>
          <label className="field full">
            <span>人才需求</span>
            <textarea
              value={form.query}
              onChange={(event) => update("query", event.target.value)}
              placeholder="例如：帮我找上海地区、5 年以上经验、有企业知识库和大模型应用项目经验的 AI 工程师"
              rows={4}
              aria-label="人才需求"
            />
          </label>

          <div className="fieldGrid">
            <div className="field">
              <span>检索模式</span>
              <div className="segmented" role="group" aria-label="检索模式">
                <button
                  type="button"
                  className={form.retrievalMode === "standard" ? "active" : ""}
                  onClick={() => update("retrievalMode", "standard")}
                >
                  标准检索
                </button>
                <button
                  type="button"
                  className={form.retrievalMode === "auto_optimize" ? "active" : ""}
                  onClick={() => update("retrievalMode", "auto_optimize")}
                >
                  自动优化
                </button>
              </div>
            </div>

            <label className="field">
              <span>Top K</span>
              <input
                type="number"
                min={1}
                max={100}
                value={form.limit}
                onChange={(event) => update("limit", Number(event.target.value) || 1)}
                aria-label="Top K"
              />
            </label>

            <label className="field checkboxField">
              <input
                type="checkbox"
                checked={form.includeEvidence}
                onChange={(event) => update("includeEvidence", event.target.checked)}
              />
              <span>生成证据包</span>
            </label>
          </div>

          <details className="advanced">
            <summary>
              <SlidersHorizontal />
              高级检索参数
            </summary>
            <div className="fieldGrid">
              <label className="field">
                <span>ef</span>
                <input
                  type="number"
                  min={10}
                  max={1000}
                  value={form.ef}
                  onChange={(event) => update("ef", Number(event.target.value) || 10)}
                />
              </label>
              <label className="field">
                <span>rrf_k</span>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={form.rrfK}
                  onChange={(event) => update("rrfK", Number(event.target.value) || 1)}
                />
              </label>
              <label className="field">
                <span>rerank_top_n</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={form.rerankTopN}
                  onChange={(event) => update("rerankTopN", Number(event.target.value) || 1)}
                />
              </label>
            </div>
          </details>

          <p className="hint">
            {form.includeEvidence
              ? "生成证据包会逐候选人、逐要求抽取事实，耗时较长；需要快速验证检索时可以先取消勾选。"
              : "当前只返回查询计划、候选人与命中片段，不生成证据包。"}
          </p>

          <div className="formActions">
            <button className="primary" type="submit" disabled={busy}>
              {busy ? <LoaderCircle className="spin" /> : <Search />}
              {busy ? "评估中…" : "开始评估"}
            </button>
            <button
              className="ghost"
              type="button"
              disabled={busy}
              onClick={() => {
                setForm(DEFAULTS);
                setResult(null);
                setProfiles({});
                setError("");
                setClarifications([]);
              }}
            >
              重置
            </button>
          </div>
        </form>

        {error && (
          <div className="inlineAlert" role="alert">
            <AlertTriangle />
            <div>
              <b>{error}</b>
              {clarifications.map((item, index) => (
                <p key={`${item.expression}-${index}`}>
                  {item.expression}：{item.reason}
                </p>
              ))}
            </div>
          </div>
        )}
      </section>

      {result ? (
        <RecommendationResults
          result={result}
          profiles={profiles}
          onOpenCitation={(chunkId, quoteStart, quoteEnd) =>
            setCitation({ chunkId, quoteStart, quoteEnd })
          }
        />
      ) : (
        <section className="panel emptyPanel">
          <FileSearch />
          <h3>提交需求后开始评估</h3>
          <p>结果会按查询计划、候选人、检索轨迹、命中证据和证据包分区展示。</p>
          <div className="flowHints">
            <span>1 · 编译需求</span>
            <span>2 · 筛选候选人</span>
            <span>3 · 召回证据</span>
            <span>4 · 核查引用</span>
          </div>
        </section>
      )}

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
