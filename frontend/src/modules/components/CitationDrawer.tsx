import React, { useEffect, useState } from "react";
import { LoaderCircle, X } from "lucide-react";
import { apiRequest } from "../api";
import type { CitationDetail } from "../types";

type CitationDrawerProps = {
  chunkId: string;
  quoteStart?: number | null;
  quoteEnd?: number | null;
  onClose: () => void;
};

export function CitationDrawer({ chunkId, quoteStart, quoteEnd, onClose }: CitationDrawerProps) {
  const [citation, setCitation] = useState<CitationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setCitation(null);
    const query =
      quoteStart != null && quoteEnd != null
        ? `?quote_start=${quoteStart}&quote_end=${quoteEnd}`
        : "";
    apiRequest<CitationDetail>(`/evidence/citations/${encodeURIComponent(chunkId)}${query}`, {
      evidence: true,
    })
      .then((data) => {
        if (!cancelled) setCitation(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "引用不可用");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [chunkId, quoteStart, quoteEnd]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="drawerWrap" onClick={onClose}>
      <article
        className="drawer citationDrawer"
        role="dialog"
        aria-modal="true"
        aria-label="引用原文"
        onClick={(event) => event.stopPropagation()}
      >
        <button className="close" onClick={onClose} aria-label="关闭">
          <X />
        </button>
        {loading && (
          <div className="citationLoading" role="status">
            <LoaderCircle className="spin" />
            正在核查引用权限…
          </div>
        )}
        {error && <p className="chunkError">{error}</p>}
        {citation && (
          <>
            <div className="drawerHead">
              <small>{citation.document_title}</small>
              <h2>引用原文</h2>
              <p>
                版本 {citation.version_no} ·{" "}
                {citation.version_state === "historical" ? "历史版本" : "当前版本"}
              </p>
            </div>
            <div className="citationMeta">
              <span>候选人 {citation.candidate_id}</span>
              <span>权限 {citation.permission_scope}</span>
              {citation.page_start != null && <span>第 {citation.page_start} 页</span>}
              {citation.timestamp_start != null && <span>{citation.timestamp_start} 秒</span>}
            </div>
            {citation.heading_path.length > 0 && (
              <p className="secondaryText">标题路径：{citation.heading_path.join(" / ")}</p>
            )}
            {(citation.quote_start != null && citation.quote_end != null) || citation.markdown_start != null ? (
              <p className="secondaryText">
                {citation.markdown_start != null && citation.markdown_end != null && (
                  <>Chunk 范围：[{citation.markdown_start}, {citation.markdown_end})　</>
                )}
                {citation.quote_start != null && citation.quote_end != null && (
                  <>引用范围：[{citation.quote_start}, {citation.quote_end})</>
                )}
              </p>
            ) : null}
            <pre className="citationText">
              {citation.quote_start != null && citation.quote_end != null ? (
                <>
                  {Array.from(citation.content).slice(0, citation.quote_start).join("")}
                  <mark>
                    {Array.from(citation.content)
                      .slice(citation.quote_start, citation.quote_end)
                      .join("")}
                  </mark>
                  {Array.from(citation.content).slice(citation.quote_end).join("")}
                </>
              ) : (
                citation.content
              )}
            </pre>
          </>
        )}
      </article>
    </div>
  );
}
