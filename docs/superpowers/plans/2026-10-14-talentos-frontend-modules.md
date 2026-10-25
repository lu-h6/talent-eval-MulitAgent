# TalentOS Frontend Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three placeholder sidebar items with a dashboard, a FastAPI-backed talent recommendation page, and a vector-versus-hybrid evaluation center while preserving the existing talent profile module.

**Architecture:** Split the 1,355-line frontend entry point into a hash-driven application shell, focused page components, typed API helpers, and shared domain types. The frontend continues to call the current FastAPI service on port `18080`; dashboard metrics are computed client-side and no backend contract changes are allowed.

**Tech Stack:** React, TypeScript, Vite, Vitest, React Testing Library, Lucide React, existing CSS and Nginx configuration.

**Spec:** `docs/superpowers/specs/2026-10-14-talentos-frontend-modules-design.md`

## Global Constraints

- Do not modify files under `backend/`, `database/`, `docker-compose.yml`, or `docker-compose-mineru.yml`.
- Use hash navigation with these canonical values: `#dashboard`, `#profiles`, `#recommendation`, and `#evaluation`.
- Keep the API base URL `http://127.0.0.1:18080/api`, tenant `course-demo`, and evidence scopes `hr_private,company_internal`.
- Preserve all existing employee roster, document library, upload, deletion, parsing, chunking, indexing, citation, and evidence behavior.
- Do not add a runtime router dependency.
- Use test-first development for every new behavior.

---

### Task 1: Frontend Test Harness and Shared Navigation/API Contracts

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/tsconfig.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/navigation.ts`
- Create: `frontend/src/navigation.test.ts`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/api.test.ts`
- Create: `frontend/src/types.ts`

**Interfaces:**
- Produces: `type ModuleId = "dashboard" | "profiles" | "recommendation" | "evaluation"`
- Produces: `parseModuleHash(hash: string): ModuleId`
- Produces: `navigateToModule(module: ModuleId): void`
- Produces: `apiRequest<T>(path: string, options?: ApiRequestOptions): Promise<T>`
- Produces: shared `Employee`, `KnowledgeBase`, `DocumentSummary`, `EvidenceHit`, `TalentSearchRequest`, and `TalentSearchResponse` types.

- [ ] **Step 1: Add test scripts and development dependencies**

Add these scripts to `frontend/package.json`:

```json
"test": "vitest run",
"test:watch": "vitest"
```

Install `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, and `@testing-library/user-event` as development dependencies. Configure `vitest.config.ts` with the React plugin, `environment: "jsdom"`, and `setupFiles: ["./src/test/setup.ts"]`.

- [ ] **Step 2: Write failing navigation tests**

Create `frontend/src/navigation.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseModuleHash } from "./navigation";

describe("parseModuleHash", () => {
  it.each([
    ["#dashboard", "dashboard"],
    ["#profiles", "profiles"],
    ["#recommendation", "recommendation"],
    ["#evaluation", "evaluation"],
  ])("maps %s to %s", (hash, expected) => {
    expect(parseModuleHash(hash)).toBe(expected);
  });

  it("falls back to profiles for an unknown hash", () => {
    expect(parseModuleHash("#missing")).toBe("profiles");
  });
});
```

- [ ] **Step 3: Run the navigation test and verify RED**

Run: `cd frontend && npm test -- navigation.test.ts`

Expected: FAIL because `./navigation` does not exist.

- [ ] **Step 4: Implement navigation helpers**

Create `frontend/src/navigation.ts`:

```ts
export type ModuleId = "dashboard" | "profiles" | "recommendation" | "evaluation";

const modules = new Set<ModuleId>(["dashboard", "profiles", "recommendation", "evaluation"]);

export function parseModuleHash(hash: string): ModuleId {
  const value = hash.replace(/^#/, "") as ModuleId;
  return modules.has(value) ? value : "profiles";
}

export function navigateToModule(module: ModuleId) {
  window.location.hash = module;
}
```

- [ ] **Step 5: Write failing API client tests**

Create `frontend/src/api.test.ts` with a restored `global.fetch` after each test and these behaviors:

```ts
it("adds tenant and evidence headers", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);

  await apiRequest("/talent-search", { method: "POST", evidence: true, body: { query: "AI 工程师" } });

  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:18080/api/talent-search",
    expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({
        "Content-Type": "application/json",
        "X-Tenant-Id": "course-demo",
        "X-Permission-Scopes": "hr_private,company_internal",
      }),
      body: JSON.stringify({ query: "AI 工程师" }),
    }),
  );
});

