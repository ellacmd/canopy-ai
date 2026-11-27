import type { Principal, TurnBody, TurnResult } from "./contracts";

export const SESSION_REVISION = "canopy.session-revision.v1";
export const SESSION_EXPIRED = "canopy.session-expired";
const SESSION_LOCK = "canopy.auth.v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export function describeError(error: unknown): string {
  return error instanceof ApiError ? error.message : "Connection interrupted. Try again.";
}

function send(path: string, options?: RequestInit): Promise<Response> {
  return fetch(`/api${path}`, { credentials: "same-origin", cache: "no-store", ...options });
}

export function jsonBody(body: object, method = "POST", idempotencyKey?: string): RequestInit {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  return { method, headers, body: JSON.stringify(body) };
}

async function check(response: Response) {
  if (response.ok) return response;
  const data = await response.json().catch(() => null);
  const fallback = response.status === 422
    ? "Check the information you entered and try again."
    : "The request could not be completed. Try again.";
  throw new ApiError(response.status, typeof data?.detail === "string" ? data.detail : fallback);
}

// The revision is an opaque notification for account changes. Credentials stay in HttpOnly cookies.
let inMemoryRevision: string | null = null;
function revision() {
  try {
    return localStorage.getItem(SESSION_REVISION);
  } catch {
    return inMemoryRevision;
  }
}

function changeRevision() {
  inMemoryRevision = crypto.randomUUID();
  try {
    localStorage.setItem(SESSION_REVISION, inMemoryRevision);
  } catch {
    // A browser with storage disabled still gets same-tab protection.
  }
}

function assertRevision(expected: string | null) {
  if (expected !== revision()) throw new ApiError(401, "The browser session changed.");
}

function authLock<T>(mode: LockMode, task: () => Promise<T>): Promise<T> {
  return navigator.locks ? navigator.locks.request(SESSION_LOCK, { mode }, task) : task();
}

let refreshFlight: { revision: string | null; promise: Promise<void> } | undefined;
function recoverSession(expected: string | null): Promise<void> {
  if (refreshFlight?.revision === expected) return refreshFlight.promise;

  const promise = authLock("exclusive", async () => {
    assertRevision(expected);
    // A different tab may have already rotated the refresh cookie.
    const current = await send("/auth/me");
    if (current.ok) return;
    if (current.status !== 401) await check(current);

    // Without cross-tab locking, require sign-in instead of racing a rotating refresh token.
    if (!navigator.locks) throw new ApiError(401, "Sign in to continue.");
    await check(await send("/auth/refresh", { method: "POST" }));
  });
  refreshFlight = { revision: expected, promise };
  const clear = () => {
    if (refreshFlight?.promise === promise) refreshFlight = undefined;
  };
  void promise.then(clear, clear);
  return promise;
}

async function authenticated(path: string, options?: RequestInit) {
  const expected = revision();
  const request = () => authLock("shared", async () => {
    assertRevision(expected);
    return send(path, options);
  });

  let response = await request();
  if (response.status === 401) {
    void response.body?.cancel().catch(() => undefined);
    options?.signal?.throwIfAborted();
    try {
      await recoverSession(expected);
      response = await request();
      if (response.status === 401) await check(response);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401 && revision() === expected) {
        window.dispatchEvent(new Event(SESSION_EXPIRED));
      }
      throw error;
    }
  }
  assertRevision(expected);
  return { response: await check(response), expected };
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const { response, expected } = await authenticated(path, options);
  const data = await response.json() as T;
  assertRevision(expected);
  return data;
}

export function signIn(email: string, password: string): Promise<Principal> {
  return authLock("exclusive", async () => {
    const response = await check(await send("/auth/login", jsonBody({ email, password })));
    changeRevision();
    return response.json();
  });
}

export function signOut(): Promise<void> {
  const expected = revision();
  return authLock("exclusive", async () => {
    assertRevision(expected);
    const current = await send("/auth/me");
    if (current.status === 401 && navigator.locks) {
      // Renew an expired access cookie so the backend can revoke its Auth session.
      const renewed = await send("/auth/refresh", { method: "POST" });
      if (renewed.status !== 401) await check(renewed);
    }
    await check(await send("/auth/logout", { method: "POST" }));
    changeRevision();
  });
}

export async function streamTurn(
  conversationId: string,
  body: TurnBody,
  key: string,
  onStage: (stage: string) => void,
  signal: AbortSignal,
): Promise<TurnResult> {
  const { response, expected } = await authenticated(`/conversations/${conversationId}/turns/stream`, {
    ...jsonBody(body, "POST", key),
    signal,
  });
  if (!response.body || !response.headers.get("content-type")?.startsWith("text/event-stream")) {
    throw new ApiError(502, "The reply could not be read. Retry this request.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      assertRevision(expected);
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = /\r?\n\r?\n/.exec(buffer);
      while (boundary) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const lines = frame.split(/\r?\n/);
        const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
        const data = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
        if (event === "stage" && data) onStage(JSON.parse(data).stage);
        // FastAPI emits text deltas only after commit. Use the complete response for the transcript.
        if (event === "completed" && data) return JSON.parse(data) as TurnResult;
        if (event === "error" && data) {
          const { status, code, retry_after } = JSON.parse(data);
          if (code === "daily_usage_budget") {
            throw new ApiError(status, "The daily AI usage budget has been reached. Try again after midnight UTC.");
          }
          if (code === "rate_limit") {
            const retryDelay = Math.max(1, Number(retry_after) || 60);
            throw new ApiError(status, `Too many requests. Try again in ${retryDelay} seconds.`);
          }
          throw new ApiError(status, status === 409
            ? "The conversation is busy or the quote changed. Retry or reload the conversation."
            : "The support turn could not finish. Retry this request.");
        }
        boundary = /\r?\n\r?\n/.exec(buffer);
      }
    }
    throw new ApiError(502, "The connection ended before the reply was confirmed. Retry this request.");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
