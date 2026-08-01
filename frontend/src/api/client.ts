/* API client — token + account header handling, one fetch wrapper. */

const TOKEN_KEY = "pt_token";
const ACCT_KEY = "pt_account";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let token: string | null = localStorage.getItem(TOKEN_KEY);
let accountId: number | null = localStorage.getItem(ACCT_KEY)
  ? Number(localStorage.getItem(ACCT_KEY))
  : null;
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}
export function getToken(): string | null {
  return token;
}
export function setToken(t: string | null) {
  token = t;
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}
export function getAccountId(): number | null {
  return accountId;
}
export function setAccountId(id: number | null) {
  accountId = id;
  if (id != null) localStorage.setItem(ACCT_KEY, String(id));
  else localStorage.removeItem(ACCT_KEY);
}

interface ApiOpts {
  method?: string;
  body?: unknown;
  /** ms before aborting; default none (AI chat can run minutes) */
  timeoutMs?: number;
  /** skip the 401 → logout redirect (used by login itself) */
  noAuthRedirect?: boolean;
}

export async function api<T = unknown>(path: string, opts: ApiOpts = {}): Promise<T> {
  const { method = "GET", body, timeoutMs, noAuthRedirect } = opts;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = "Bearer " + token;
  if (accountId != null) headers["X-Account-ID"] = String(accountId);

  const ctrl = timeoutMs ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  let res: Response;
  try {
    res = await fetch("/api" + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl?.signal,
    });
  } catch (e) {
    if (timer) clearTimeout(timer);
    if ((e as Error).name === "AbortError") throw new ApiError(0, "Request timed out");
    throw new ApiError(0, "Network error — is the server up?");
  }
  if (timer) clearTimeout(timer);

  if (res.status === 401 && !noAuthRedirect) {
    setToken(null);
    onUnauthorized?.();
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}
