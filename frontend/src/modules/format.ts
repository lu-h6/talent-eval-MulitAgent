export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  resume: "个人简历",
  interview: "面试记录",
  project: "项目成果",
  certificate: "能力证明",
  hris: "HRIS 数据",
  performance: "绩效记录",
  performance_review: "绩效记录",
  review: "述职报告",
  promotion: "晋升材料",
};

export const PIPELINE_LABELS: Record<string, string> = {
  uploaded: "仅上传",
  parsing: "解析中",
  parsed: "已解析",
  chunking: "切片中",
  chunked: "已切片",
  indexing: "索引中",
  ready: "可召回",
  failed: "失败",
};

export const PIPELINE_TONES: Record<string, string> = {
  uploaded: "pending",
  parsing: "pending",
  parsed: "running",
  chunking: "running",
  chunked: "running",
  indexing: "running",
  ready: "succeeded",
  failed: "failed",
};

export const TASK_TYPE_LABELS: Record<string, string> = {
  find_talent: "人才检索",
  lookup_profile: "档案查询",
};

export const FIELD_LABELS: Record<string, string> = {
  age: "年龄",
  region: "地区",
  department: "部门",
  job_level: "职级",
  years_of_experience: "工作年限",
  current_position: "当前岗位",
  employment_status: "在职状态",
};

export const OPERATOR_LABELS: Record<string, string> = {
  eq: "=",
  in: "属于",
  lt: "<",
  lte: "≤",
  gt: ">",
  gte: "≥",
};

export const PACK_STATUS_LABELS: Record<string, string> = {
  sufficient: "材料支持当前要求",
  partial: "证据待补充或核查",
  missing: "未找到可支持要求的证据",
  conflicting: "材料存在待核查冲突",
};

export const ANSWER_LABELS: Record<string, string> = {
  yes: "支持",
  no: "反向",
};

export const PERMISSION_LABELS: Record<string, string> = {
  hr_private: "HR 专属",
  department: "部门可见",
  internal: "公司内部",
};

export function documentTypeLabel(value?: string | null) {
  if (!value) return "-";
  return DOCUMENT_TYPE_LABELS[value] || value;
}

export function permissionLabel(value?: string | null) {
  if (!value) return "-";
  return PERMISSION_LABELS[value] || value;
}

export function pipelineLabel(value?: string | null) {
  return PIPELINE_LABELS[value || "uploaded"] || value || "仅上传";
}

export function pipelineTone(value?: string | null) {
  return PIPELINE_TONES[value || "uploaded"] || value || "pending";
}

export function packStatusLabel(value?: string | null) {
  return PACK_STATUS_LABELS[value || ""] || value || "待核查";
}

export function answerLabel(value?: string | null) {
  return ANSWER_LABELS[value || ""] || value || "-";
}

export function formatFilterValue(value: string | number | string[]) {
  return Array.isArray(value) ? value.join("、") : String(value);
}

export function formatScore(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "-";
}

export function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function formatDuration(ms?: number | null) {
  if (ms == null || !Number.isFinite(ms)) return "-";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export function metaString(metadata: Record<string, unknown>, key: string) {
  const value = metadata[key];
  return value == null ? "" : String(value);
}
