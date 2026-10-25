export type ModuleId = "dashboard" | "profiles" | "recommendation" | "evaluation";

export const MODULE_ORDER: ModuleId[] = ["dashboard", "profiles", "recommendation", "evaluation"];

export const MODULE_META: Record<ModuleId, { label: string; subtitle: string; description: string }> = {
  dashboard: {
    label: "工作台",
    subtitle: "运营总览",
    description: "汇总人才档案、知识库与材料处理进度，快速进入核心工作流。",
  },
  profiles: {
    label: "人才档案",
    subtitle: "员工档案",
    description: "维护结构化员工信息，并管理每名员工关联的档案材料。",
  },
  recommendation: {
    label: "评估与推荐",
    subtitle: "人才检索",
    description: "把用人需求编译成查询计划，完成候选人筛选、证据召回与材料核验。",
  },
  evaluation: {
    label: "评测中心",
    subtitle: "召回对比",
    description: "同一查询并行执行向量检索与混合检索，对比耗时、命中与排序结果。",
  },
};

const modules = new Set<string>(MODULE_ORDER);

export function parseModuleHash(hash: string): ModuleId {
  const value = hash.replace(/^#/, "").split("?")[0].trim();
  return modules.has(value) ? (value as ModuleId) : "profiles";
}

export function navigateToModule(module: ModuleId) {
  if (window.location.hash === `#${module}`) return;
  window.location.hash = module;
}
