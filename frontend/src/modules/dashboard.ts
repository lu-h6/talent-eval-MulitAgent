import { PIPELINE_LABELS } from "./format";
import type { DocumentSummary, Employee, KnowledgeBase } from "./types";

const PROCESSING_STATUSES = ["parsing", "parsed", "chunking", "chunked", "indexing"];

export type DashboardMetrics = {
  employeeCount: number;
  knowledgeBaseCount: number;
  documentCount: number;
  readyCount: number;
  processingCount: number;
  failedCount: number;
  statusCounts: Record<string, number>;
  recentDocuments: DocumentSummary[];
};

export type DashboardInput = {
  employees: Employee[];
  knowledgeBases: KnowledgeBase[];
  documents: DocumentSummary[];
};

export function calculateDashboardMetrics({
  employees,
  knowledgeBases,
  documents,
}: DashboardInput): DashboardMetrics {
  const statusCounts: Record<string, number> = {};
  for (const key of Object.keys(PIPELINE_LABELS)) statusCounts[key] = 0;

  let readyCount = 0;
  let processingCount = 0;
  let failedCount = 0;

  for (const document of documents) {
    const status = document.pipeline_status || "uploaded";
    statusCounts[status] = (statusCounts[status] || 0) + 1;
    if (status === "ready") readyCount += 1;
    else if (status === "failed") failedCount += 1;
    else if (PROCESSING_STATUSES.includes(status)) processingCount += 1;
  }

  const recentDocuments = [...documents]
    .sort((left, right) => {
      const leftTime = new Date(left.created_at).getTime();
      const rightTime = new Date(right.created_at).getTime();
      return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
    })
    .slice(0, 5);

  return {
    employeeCount: employees.length,
    knowledgeBaseCount: knowledgeBases.length,
    documentCount: documents.length,
    readyCount,
    processingCount,
    failedCount,
    statusCounts,
    recentDocuments,
  };
}
