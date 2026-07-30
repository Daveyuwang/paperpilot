import { useEffect, useRef, useCallback, useState } from "react";
import type { WSMessage } from "@/types";
import { getGuestId } from "@/store/guestStore";

const WS_BASE = import.meta.env.VITE_WS_URL ?? "";

type MessageHandler = (msg: WSMessage) => void;
export type ConnectionState = "idle" | "connecting" | "open" | "closed" | "error";
export type CloseInfo = { code: number; reason: string; sequence: number };
type OutboundMessage = {
  question: string;
  question_id: string | null;
  mode_override: string | null;
  context?: Record<string, unknown>;
};

export function useWebSocket(sessionId: string | null, onMessage: MessageHandler) {
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef<MessageHandler>(onMessage);
  const pendingMessagesRef = useRef<OutboundMessage[]>([]);
  handlerRef.current = onMessage;
  // Increment to force a reconnect without changing sessionId
  const [reconnectTick, setReconnectTick] = useState(0);
  const [connectionState, setConnectionState] = useState<ConnectionState>(sessionId ? "connecting" : "idle");
  const [closeInfo, setCloseInfo] = useState<CloseInfo | null>(null);
  const closeSequenceRef = useRef(0);

  useEffect(() => {
    pendingMessagesRef.current = [];
    if (!sessionId) {
      setConnectionState("idle");
      setCloseInfo(null);
      return;
    }

    let disposed = false;
    setConnectionState("connecting");
    setCloseInfo(null);
    const url = `${WS_BASE}/ws/chat/${sessionId}?guest_id=${encodeURIComponent(getGuestId())}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (disposed) return;
      setConnectionState("open");
      console.log("[WS] connected", sessionId);
      if (pendingMessagesRef.current.length > 0) {
        const queued = [...pendingMessagesRef.current];
        pendingMessagesRef.current = [];
        queued.forEach((message) => ws.send(JSON.stringify(message)));
      }
    };

    ws.onmessage = (event) => {
      if (disposed || wsRef.current !== ws) return;
      try {
        const msg = JSON.parse(event.data) as WSMessage;
        handlerRef.current(msg);
      } catch {
        console.error("[WS] parse error", event.data);
      }
    };

    ws.onerror = (e) => {
      if (!disposed && wsRef.current === ws) setConnectionState("error");
      console.error("[WS] error", e);
    };

    ws.onclose = (event) => {
      if (!disposed && wsRef.current === ws) {
        setConnectionState("closed");
        setCloseInfo({
          code: event.code,
          reason: event.reason,
          sequence: ++closeSequenceRef.current,
        });
      }
      console.log("[WS] disconnected");
    };

    return () => {
      disposed = true;
      ws.close();
      wsRef.current = null;
      pendingMessagesRef.current = [];
    };
  }, [sessionId, reconnectTick]);

  const sendMessage = useCallback(
    (question: string, questionId?: string, modeOverride?: string, context?: Record<string, unknown>) => {
      const payload: OutboundMessage = {
        question,
        question_id: questionId ?? null,
        mode_override: modeOverride ?? null,
        ...(context ? { context } : {}),
      };

      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(payload));
        return;
      }

      pendingMessagesRef.current.push(payload);
      console.warn("[WS] queued until connected");
    },
    []
  );

  // Close the connection immediately (used for stop-generating)
  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    pendingMessagesRef.current = [];
    setConnectionState("closed");
  }, []);

  // Reconnect with the same sessionId (call after disconnect to restore WS)
  const reconnect = useCallback(() => {
    setConnectionState("connecting");
    setReconnectTick((t) => t + 1);
  }, []);

  return { sendMessage, disconnect, reconnect, connectionState, closeInfo };
}
