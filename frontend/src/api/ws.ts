/**
 * Resilient WebSocket wrapper with ping/pong heartbeat and auto-reconnect.
 */
export interface WSOptions {
  url: string;
  onMessage: (data: Record<string, unknown>) => void;
  onOpen?: () => void;
  onClose?: (reason: string) => void;
  onError?: (error: Event) => void;
  onReconnect?: (attempt: number) => void;
  heartbeatIntervalMs?: number;
  maxRetries?: number;
}

const WS_HEARTBEAT_MS = 30_000;
const WS_MAX_RETRIES = 5;
const WS_BASE_DELAY_MS = 1000;

export class ResilientWebSocket {
  private ws: WebSocket | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private pongTimeout: ReturnType<typeof setTimeout> | null = null;
  private retryCount = 0;
  private closed = false;
  private lastMessageId = "";

  constructor(private opts: WSOptions) {}

  connect(): void {
    if (this.closed) return;

    try {
      this.ws = new WebSocket(this.opts.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.retryCount = 0;
      this.startHeartbeat();
      this.opts.onOpen?.();
    };

    this.ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        if (data.type === "pong") return;
        if (data.message_id) this.lastMessageId = data.message_id;
        this.opts.onMessage(data);
      } catch {
        // Skip malformed messages
      }
    };

    this.ws.onclose = () => {
      this.stopHeartbeat();
      if (!this.closed) {
        this.scheduleReconnect();
      }
      this.opts.onClose?.("connection closed");
    };

    this.ws.onerror = (err) => {
      this.opts.onError?.(err);
    };
  }

  send(data: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  close(): void {
    this.closed = true;
    this.stopHeartbeat();
    this.ws?.close();
    this.ws = null;
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  private startHeartbeat(): void {
    const interval = this.opts.heartbeatIntervalMs ?? WS_HEARTBEAT_MS;
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "ping" }));
        this.pongTimeout = setTimeout(() => {
          this.ws?.close();
        }, 5000);
      }
    }, interval);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    if (this.pongTimeout) clearTimeout(this.pongTimeout);
    this.heartbeatTimer = null;
    this.pongTimeout = null;
  }

  private scheduleReconnect(): void {
    const maxRetries = this.opts.maxRetries ?? WS_MAX_RETRIES;
    if (this.retryCount >= maxRetries) {
      this.opts.onClose?.("max retries reached");
      return;
    }
    this.retryCount++;
    const delay = Math.min(WS_BASE_DELAY_MS * Math.pow(2, this.retryCount - 1), 15_000);
    this.opts.onReconnect?.(this.retryCount);
    setTimeout(() => this.connect(), delay);
  }
}
