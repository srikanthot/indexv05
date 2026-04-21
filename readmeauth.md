import type {
  Conversation,
  Message,
  Citation,
  FeedbackPayload,
  HealthStatus,
} from "./types";
import { isEntraConfigured, getMsalInstance, loginRequest } from "./auth-config";
import type { AuthenticationResult, PublicClientApplication } from "@azure/msal-browser";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Acquire an access token from the shared MSAL singleton.
 * Returns null when Entra is not configured or token acquisition fails.
 */
let tokenPromise: Promise<string | null> | null = null;

async function getTokenSingleton(
  fn: () => Promise<string | null>
): Promise<string | null> {
  // If a token request is already in flight, return it
  if (tokenPromise) {
    return tokenPromise;
  }

  // Otherwise start a new one
  tokenPromise = (async () => {
    try {
      return await fn();
    } finally {
      tokenPromise = null;
    }
  })();

  return tokenPromise;
}


async function getAccessToken(): Promise<string | null> {
  if (!isEntraConfigured()) return null;

  const instance: PublicClientApplication | null = getMsalInstance();
  if (!instance) return null;

  const accounts = instance.getAllAccounts();
  if (accounts.length === 0) return null;

  try {
    let response: AuthenticationResult = await instance.acquireTokenSilent({
      ...loginRequest,
      account: accounts[0],
    });

    // MSAL failure with no exception or no token
    if (!response || !response.accessToken) {
      console.warn("MSAL soft failure detected - clearing cache and retrying");
      await instance.clearCache();

      response = await instance.acquireTokenSilent({
        ...loginRequest,
        account: accounts[0],
      });
    }

    return response.accessToken;
  } catch (err) {
    /* MSAL Fix #2 Check if the message is recoverable so we don't waste time trying to recover
    a non-recoverable error */
    console.warn("First attempt for acquiring token failed. Retrying.")
    const msg = (err as any)?.message?.toLowerCase() ?? "";
    const isRecoverable =
      msg.includes("cache") ||
      msg.includes("invalid_grant") ||
      msg.includes("interaction_required");

    // If it is recoverable, clear the cache and try again
    let retryErrMessage = "";
    if (isRecoverable) {
      console.warn("MSAL cache issue detected - clearing and retrying");
      await instance.clearCache();

      try {
        const res: AuthenticationResult = await instance.acquireTokenSilent({
          ...loginRequest,
          account: accounts[0],
        });
        return res.accessToken;
      } catch (newErr) {
        retryErrMessage = (newErr as any)?.message;
      }
    }

    // Silent acquisition failed — user may need to re-authenticate.
    // Don't block the request; EasyAuth headers will provide identity
    // in production even without a Bearer token.
    console.warn("MSAL silent token acquisition failed — request will proceed without Bearer token.", err);
    if (retryErrMessage) {
      console.warn("MSAL silent token acquisition retry after cache refresh failed.", retryErrMessage);
    }
  }
  return null;
}

function getHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  // Debug user identity for local dev without Entra auth.
  // In production, the backend extracts identity from the Entra token
  // forwarded by Azure App Service EasyAuth. This header is ignored
  // when real auth is active.
  if (!isEntraConfigured() && typeof window !== "undefined") {
    const debugUser = localStorage.getItem("debug_user_id");
    if (debugUser) {
      headers["X-Debug-User-Id"] = debugUser;
    }
  }
  return headers;
}

/**
 * Build headers with optional Bearer token.
 * In Entra mode, tries to acquire an access token silently.
 * Falls back to base headers if token acquisition fails.
 */
async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers = getHeaders();
  const token = await getTokenSingleton(async () => {
    return await getAccessToken();
  });

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const authHeaders = await getAuthHeaders();
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { ...authHeaders, ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, text);
  }
  return res.json() as Promise<T>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string
  ) {
    super(`API ${status}: ${body}`);
    this.name = "ApiError";
  }
}

// ─── Health ────────────────────────────────────────────────────────
export async function checkHealth(): Promise<HealthStatus> {
  return request<HealthStatus>("/health");
}

// ─── Conversations ─────────────────────────────────────────────────
export async function listConversations(
  limit = 20
): Promise<Conversation[]> {
  return request<Conversation[]>(`/conversations?limit=${limit}`);
}

export async function createConversation(
  title?: string
): Promise<Conversation> {
  return request<Conversation>("/conversations", {
    method: "POST",
    body: JSON.stringify({ title: title ?? null }),
  });
}

export async function renameConversation(
  threadId: string,
  title: string
): Promise<Conversation> {
  return request<Conversation>(`/conversations/${threadId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export async function deleteConversation(
  threadId: string
): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/conversations/${threadId}`, {
    method: "DELETE",
  });
}