it("normalizes a backend detail error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "模型未配置" }), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  })));

  await expect(apiRequest("/talent-search")).rejects.toThrow("模型未配置");
});
```

- [ ] **Step 6: Run API tests and verify RED**

Run: `cd frontend && npm test -- api.test.ts`

Expected: FAIL because `apiRequest` does not exist.

- [ ] **Step 7: Implement the shared API client and types**

Create `frontend/src/api.ts` with:

```ts
export const API_BASE_URL = "http://127.0.0.1:18080/api";
export const TENANT_ID = "course-demo";
export const PERMISSION_SCOPES = "hr_private,company_internal";

export type ApiRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  evidence?: boolean;
};

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("X-Tenant-Id", TENANT_ID);
  if (options.evidence) headers.set("X-Permission-Scopes", PERMISSION_SCOPES);
  if (options.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload && "detail" in payload ? payload.detail : payload;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || "请求失败"));
  }
  return payload as T;
}
```

Move shared domain interfaces out of `main.tsx` into `frontend/src/types.ts` without changing field names. Add explicit response types for `/talent-search` and both evidence endpoints based on the current backend response shape.

- [ ] **Step 8: Run Task 1 tests and build**

Run: `cd frontend && npm test -- navigation.test.ts api.test.ts && npm run build`

Expected: all tests PASS and build exits `0`.

- [ ] **Step 9: Commit Task 1**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/vitest.config.ts frontend/src/test/setup.ts frontend/src/navigation.ts frontend/src/navigation.test.ts frontend/src/api.ts frontend/src/api.test.ts frontend/src/types.ts
git commit -m "test: add frontend navigation and api contracts"
```

### Task 2: Application Shell and Existing Profiles Extraction

**Files:**
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/App.test.tsx`
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/pages/TalentProfilesPage.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/EvidencePackPanel.tsx`

**Interfaces:**
- Consumes: `ModuleId`, `parseModuleHash`, `navigateToModule`, shared API constants and types.
- Produces: `App` with hash-aware shell and `TalentProfilesPage` containing the existing profile workflows.

- [ ] **Step 1: Write failing application shell tests**

Create `frontend/src/App.test.tsx`:

```tsx
it("renders talent profiles for the profiles hash", () => {
  window.location.hash = "#profiles";
  render(<App />);
  expect(screen.getByRole("button", { name: "人才档案" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("button", { name: "员工花名册" })).toBeInTheDocument();
});

it("changes modules when a sidebar item is selected", async () => {
  window.location.hash = "#profiles";
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "工作台" }));
  expect(window.location.hash).toBe("#dashboard");
  expect(screen.getByRole("heading", { name: "工作台" })).toBeInTheDocument();
});
```

Stub the profile list requests with empty arrays so the test exercises the real page without needing a backend.

- [ ] **Step 2: Run the shell test and verify RED**

Run: `cd frontend && npm test -- App.test.tsx`

Expected: FAIL because `App.tsx` and functional module navigation do not exist.

- [ ] **Step 3: Extract the existing profile module without behavior changes**

Move the current `App` state, effects, roster/library rendering, dialogs, drawer, `Pagination`, `ChunkPane`, and `RecallLab` into `TalentProfilesPage.tsx`. Replace local API constants/helpers with imports from `api.ts`. Keep multipart upload requests as direct `fetch` calls because `apiRequest` JSON-encodes bodies; use the shared tenant constants for headers.

Update `EvidencePackPanel` to import `API_BASE_URL`, `TENANT_ID`, `PERMISSION_SCOPES`, and `apiRequest` instead of maintaining a second request implementation. Preserve its public behavior and citation drawer.

- [ ] **Step 4: Implement the hash-aware application shell**

`AppShell` receives:

```ts
type AppShellProps = {
  activeModule: ModuleId;
  collapsed: boolean;
  onCollapseChange: (collapsed: boolean) => void;
  onNavigate: (module: ModuleId) => void;
  children: React.ReactNode;
};
```

Each sidebar button sets `aria-current="page"` only when active. `App` listens for `hashchange`, normalizes unknown hashes to `#profiles`, and renders a temporary heading-only placeholder for new modules until later tasks replace it.

