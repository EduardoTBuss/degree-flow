import type { ApiErrorBody } from "./types";

// Prod: same origin. Dev: vite proxy sends /api to localhost:8000.
const BASE: string = import.meta.env.VITE_API_BASE ?? "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | undefined;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export interface RequestOpts {
  /** Per-request timeout in ms. Default: none — waits for the server (the
   *  exhaustive scrape can take minutes and must not be cancelled client-side). */
  timeoutMs?: number;
}

async function request<T>(path: string, init?: RequestInit, opts?: RequestOpts): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const headers: Record<string, string> = isForm
    ? { ...(init?.headers as Record<string, string>) }
    : { "Content-Type": "application/json", ...(init?.headers as Record<string, string>) };

  const controller = opts?.timeoutMs ? new AbortController() : null;
  const timer =
    controller && opts?.timeoutMs ? window.setTimeout(() => controller.abort(), opts.timeoutMs) : null;

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers, signal: controller?.signal });
  } catch {
    if (controller?.signal.aborted) {
      throw new ApiError(0, "TIMEOUT", "O servidor demorou demais para responder. Tente de novo em instantes.");
    }
    throw new ApiError(0, "NETWORK", "Não foi possível falar com o servidor. O backend está no ar?");
  } finally {
    if (timer !== null) window.clearTimeout(timer);
  }

  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    let message = res.statusText || `Erro ${res.status}`;
    let details: Record<string, unknown> | undefined;
    try {
      const body = (await res.json()) as ApiErrorBody | { detail?: unknown };
      if ("error" in body && body.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
        details = body.error.details;
      } else if ("detail" in body && body.detail) {
        message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, message, details);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const http = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown, opts?: RequestOpts) =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }, opts),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};
