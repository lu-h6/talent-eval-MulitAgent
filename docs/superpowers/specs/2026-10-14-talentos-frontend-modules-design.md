# TalentOS Frontend Modules Design

## Goal

Turn the three placeholder navigation items into functional frontend modules while preserving the existing talent profile experience and leaving all backend code and API contracts unchanged.

## Scope

The implementation is limited to `frontend/` plus frontend-focused documentation and tests. It must not modify files under `backend/`, `database/`, or either Compose file.

The resulting application contains four top-level modules:

- Dashboard (`#dashboard`)
- Talent Profiles (`#profiles`)
- Evaluation and Recommendation (`#recommendation`)
- Evaluation Center (`#evaluation`)

Hash navigation is used so page refresh, browser back, and browser forward work without adding a router dependency or changing Nginx fallback behavior.

## Architecture

The current `frontend/src/main.tsx` contains the application shell, data types, API helpers, and the entire talent profile module. The implementation separates responsibilities without rewriting the existing profile workflows:

- `App` owns the application shell, top-level hash navigation, sidebar state, and breadcrumb.
- Each top-level module is a focused page component.
- The existing roster and document library behavior moves into `TalentProfilesPage` with its current API behavior preserved.
- A shared API client handles JSON parsing, error normalization, tenant headers, and permission headers.
- Shared domain types and display helpers move out of `main.tsx` only when used by more than one page.

No runtime routing dependency is added. Lucide remains the icon source, and the current visual language remains the baseline.

## Shared API Client

The client uses the existing backend base URL `http://127.0.0.1:18080/api` and the existing tenant `course-demo`.

Requests that access tenant data include:

```text
X-Tenant-Id: course-demo
```

Requests that access evidence include:

```text
X-Permission-Scopes: hr_private,company_internal
```

The client exposes typed functions for:

- `GET /employees`
- `GET /knowledge-bases`
- `GET /documents`
- `POST /talent-search`
- `POST /evidence/search`
- `POST /evidence/hybrid-search`

Existing profile mutations and document operations continue to use the same URLs and payloads they use today. API errors are converted into a single user-facing message using `detail` when available and response text otherwise.

## Dashboard

The dashboard loads employees, knowledge bases, and documents in parallel from existing endpoints. It computes all metrics in the browser, so no dashboard endpoint is required.

The page displays:

- Employee count
- Knowledge base count
- Document count
- Ready document count
- Processing and failed document counts
- Pipeline status distribution
- Five most recently created documents
- Quick navigation commands for profiles, recommendation, and evaluation center

Partial loading failures are shown as a page-level error with a retry command. No cached or fabricated values are presented as current data.

## Talent Profiles

The existing employee roster and document library remain functionally unchanged. Their internal tab state remains local to the page. Existing dialogs, document drawers, upload, deletion, parsing, chunking, indexing, citations, and evidence pack behavior are preserved.

Moving this functionality into a page component must not change request payloads, response interpretation, pagination, or user-facing behavior.

## Evaluation and Recommendation

This page calls `POST /api/talent-search` and provides controls for:

- Natural-language talent request
- Retrieval mode: `standard` or `auto_optimize`
- Result limit
- Evidence pack inclusion
- Advanced values for `ef`, `rrf_k`, and `rerank_top_n`

The request always carries tenant and permission headers. The response is presented in four inspectable views:

- Query plan: task type, structured filters, semantic requirements, preferences, and clarifications
- Candidate set: candidate identifiers and count
- Search trace: requirement identifier, optimization strategy, generated queries, and chunk count
- Evidence: ranked chunks and optional candidate evidence packs

Empty candidate sets, clarification-required responses, missing model configuration, and retrieval failures have distinct empty/error states. Raw backend errors are not rendered as unstructured stack traces.

## Evaluation Center

The evaluation center compares the current evidence retrieval implementations without changing either implementation.

One form supplies:

- Query
- Optional candidate identifiers
- Optional document types
- Limit
- `ef`
- `rrf_k`
- `rerank_top_n`

Submitting runs these requests in parallel:

- `POST /api/evidence/search`
- `POST /api/evidence/hybrid-search`

Each request is timed independently with `performance.now()`. Results are shown side by side with:

- Duration
- Hit count
- Rank
- Candidate
- Source document
- Score
- Matching content

One request failing does not discard the successful result from the other request. Candidate and document type input is normalized by trimming whitespace and removing blank values before submission.

## Navigation and Interaction

The sidebar buttons become real navigation controls with an active state derived from the hash. Unknown hashes fall back to `#profiles`.

The breadcrumb and page title change with the active module. The sidebar collapse behavior remains global. Navigation commands use buttons and existing Lucide icons. Loading controls are disabled while their own request is active, without blocking unrelated modules.

## Visual Design

The application keeps its existing dark green sidebar and restrained operational UI. New pages use full-width page sections, compact metrics, tables, segmented controls, and structured result panels. They do not use marketing-style hero content, nested cards, decorative gradients, or nonfunctional explanatory banners.

The layout must work at desktop and mobile widths. Comparison results stack vertically on narrow screens. Text content must wrap without overlapping controls or changing fixed toolbar dimensions.

## Error Handling

- Initial load failures show a retryable page state.
- Form validation prevents blank queries and invalid numeric ranges.
- Recommendation errors preserve the submitted form values.
- Evaluation comparison records vector and hybrid errors independently.
- A failed request must not clear the last successful result until a new successful response replaces it.
- Backend-provided `detail` messages are shown when useful; implementation traces are not exposed.

## Testing

Add Vitest and React Testing Library as frontend development dependencies. Tests cover:

- Hash parsing, fallback, and navigation updates
- Shared request headers and error normalization
- Dashboard metric calculation
- Recommendation request payload and result rendering
- Independent success/failure behavior in the evaluation comparison
- Existing talent profile module remains reachable and active through `#profiles`

Verification includes:

- Frontend unit/component tests
- `npm run build`
- Browser checks at desktop and mobile viewport sizes
- Confirmation that no files outside the approved frontend/documentation scope changed during implementation

## Out of Scope

- Backend code or API changes
- LangGraph Agent Server integration on port `2024`
- New authentication or tenant selection flows
- Persistent evaluation history
- New job monitoring or retry endpoints
- Replacing the existing visual design system