// ─── Messages ──────────────────────────────────────────────────────
export async function getMessages(
  threadId: string,
  limit = 50
): Promise<Message[]> {
  return request<Message[]>(
    `/conversations/${threadId}/messages?limit=${limit}`
  );
}

// ─── Chat (streaming) ──────────────────────────────────────────────
export interface StreamCallbacks {
  onToken: (token: string) => void;
  onMeta: (meta: Record<string, string>[]) => void;
  onCitations: (citations: Citation[]) => void;
  onAnswerReplaced?: (finalAnswer: string) => void;
  onDone: () => void;
  onError: (error: Error) => void;
}

/**
 * Stream a chat response via SSE.
 * Pass an AbortSignal to cancel the stream when the user switches
 * conversations or starts a new chat mid-generation.
 */
export async function streamChat(
  question: string,
  sessionId: string | null,
  callbacks: StreamCallbacks,
  signal?: AbortSignal
): Promise<void> {
  if (signal?.aborted) return;

  let res: Response;
  try {
    const authHeaders = await getAuthHeaders();
    res = await fetch(`${BASE_URL}/chat/stream`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({ question, session_id: sessionId }),
      signal,
    });
  } catch (err) {
    // AbortError is expected when caller cancels — not a real error
    if (err instanceof DOMException && err.name === "AbortError") return;
    callbacks.onError(
      err instanceof Error ? err : new Error("Network error")
    );
    return;
  }

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => res.statusText);
    callbacks.onError(new ApiError(res.status, text));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // Track current SSE event type across chunk boundaries — event: and
  // data: lines may arrive in different read() chunks.
  let currentEvent = "";

  // Inactivity timeout — abort if no data for 180s (catches hung connections)
  const STREAM_TIMEOUT_MS = 180_000;
  let inactivityTimer: ReturnType<typeof setTimeout> | null = null;
  let timedOut = false;
  const resetTimer = () => {
    if (inactivityTimer) clearTimeout(inactivityTimer);
    inactivityTimer = setTimeout(() => {
      timedOut = true;
      reader.cancel();
      callbacks.onError(new Error("Stream timed out — no data received for 3 minutes."));
    }, STREAM_TIMEOUT_MS);
  };

  try {
    resetTimer();
    while (true) {
      if (signal?.aborted) {
        reader.cancel();
        return;
      }

      const { value, done } = await reader.read();
      if (done) break;
      resetTimer();

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      // Keep incomplete last line in buffer
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          const data = line.slice(6);

          if (data === "[DONE]") {
            callbacks.onDone();
            return;
          }

          if (currentEvent === "answer_replaced") {
            // Backend sends the final renumbered answer text
            const finalAnswer = data.replace(/\\n/g, "\n");
            callbacks.onAnswerReplaced?.(finalAnswer);
            currentEvent = "";
          } else if (currentEvent === "meta") {
            try {
              const parsed = JSON.parse(data);
              if (parsed.meta) {
                callbacks.onMeta(parsed.meta);
              }
            } catch (parseErr) {
              console.warn("Failed to parse meta JSON from SSE:", parseErr, data);
            }

          } else if (currentEvent === "citations") {
            try {
              const parsed = JSON.parse(data);
              if (parsed.citations) {
                callbacks.onCitations(parsed.citations);
              }
            } catch (parseErr) {
              console.warn("Failed to parse citation JSON from SSE:", parseErr, data);
            }
            currentEvent = "";
          } else if (currentEvent === "ping") {
            // Ignore keepalive
            currentEvent = "";
          } else {
            // Content token — unescape literal \n back to newlines
            const token = data.replace(/\\n/g, "\n");
            callbacks.onToken(token);
            currentEvent = "";
          }
        } else if (line === "") {
          // Blank line resets event context per SSE spec
          currentEvent = "";
        }
      }
    }
    // Stream ended without [DONE] — still notify (unless timeout already fired)
    if (!timedOut) callbacks.onDone();
  } catch (err) {
    if (timedOut) return; // Timeout already called onError — don't double-fire
    if (err instanceof DOMException && err.name === "AbortError") return;
    callbacks.onError(
      err instanceof Error ? err : new Error("Stream read error")
    );
  } finally {
    if (inactivityTimer) clearTimeout(inactivityTimer);
  }
}

// ─── SAS URL signing (on-demand for PDF citations) ─────────────────
export async function getSignedPdfUrl(rawUrl: string): Promise<string> {
  try {
    const res = await request<{ signed_url: string }>(
      `/sas?url=${encodeURIComponent(rawUrl)}`
    );
    return res.signed_url || rawUrl;
  } catch {
    // Fallback: try opening the raw URL directly
    return rawUrl;
  }
}

// ─── Feedback ──────────────────────────────────────────────────────
export async function submitFeedback(
  payload: FeedbackPayload
): Promise<void> {
  await request<{ status: string }>("/feedback", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