- [ ] **Step 5: Reduce the entry point**

Replace `frontend/src/main.tsx` with:

```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./style.css";
import "./refinements.css";

createRoot(document.getElementById("root")!).render(<App />);
```

- [ ] **Step 6: Run shell/profile regression tests and build**

Run: `cd frontend && npm test -- App.test.tsx && npm run build`

Expected: PASS, with the profiles page active at `#profiles` and no TypeScript errors.

- [ ] **Step 7: Commit Task 2**

```bash
git add frontend/src/main.tsx frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/components/AppShell.tsx frontend/src/pages/TalentProfilesPage.tsx frontend/src/EvidencePackPanel.tsx
git commit -m "refactor: split TalentOS application shell"
```

### Task 3: Dashboard Module

**Files:**
- Create: `frontend/src/pages/dashboard.ts`
- Create: `frontend/src/pages/dashboard.test.ts`
- Create: `frontend/src/pages/DashboardPage.tsx`
- Create: `frontend/src/pages/DashboardPage.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/refinements.css`

**Interfaces:**
- Produces: `calculateDashboardMetrics(documents: DocumentSummary[]): DashboardMetrics`
- Produces: `DashboardPage({ onNavigate }: { onNavigate: (module: ModuleId) => void })`

- [ ] **Step 1: Write failing metric calculation tests**

```ts
it("counts ready, processing, and failed documents", () => {
  const documents = [
    { id: "1", pipeline_status: "ready" },
    { id: "2", pipeline_status: "parsing" },
    { id: "3", pipeline_status: "indexing" },
    { id: "4", pipeline_status: "failed" },
  ] as DocumentSummary[];

  expect(calculateDashboardMetrics(documents)).toMatchObject({
    documentCount: 4,
    readyCount: 1,
    processingCount: 2,
    failedCount: 1,
  });
});
```

- [ ] **Step 2: Run metric test and verify RED**

Run: `cd frontend && npm test -- dashboard.test.ts`

Expected: FAIL because `calculateDashboardMetrics` does not exist.

- [ ] **Step 3: Implement deterministic dashboard calculations**

Treat `parsing`, `parsed`, `chunking`, `chunked`, and `indexing` as processing. Return a complete `statusCounts` record and sort recent documents by `created_at` descending before taking five.

- [ ] **Step 4: Write failing dashboard component tests**

Mock `apiRequest` so employees, knowledge bases, and documents resolve with fixture data. Assert that the page renders all four totals, recent documents, and that clicking “开始人才评估” navigates to `recommendation`.

- [ ] **Step 5: Run component test and verify RED**

Run: `cd frontend && npm test -- DashboardPage.test.tsx`

Expected: FAIL because `DashboardPage` does not exist.

- [ ] **Step 6: Implement DashboardPage**

Load `/employees`, `/knowledge-bases`, and `/documents` with `Promise.all`. Render compact KPI items, a status distribution band, recent document table, retry state, and icon-backed quick commands. Register the page in `App.tsx` for `#dashboard`.

- [ ] **Step 7: Run dashboard tests and build**

Run: `cd frontend && npm test -- dashboard.test.ts DashboardPage.test.tsx App.test.tsx && npm run build`

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add frontend/src/pages/dashboard.ts frontend/src/pages/dashboard.test.ts frontend/src/pages/DashboardPage.tsx frontend/src/pages/DashboardPage.test.tsx frontend/src/App.tsx frontend/src/refinements.css
git commit -m "feat: add TalentOS dashboard"
```

### Task 4: Evaluation and Recommendation Module

**Files:**
- Create: `frontend/src/pages/RecommendationPage.tsx`
- Create: `frontend/src/pages/RecommendationPage.test.tsx`
- Create: `frontend/src/components/TalentSearchResults.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/refinements.css`

**Interfaces:**
- Consumes: `apiRequest<TalentSearchResponse>`, `TalentSearchRequest`, and shared result types.
- Produces: a form-backed recommendation page registered at `#recommendation`.

- [ ] **Step 1: Write failing request and rendering tests**

Test the real form interactions with `userEvent`:

