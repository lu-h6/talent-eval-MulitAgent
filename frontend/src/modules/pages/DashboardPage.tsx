import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Database,
  FileStack,
  FlaskConical,
  LoaderCircle,
  RefreshCw,
  Sparkles,
  UserSquare2,
  Users,
} from "lucide-react";
import { apiRequest } from "../api";
import { calculateDashboardMetrics } from "../dashboard";
import { documentTypeLabel, formatDateTime, pipelineLabel, pipelineTone } from "../format";
import type { ModuleId } from "../nav";
import type { DocumentSummary, Employee, KnowledgeBase } from "../types";

type DashboardData = {
  employees: Employee[];
  knowledgeBases: KnowledgeBase[];
  documents: DocumentSummary[];
};

const KPI_ITEMS = [
  { key: "employeeCount", label: "员工", hint: "花名册在册人数", icon: <Users /> },
  { key: "knowledgeBaseCount", label: "知识库", hint: "档案资料分类", icon: <BookOpen /> },
  { key: "documentCount", label: "材料", hint: "全部档案材料", icon: <FileStack /> },
  { key: "readyCount", label: "可召回", hint: "已完成证据索引", icon: <Database /> },
  { key: "processingCount", label: "处理中", hint: "解析 / 切片 / 索引", icon: <LoaderCircle /> },
  { key: "failedCount", label: "需处理", hint: "处理失败的材料", icon: <AlertTriangle /> },
] as const;

export function DashboardPage({ onNavigate }: { onNavigate: (module: ModuleId) => void }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [employees, knowledgeBases, documents] = await Promise.all([
        apiRequest<Employee[]>("/employees"),
        apiRequest<KnowledgeBase[]>("/knowledge-bases"),
        apiRequest<DocumentSummary[]>("/documents"),
      ]);
      setData({ employees, knowledgeBases, documents });
    } catch (err) {
      setError(err instanceof Error ? err.message : "工作台数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const metrics = useMemo(() => (data ? calculateDashboardMetrics(data) : null), [data]);
  const hasData = Boolean(data && metrics);

  return (
    <>
      <div className="pageHead">
        <div>
          <h1>工作台</h1>
          <p>汇总人才档案、知识库与材料处理进度，快速进入核心工作流。</p>
        </div>
        <div className="actions">
          <button className="ghost" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={loading ? "spin" : ""} />
            刷新
          </button>
          <button className="primary" onClick={() => onNavigate("recommendation")}>
            <Sparkles />
            开始人才评估
          </button>
        </div>
      </div>

      {error && (
        <div className="pageError" role="alert">
          <AlertTriangle />
          <div>
            <b>{hasData ? "刷新失败，当前展示上次加载的数据" : "工作台数据加载失败"}</b>
            <span>{error}</span>
          </div>
          <button className="ghost" onClick={() => void load()} disabled={loading}>
            <RefreshCw />
            重试
          </button>
        </div>
      )}

      {!hasData && loading && (
        <div className="panel loadingPanel" role="status">
          <LoaderCircle className="spin" />
          正在加载工作台数据…
        </div>
      )}

      {hasData && metrics && (
        <>
          <div className="kpiGrid">
            {KPI_ITEMS.map((item) => (
              <article className={`kpi kpi-${item.key}`} key={item.key}>
                <span className="kpiIcon">{item.icon}</span>
                <div>
                  <small>{item.label}</small>
                  <b>{metrics[item.key]}</b>
                  <em>{item.hint}</em>
                </div>
              </article>
            ))}
          </div>

          <div className="dashboardColumns">
            <section className="panel statusPanel">
              <div className="panelHead">
                <div>
                  <h2>材料处理分布</h2>
                  <p>按解析、切片与索引的流水线状态统计全部材料。</p>
                </div>
                <span className="panelBadge">共 {metrics.documentCount} 份</span>
              </div>
              {metrics.documentCount === 0 ? (
                <div className="empty compact">
                  <FileStack />
                  <h3>还没有档案材料</h3>
                  <p>前往人才档案的「档案资料库」上传第一份材料。</p>
                  <button className="primary" onClick={() => void load()}>
                    刷新数据
                  </button>
                </div>
              ) : (
                <>
                  <div className="statusBar" aria-hidden="true">
                    {Object.entries(metrics.statusCounts)
                      .filter(([, count]) => count > 0)
                      .map(([status, count]) => (
                        <i
                          key={status}
                          className={`statusSeg ${pipelineTone(status)}`}
                          style={{ width: `${(count / metrics.documentCount) * 100}%` }}
                          title={`${pipelineLabel(status)} ${count}`}
                        />
                      ))}
                  </div>
                  <div className="statusLegend">
                    {Object.entries(metrics.statusCounts).map(([status, count]) => (
                      <span key={status} className={count > 0 ? "" : "muted"}>
                        <i className={`statusDot ${pipelineTone(status)}`} />
                        {pipelineLabel(status)}
                        <b>{count}</b>
                      </span>
                    ))}
                  </div>
                </>
              )}
            </section>

            <section className="panel quickPanel">
              <div className="panelHead">
                <div>
                  <h2>快捷入口</h2>
                  <p>从总览直接进入常用工作流。</p>
                </div>
              </div>
              <div className="quickActions">
                <button onClick={() => (window.location.href = "/")}>
                  <UserSquare2 />
                  <span>
                    <b>人才档案</b>
                    <small>维护员工与档案材料</small>
                  </span>
                  <ArrowRight />
                </button>
                <button onClick={() => onNavigate("recommendation")}>
                  <Sparkles />
                  <span>
                    <b>评估与推荐</b>
                    <small>按用人需求检索候选人</small>
                  </span>
                  <ArrowRight />
                </button>
                <button onClick={() => onNavigate("evaluation")}>
                  <FlaskConical />
                  <span>
                    <b>评测中心</b>
                    <small>对比向量与混合召回</small>
                  </span>
                  <ArrowRight />
                </button>
              </div>
            </section>
          </div>

          <section className="panel recentPanel">
            <div className="panelHead">
              <div>
                <h2>最近上传</h2>
                <p>按创建时间展示最近 5 份档案材料。</p>
              </div>
              <button className="ghost small" onClick={() => (window.location.href = "/")}>
                进入人才档案
                <ArrowRight />
              </button>
            </div>
            {metrics.recentDocuments.length === 0 ? (
              <div className="empty compact">暂无材料</div>
            ) : (
              <div className="recentTable">
                <div className="recentRow recentHead">
                  <span>材料</span>
                  <span>员工</span>
                  <span>类型</span>
                  <span>状态</span>
                  <span>创建时间</span>
                </div>
                {metrics.recentDocuments.map((document) => (
                  <div className="recentRow" key={document.id}>
                    <span className="recentTitle">
                      <FileStack />
                      <b>
                        {document.title}
                        <small>{document.material_no || document.id.slice(0, 8)}</small>
                      </b>
                    </span>
                    <span>{document.employee_name}</span>
                    <span>{documentTypeLabel(document.document_type)}</span>
                    <span>
                      <strong className={`status ${pipelineTone(document.pipeline_status)}`}>
                        {pipelineLabel(document.pipeline_status)}
                      </strong>
                    </span>
                    <span className="recentTime">{formatDateTime(document.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
