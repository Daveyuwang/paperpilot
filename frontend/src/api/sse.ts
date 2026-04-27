/**
 * Resilient SSE stream reader with automatic reconnect and backoff.
 */
export interface SSEOptions {
  url: string;
  method?: "GET" | "POST";
  headers?: Record<string, string>;
  body?: string;
  signal?: AbortSignal;
  onEvent: (event: Record<string, unknown>) => void;
  onError?: (error: Error) => void;
  onReconnect?: (attempt: number) => void;
  maxRetries?: number;
  lastEventId?: string;
}

const MAX_RETRIES = 5;
const BASE_DELAY_MS = 1000;
const HEARTBEAT_TIMEOUT_MS = 30_000;

export async function resilientSSE(opts: SSEOptions): Promise<void> {
  const maxRetries = opts.maxRetries ?? MAX_RETRIES;
  let attempt = 0;
  let lastEventId = opts.lastEventId || "";

  while (attempt <= maxRetries) {
    try {
      const headers: Record<string, string> = {
        "Accept": "text/event-stream",
        ...(opts.headers || {}),
      };
      if (lastEventId) {
        headers["Last-Event-ID"] = lastEventId;
      }

      const response = await fetch(opts.url, {
        method: opts.method || "POST",
        headers,
        body: opts.body,
        signal: opts.signal,
      });

      if (!response.ok) {
        throw new Error(`SSE request failed: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";
      let heartbeatTimer: ReturnType<typeof setTimeout> | null = null;

      const resetHeartbeat = () => {
        if (heartbeatTimer) clearTimeout(heartbeatTimer);
        heartbeatTimer = setTimeout(() => {
          reader.cancel();
        }, HEARTBEAT_TIMEOUT_MS);
      };

      resetHeartbeat();
      attempt = 0; // Reset on successful connection

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        resetHeartbeat();
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("id:")) {
            lastEventId = line.slice(3).trim();
            continue;
          }
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (!data || data === "[DONE]") continue;
          try {
            const parsed = JSON.parse(data);
            opts.onEvent(parsed);
          } catch {
            // Skip malformed JSON
          }
        }
      }

      if (heartbeatTimer) clearTimeout(heartbeatTimer);
      return; // Clean exit

    } catch (err) {
      if (opts.signal?.aborted) return;

      attempt++;
      if (attempt > maxRetries) {
        opts.onError?.(err instanceof Error ? err : new Error(String(err)));
        return;
      }

      const delay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt - 1), 15_000);
      opts.onReconnect?.(attempt);
      await new Promise((r) => setTimeout(r, delay));
    }
  }
}
