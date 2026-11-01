import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, LoaderCircle } from "lucide-react";

type LegacyProfilesProps = {
  active: boolean;
};

// 演示环境下默认展示带材料的演示知识库；只应用一次，之后尊重用户手动切换。
let demoKnowledgeBaseApplied = false;

/**
 * 把现有人才档案应用挂载到新壳里。
 *
 * main.tsx 保持原样，只在第一次进入「人才档案」时动态导入；
 * 导入后不卸载，切走时只隐藏，因此档案页的筛选、分页、上传抽屉等状态会保留。
 */
export function LegacyProfiles({ active }: LegacyProfilesProps) {
  const started = useRef(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!active || started.current) return;
    started.current = true;
    void import("../main")
      .then(() => setReady(true))
      .catch((err) => {
        started.current = false;
        setError(err instanceof Error ? err.message : "人才档案加载失败");
      });
  }, [active]);

  useEffect(() => {
    if (!active || !ready || demoKnowledgeBaseApplied) return;
    const root = document.getElementById("legacy-root");
    if (!root) return;

    const applyDemoKnowledgeBase = () => {
      if (demoKnowledgeBaseApplied) return;
      const select = root.querySelector<HTMLSelectElement>(".librarySubtitle select");
      if (!select || select.options.length === 0) return;
      const option = Array.from(select.options).find((item) =>
        (item.textContent || "").includes("人才评估演示库"),
      );
      if (!option) return;

      demoKnowledgeBaseApplied = true;
      if (select.value === option.value) return;
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
      setter?.call(select, option.value);
      select.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const observer = new MutationObserver(applyDemoKnowledgeBase);
    observer.observe(root, { childList: true, subtree: true });
    applyDemoKnowledgeBase();
    return () => observer.disconnect();
  }, [active, ready]);

  return (
    <div id="legacy-root" className={active ? "legacyPane" : "legacyPane legacyHidden"}>
      <div id="root" />
      {active && !ready && !error && (
        <div className="legacyLoading" role="status">
          <LoaderCircle className="spin" />
          正在加载人才档案…
        </div>
      )}
      {error && (
        <div className="pageError" role="alert">
          <AlertTriangle />
          <div>
            <b>人才档案加载失败</b>
            <span>{error}</span>
          </div>
        </div>
      )}
    </div>
  );
}