```tsx
it("submits the selected retrieval options and renders the response", async () => {
  apiRequestMock.mockResolvedValue({
    query_plan: { task_type: "talent_search", filters: [], semantic_requirements: [] },
    candidate_ids: ["C001"],
    searches: [],
    chunks: [],
    evidence_packs: [],
    evidence_status: "reviewed",
  });

  render(<RecommendationPage />);
  await userEvent.type(screen.getByLabelText("人才需求"), "上海 AI 应用工程师");
  await userEvent.click(screen.getByLabelText("自动优化"));
  await userEvent.click(screen.getByLabelText("生成证据包"));
  await userEvent.click(screen.getByRole("button", { name: "开始评估" }));

  expect(apiRequestMock).toHaveBeenCalledWith("/talent-search", expect.objectContaining({
    method: "POST",
    evidence: true,
    body: expect.objectContaining({
      query: "上海 AI 应用工程师",
      retrieval_mode: "auto_optimize",
      include_evidence_pack: true,
    }),
  }));
  expect(await screen.findByText("C001")).toBeInTheDocument();
});
```

Add tests for a `422` clarification message and an empty candidate response.

- [ ] **Step 2: Run recommendation tests and verify RED**

Run: `cd frontend && npm test -- RecommendationPage.test.tsx`

Expected: FAIL because the page does not exist.

- [ ] **Step 3: Implement the recommendation form**

Use a textarea for the request, a segmented control for retrieval mode, a checkbox for evidence packs, and numeric controls constrained to backend ranges. Submit this exact body:

```ts
{
  query,
  retrieval_mode,
  include_evidence_pack,
  limit,
  ef,
  rrf_k,
  rerank_top_n,
}
```

Preserve form values after errors and keep the previous successful result until a later success replaces it.

- [ ] **Step 4: Implement structured result views**

`TalentSearchResults` renders query plan sections, candidate identifiers, search traces, ranked chunks, and evidence packs. Reuse citation opening behavior through the shared API client. Do not render raw JSON as the primary experience; raw metadata may appear in a collapsed details element.

- [ ] **Step 5: Register and style the module**

Replace the `recommendation` placeholder in `App.tsx`. Add compact form, result tabs/sections, responsive tables, and non-overlapping long text styles to `refinements.css`.

- [ ] **Step 6: Run recommendation tests and build**

Run: `cd frontend && npm test -- RecommendationPage.test.tsx App.test.tsx && npm run build`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add frontend/src/pages/RecommendationPage.tsx frontend/src/pages/RecommendationPage.test.tsx frontend/src/components/TalentSearchResults.tsx frontend/src/App.tsx frontend/src/refinements.css
git commit -m "feat: add talent recommendation workspace"
```

### Task 5: Vector and Hybrid Evaluation Center

**Files:**
- Create: `frontend/src/pages/evaluation.ts`
- Create: `frontend/src/pages/evaluation.test.ts`
- Create: `frontend/src/pages/EvaluationCenterPage.tsx`
- Create: `frontend/src/pages/EvaluationCenterPage.test.tsx`
- Create: `frontend/src/components/EvidenceResultColumn.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/refinements.css`

**Interfaces:**
- Produces: `parseListInput(value: string): string[] | undefined`
- Produces: `runTimedSearch<T>(operation: () => Promise<T>, now?: () => number): Promise<TimedResult<T>>`
- Produces: evaluation center registered at `#evaluation`.

- [ ] **Step 1: Write failing utility tests**

```ts
it("normalizes comma and newline separated filters", () => {
  expect(parseListInput(" C001, C002\nC003 ,, ")).toEqual(["C001", "C002", "C003"]);
  expect(parseListInput("   ")).toBeUndefined();
});

it("records duration for a successful search", async () => {
  const timestamps = [10, 42];
  await expect(runTimedSearch(async () => ["hit"], () => timestamps.shift()!)).resolves.toEqual({
    status: "success",
    durationMs: 32,
    data: ["hit"],
  });
});

it("records one search failure without throwing", async () => {
  const timestamps = [10, 25];
  await expect(runTimedSearch(async () => { throw new Error("服务不可用"); }, () => timestamps.shift()!)).resolves.toEqual({
    status: "error",
    durationMs: 15,
    error: "服务不可用",
  });
});
```

- [ ] **Step 2: Run utility tests and verify RED**

Run: `cd frontend && npm test -- evaluation.test.ts`

Expected: FAIL because the utilities do not exist.

