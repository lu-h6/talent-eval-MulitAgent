import { describe, expect, it } from "vitest";

import { MODULE_LAYOUT, MODULE_VIEWS } from "./App";
import { MODULE_ORDER } from "./nav";

describe("module routing tables", () => {
  it("每个模块都在 MODULE_LAYOUT 里声明了渲染方式", () => {
    for (const moduleId of MODULE_ORDER) {
      expect(MODULE_LAYOUT[moduleId], `缺少 ${moduleId} 的布局声明`).toMatch(/^(shell|fullBleed)$/);
    }
  });

  it("每个模块都在 MODULE_VIEWS 里绑定了页面实现", () => {
    for (const moduleId of MODULE_ORDER) {
      expect(typeof MODULE_VIEWS[moduleId], `缺少 ${moduleId} 的页面实现`).toBe("function");
    }
  });

  it("两张表的键与导航顺序完全一致", () => {
    expect(Object.keys(MODULE_LAYOUT).sort()).toEqual([...MODULE_ORDER].sort());
    expect(Object.keys(MODULE_VIEWS).sort()).toEqual([...MODULE_ORDER].sort());
  });
});
