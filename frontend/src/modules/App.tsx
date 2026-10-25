import React, { useEffect, useState } from "react";
import { LegacyProfiles } from "./LegacyProfiles";
import { ModuleShell } from "./ModuleShell";
import { MODULE_META, MODULE_ORDER, navigateToModule, parseModuleHash, type ModuleId } from "./nav";
import { DashboardPage } from "./pages/DashboardPage";
import { EvaluationCenterPage } from "./pages/EvaluationCenterPage";
import { RecommendationPage } from "./pages/RecommendationPage";

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

  return (
    <ModuleShell
      activeModule={activeModule}
      subtitle={MODULE_META[activeModule].subtitle}
      collapsed={collapsed}
      onCollapseChange={setCollapsed}
      onNavigate={navigate}
    >
      <LegacyProfiles active={activeModule === "profiles"} />
      {activeModule === "dashboard" && <DashboardPage onNavigate={navigate} />}
      {activeModule === "recommendation" && <RecommendationPage />}
      {activeModule === "evaluation" && <EvaluationCenterPage />}
    </ModuleShell>
  );
}
