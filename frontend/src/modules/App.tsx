import React, { useEffect, useState } from "react";
import { LegacyProfiles } from "./LegacyProfiles";
import { ModuleShell } from "./ModuleShell";
import { API_BASE_URL } from "./api";
import { MODULE_META, MODULE_ORDER, navigateToModule, parseModuleHash, type ModuleId } from "./nav";
import { DashboardPage } from "./pages/DashboardPage";
import { EvaluationCenter } from "./pages/EvaluationCenter";
import { TalentChatPage } from "./pages/TalentChatPage";

type ViewContext = {
  navigate: (module: ModuleId) => void;
  collapsed: boolean;
  toggleSidebar: () => void;
};

/**
 * 模块 → 渲染方式。
 * "shell"：内容区挂进本地 ModuleShell（自带左侧导航）；
 * "fullBleed"：整屏渲染，页面自带导航。
 */
export const MODULE_LAYOUT: Record<ModuleId, "shell" | "fullBleed"> = {
  dashboard: "shell",
  profiles: "shell",
  recommendation: "fullBleed",
  evaluation: "fullBleed",
};

/**
 * 模块 → 页面实现。换页面 / 换皮肤只改这一张表，
 * 路由、hash 同步与导航回调都不用动。
 */
export const MODULE_VIEWS: Record<ModuleId, (ctx: ViewContext) => React.ReactNode> = {
  dashboard: (ctx) => <DashboardPage onNavigate={ctx.navigate} />,
  profiles: () => <LegacyProfiles active />,
  recommendation: (ctx) => (
    <TalentChatPage
      apiBase={API_BASE_URL}
      sidebarCollapsed={ctx.collapsed}
      onToggleSidebar={ctx.toggleSidebar}
      onOpenRecords={() => ctx.navigate("profiles")}
      onOpenDashboard={() => ctx.navigate("dashboard")}
      onOpenEvaluation={() => ctx.navigate("evaluation")}
    />
  ),
  evaluation: (ctx) => (
    <EvaluationCenter
      apiBase={API_BASE_URL}
      sidebarCollapsed={ctx.collapsed}
      onToggleSidebar={ctx.toggleSidebar}
      onRecords={() => ctx.navigate("profiles")}
      onChat={() => ctx.navigate("recommendation")}
      onDashboard={() => ctx.navigate("dashboard")}
    />
  ),
};

export function App() {
  const [activeModule, setActiveModule] = useState<ModuleId>(() => parseModuleHash(window.location.hash));
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const onHashChange = () => setActiveModule(parseModuleHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (!window.location.hash) {
      window.history.replaceState(null, "", "#profiles");
      return;
    }
    const value = window.location.hash.replace(/^#/, "").split("?")[0];
    if (!MODULE_ORDER.includes(value as ModuleId)) {
      window.history.replaceState(null, "", "#profiles");
      setActiveModule("profiles");
    }
  }, []);

  useEffect(() => {
    document.title = `智鉴 TalentOS · ${MODULE_META[activeModule].label}`;
  }, [activeModule]);

  function navigate(module: ModuleId) {
    if (window.location.hash === `#${module}`) {
      setActiveModule(module);
      return;
    }
    navigateToModule(module);
  }

  const context: ViewContext = {
    navigate,
    collapsed,
    toggleSidebar: () => setCollapsed((value) => !value),
  };
  const view = MODULE_VIEWS[activeModule](context);

  if (MODULE_LAYOUT[activeModule] === "fullBleed") {
    return <>{view}</>;
  }

  return (
    <ModuleShell
      activeModule={activeModule}
      subtitle={MODULE_META[activeModule].subtitle}
      collapsed={collapsed}
      onCollapseChange={setCollapsed}
      onNavigate={navigate}
    >
      {view}
    </ModuleShell>
  );
}
