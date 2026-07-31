import { useEffect, useRef, useCallback, useState } from "react";
import type { WSMessage } from "@/types";
import { getGuestId } from "@/store/guestStore";

const WS_BASE = import.meta.env.VITE_WS_URL ?? "";

type MessageHandler = (msg: WSMessage) => void;
type DisconnectHandler = () => void;
type OutboundMessage = {
  question: string;
  question_id: string | null;
  mode_override: string | null;
  context?: Record<string, unknown>;
};

export function useWebSocket(
  sessionId: string | null,
  onMessage: MessageHandler,
  onUnexpectedDisconnect?: DisconnectHandler
) {
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef<MessageHandler>(onMessage);
  const disconnectHandlerRef = useRef<DisconnectHandler | undefined>(onUnexpectedDisconnect);
  const intentionallyClosedSocketsRef = useRef(new WeakSet<WebSocket>());
  const pendingMessagesRef = useRef<OutboundMessage[]>([]);
  handlerRef.current = onMessage;
  disconnectHandlerRef.current = onUnexpectedDisconnect;
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
      if (!intentionallyClosedSocketsRef.current.has(ws)) {
        disconnectHandlerRef.current?.();
      }
    };

    return () => {
      intentionallyClosedSocketsRef.current.add(ws);
      ws.close();
      if (wsRef.current === ws) {
        wsRef.current = null;
      }
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
    const ws = wsRef.current;
    if (ws) {
      intentionallyClosedSocketsRef.current.add(ws);
      ws.close();
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
