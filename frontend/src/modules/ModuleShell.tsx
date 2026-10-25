import React from "react";
import {
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  LayoutGrid,
  Sparkles,
  UserSquare2,
} from "lucide-react";
import { MODULE_META, type ModuleId } from "./nav";

type NavItem = {
  id: ModuleId | "profiles";
  label: string;
  icon: React.ReactNode;
};

const NAV_ITEMS: NavItem[] = [
  { id: "dashboard", label: "工作台", icon: <LayoutGrid /> },
  { id: "profiles", label: "人才档案", icon: <UserSquare2 /> },
  { id: "recommendation", label: "评估与推荐", icon: <Sparkles /> },
  { id: "evaluation", label: "评测中心", icon: <FlaskConical /> },
];

type ModuleShellProps = {
  activeModule: ModuleId;
  subtitle: string;
  collapsed: boolean;
  onCollapseChange: (collapsed: boolean) => void;
  onNavigate: (module: ModuleId) => void;
  children: React.ReactNode;
};

export function ModuleShell({
  activeModule,
  subtitle,
  collapsed,
  onCollapseChange,
  onNavigate,
  children,
}: ModuleShellProps) {
  const meta = MODULE_META[activeModule];

  return (
    <div className={`app${collapsed ? " sidebarCollapsed" : ""}`}>
      <aside className="side">
        <div className="brand">
          <img src="/talentos-favicon.png" alt="TalentOS" />
          <div>
            <b>智鉴 TalentOS</b>
            <small>多 AGENT 人才决策平台</small>
          </div>
        </div>
        <nav>
          {NAV_ITEMS.map((item) => {
            if (item.id === "profiles") {
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    window.location.href = "/";
                  }}
                >
                  {item.icon}
                  <span>{item.label}</span>
                </button>
              );
            }
            const moduleId = item.id as ModuleId;
            const active = moduleId === activeModule;
            return (
              <button
                key={item.id}
                type="button"
                className={active ? "active" : ""}
                aria-current={active ? "page" : undefined}
                onClick={() => onNavigate(moduleId)}
              >
                {item.icon}
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="env">
          <div>
            <i />
            <b>测试环境</b>
          </div>
          <small>服务正常 · V1.0</small>
        </div>
        <button
          type="button"
          className="sideToggle"
          onClick={() => onCollapseChange(!collapsed)}
          aria-label={collapsed ? "展开导航栏" : "收起导航栏"}
          aria-expanded={!collapsed}
        >
          {collapsed ? <ChevronRight /> : <ChevronLeft />}
        </button>
      </aside>
      <main>
        <header>
          <div>
            <span>{meta.label}</span>
            <ChevronRight />
            <b>{subtitle}</b>
          </div>
          <div className="currentUser">
            <b>Will</b>
            <span>W</span>
          </div>
        </header>
        <section className="content">{children}</section>
      </main>
    </div>
  );
}
