export const API_BASE_URL = "http://127.0.0.1:18080/api";
export const TENANT_ID = "course-demo";
export const PERMISSION_SCOPES = "hr_private,department,internal";

export type ApiRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  evidence?: boolean;
};

export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status = 0, payload: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

function errorMessage(payload: unknown, fallback: string) {
  if (typeof payload === "string" && payload.trim()) return payload;
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail !== undefined) return JSON.stringify(detail);
  }
  return fallback;
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const { body, evidence = false, headers: inputHeaders, ...rest } = options;
  const headers = new Headers(inputHeaders);
  headers.set("X-Tenant-Id", TENANT_ID);
  if (evidence) headers.set("X-Permission-Scopes", PERMISSION_SCOPES);
  if (body !== undefined) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...rest,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    throw new ApiError(
      error instanceof Error && error.message && error.message !== "Failed to fetch"
        ? `无法连接后端服务：${error.message}`
        : "无法连接后端服务，请确认后端已在 18080 端口启动",
      0,
      null,
    );
  }

  if (response.status === 204) return undefined as T;

  const contentType = response.headers.get("content-type") || "";
  let payload: unknown = null;
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  } else {
    const text = await response.text();
    payload = text;
  }

  if (!response.ok) {
    throw new ApiError(errorMessage(payload, `请求失败（HTTP ${response.status}）`), response.status, payload);
  }
  return payload as T;
}

export function friendlyErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "请求失败";
  if (/401|invalid[_-]?api[_-]?key|api[_-]?key.*(blocked|invalid)|AuthenticationError|Incorrect API key/i.test(message)) {
    return "模型服务鉴权失败：请检查后端 DASHSCOPE_API_KEY 是否有效后重试。";
  }
  if (/Embedding 服务未配置/i.test(message)) return "Embedding 服务未配置，无法执行检索。";
  if (/Rerank 服务未配置/i.test(message)) return "Rerank 服务未配置，无法执行混合检索。";
  if (/模型未配置|查询计划模型未配置/i.test(message)) return "查询计划模型未配置，请联系管理员检查模型设置。";
  return message;
}

export function errorDetail(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function clarificationItems(error: unknown): { expression: string; reason: string }[] {
  if (!(error instanceof ApiError)) return [];
  const detail = (error.payload as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && "items" in detail) {
    const items = (detail as { items?: unknown }).items;
    if (Array.isArray(items)) {
      return items.filter(
        (item): item is { expression: string; reason: string } =>
          Boolean(item) && typeof item === "object" && "expression" in item && "reason" in item,
      );
    }
  }
  return [];
}
