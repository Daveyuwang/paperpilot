import { useEffect, useRef, useCallback, useState } from "react";
import type { WSMessage } from "@/types";
import { getGuestId } from "@/store/guestStore";

const WS_BASE = import.meta.env.VITE_WS_URL ?? "";

type MessageHandler = (msg: WSMessage) => void;
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

  useEffect(() => {
    if (!sessionId) return;

    const url = `${WS_BASE}/ws/chat/${sessionId}?guest_id=${encodeURIComponent(getGuestId())}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("[WS] connected", sessionId);
      if (pendingMessagesRef.current.length > 0) {
        const queued = [...pendingMessagesRef.current];
        pendingMessagesRef.current = [];
        queued.forEach((message) => ws.send(JSON.stringify(message)));
      }
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WSMessage;
        handlerRef.current(msg);
      } catch {
        console.error("[WS] parse error", event.data);
      }
    };

    ws.onerror = (e) => {
      console.error("[WS] error", e);
    };

    ws.onclose = () => {
      console.log("[WS] disconnected");
    };

    return () => {
      ws.close();
      wsRef.current = null;
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
  }, []);

  // Reconnect with the same sessionId (call after disconnect to restore WS)
  const reconnect = useCallback(() => {
    setReconnectTick((t) => t + 1);
  }, []);

  return { sendMessage, disconnect, reconnect };
}
