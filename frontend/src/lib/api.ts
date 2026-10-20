/**
 * REST API 封装：统一 {code, data, message} 响应处理。
 *
 * 后端 Pydantic 序列化输出 snake_case，前端类型约定 camelCase，
 * 故在此边界层做递归键名转换（请求 camel→snake，响应 snake→camel）。
 */

import { API_BASE } from "./constants";

export class ApiError extends Error {
  code: number;
  data?: unknown;

  constructor(code: number, message: string, data?: unknown) {
    super(message);
    this.code = code;
    this.data = data;
    this.name = "ApiError";
  }
}

interface ApiEnvelope<T> {
  code: number;
  data: T | null;
  message: string;
}

/** 错误码 → 友好提示（DESIGN 共享知识 #1 分段）。 */
const ERROR_HINTS: Record<number, string> = {
  1001: "参数错误",
  1002: "资源不存在",
  2001: "不支持的文件格式",
  2002: "PDF 已加密，需要密码",
  2003: "文档解析失败",
  2004: "切分失败",
  2005: "向量化失败",
  2006: "写入向量库失败",
  3001: "LLM 连接失败",
  3002: "模型下载失败",
  4001: "向量库连接失败",
  4002: "已自动降级为 sqlite-vec",
};

/** 根据错误码返回友好提示（无命中时返回后端原始 message）。 */
export function friendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    const hint = ERROR_HINTS[err.code];
    if (hint && !err.message.includes(hint)) return `${hint}：${err.message}`;
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

const SNAKE_RE = /_([a-z0-9])/g;
const CAMEL_RE = /[A-Z]/g;

/** snake_case 键 → camelCase（递归处理对象/数组）。 */
export function toCamel<T>(value: unknown): T {
  if (Array.isArray(value)) return value.map((v) => toCamel(v)) as T;
  if (value !== null && typeof value === "object" && !(value instanceof Date)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k.replace(SNAKE_RE, (_, c: string) => c.toUpperCase())] = toCamel(v);
    }
    return out as T;
  }
  return value as T;
}

/** camelCase 键 → snake_case（递归处理对象/数组）。 */
export function toSnake(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(toSnake);
  if (value !== null && typeof value === "object" && !(value instanceof Date)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k.replace(CAMEL_RE, (c) => `_${c.toLowerCase()}`)] = toSnake(v);
    }
    return out;
  }
  return value;
}

/** 通用请求：code!==0 抛 ApiError，否则返回 camelCase 化后的 data。 */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  let resp: Response;
  try {
    const isForm = init?.body instanceof FormData;
    resp = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        // FormData 由浏览器自动生成带 boundary 的 Content-Type
        ...(init?.body && !isForm ? { "Content-Type": "application/json" } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(-1, "无法连接后端服务，请确认后端已启动");
  }
  let body: ApiEnvelope<T>;
  try {
    body = (await resp.json()) as ApiEnvelope<T>;
  } catch {
    throw new ApiError(resp.status, `响应解析失败（HTTP ${resp.status}）`);
  }
  if (!resp.ok || body.code !== 0) {
    throw new ApiError(body.code ?? resp.status, body.message || resp.statusText, body.data);
  }
  return toCamel<T>(body.data);
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(toSnake(body)) : undefined,
    }),
  put: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(toSnake(body)) : undefined,
    }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
  /** multipart 上传（不设置 Content-Type，由浏览器生成 boundary）。 */
  upload: <T>(path: string, form: FormData) =>
    apiFetch<T>(path, { method: "POST", body: form }),
};