- [ ] **Step 3: Implement list normalization and timed results**

Use a discriminated union:

```ts
export type TimedResult<T> =
  | { status: "success"; durationMs: number; data: T }
  | { status: "error"; durationMs: number; error: string };
```

Never throw from `runTimedSearch`; convert unknown errors to `请求失败`.

- [ ] **Step 4: Write failing independent-result component test**

Mock `/evidence/search` to resolve and `/evidence/hybrid-search` to reject. Submit one query and assert that the vector result is rendered while the hybrid column shows its own error. Assert both requests contain the same candidate/document filters and that hybrid-only parameters are included only in the hybrid body.

- [ ] **Step 5: Run component test and verify RED**

Run: `cd frontend && npm test -- EvaluationCenterPage.test.tsx`

Expected: FAIL because the page does not exist.

- [ ] **Step 6: Implement the evaluation center**

Build one compact form and execute both requests with `Promise.all` over two `runTimedSearch` calls. The vector body contains `query`, `candidate_ids`, `document_types`, `limit`, and `ef`. The hybrid body additionally contains `rrf_k` and `rerank_top_n`.

`EvidenceResultColumn` renders duration, hit count, rank, candidate, document title, score, and content. A column keeps its previous successful results when a later request for that column fails.

- [ ] **Step 7: Register and style the module**

Replace the `evaluation` placeholder in `App.tsx`. Use equal comparison columns on desktop and one column below the other under `900px`.

- [ ] **Step 8: Run evaluation tests and build**

Run: `cd frontend && npm test -- evaluation.test.ts EvaluationCenterPage.test.tsx App.test.tsx && npm run build`

Expected: PASS.

- [ ] **Step 9: Commit Task 5**

```bash
git add frontend/src/pages/evaluation.ts frontend/src/pages/evaluation.test.ts frontend/src/pages/EvaluationCenterPage.tsx frontend/src/pages/EvaluationCenterPage.test.tsx frontend/src/components/EvidenceResultColumn.tsx frontend/src/App.tsx frontend/src/refinements.css
git commit -m "feat: add evidence retrieval evaluation center"
```

### Task 6: Full Regression and Browser Verification

**Files:**
- Modify if required by verified defects only: `frontend/src/*.tsx`, `frontend/src/pages/*.tsx`, `frontend/src/components/*.tsx`, `frontend/src/refinements.css`
- Do not modify backend or Compose files.

**Interfaces:**
- Consumes all prior tasks.
- Produces verified desktop/mobile frontend behavior.

- [ ] **Step 1: Run the complete frontend test suite**

Run: `cd frontend && npm test`

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run the production build**

Run: `cd frontend && npm run build`

Expected: TypeScript and Vite build exit `0`.

- [ ] **Step 3: Start the frontend development server**

Run: `cd frontend && npm run dev`

Keep the returned session running and use the reported URL. If `15173` is occupied, start Vite on another available port and report it.

- [ ] **Step 4: Verify desktop behavior in a browser**

At a desktop viewport, verify:

- Every sidebar item changes the hash and active state.
- Dashboard metrics and retry behavior render without overlap.
- Talent profiles still switch between roster and library.
- Recommendation validates a blank request and renders a mocked or available backend response/error coherently.
- Evaluation center displays independent vector and hybrid outcomes.
- Browser console has no uncaught errors.

- [ ] **Step 5: Verify mobile behavior in a browser**

At a mobile viewport, verify:

- Sidebar collapse/navigation remains usable.
- KPI text and controls do not overflow.
- Recommendation form and result sections stack cleanly.
- Evaluation columns stack without horizontal page overflow.

- [ ] **Step 6: Confirm backend and Compose files are untouched by this implementation**

Run:

```bash
git diff --name-only 5121cf0..HEAD -- backend database docker-compose.yml docker-compose-mineru.yml
git diff --name-only -- backend database docker-compose.yml docker-compose-mineru.yml
```

Expected: no output attributable to this implementation. Pre-existing user changes remain untouched.

- [ ] **Step 7: Review final frontend diff**

Run: `git diff --check` and inspect `git status --short` plus the frontend-only diff. Fix only defects introduced by this plan.

- [ ] **Step 8: Commit verified integration fixes, if any**

```bash
git add frontend
git commit -m "fix: polish TalentOS module integration"
```

Skip this commit when verification required no additional changes.
