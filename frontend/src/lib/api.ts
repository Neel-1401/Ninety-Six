// The only way the frontend talks to the backend (lead-owned).
// Cookies carry the session; every mutation sends the CSRF header the backend requires.

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
  /** Field errors from a 422 validation_error, keyed by dotted field path. */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    const fields = (this.details?.fields as { field: string; message: string }[] | undefined) ?? [];
    for (const f of fields) out[f.field] = f.message;
    return out;
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

async function request<T>(method: Method, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["X-Requested-With"] = "ninety-six";
    headers["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path.startsWith("/api") ? path : `/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError(0, "network_error", "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = undefined;
    }
  }
  if (!res.ok) {
    const e = (data as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
    throw new ApiError(
      res.status,
      e?.code ?? "http_error",
      e?.message ?? `Request failed (${res.status}).`,
      e?.details ?? {},
    );
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>("GET", path, undefined, signal),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/** Human message for any thrown error (safe to show). */
export function errorMessage(e: unknown): string {
  if (isApiError(e)) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong.";
}

/** RFC4122 v4 id for idempotent submissions (check-ins, concerns). */
export function newSubmissionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return "sub-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}
