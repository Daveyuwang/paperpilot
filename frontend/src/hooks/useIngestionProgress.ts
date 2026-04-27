import { useEffect, useRef, useState } from "react";
import { getGuestId } from "@/store/guestStore";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

const STAGE_LABELS: Record<string, string> = {
  uploaded: "Starting...",
  text_extracted: "Parsing PDF",
  chunked: "Chunking",
  embedded: "Embedding",
  scaffolded: "Building guide",
  ready: "Done",
  failed: "Failed",
};

export function stageLabel(stage: string | null): string {
  if (!stage) return "Processing";
  return STAGE_LABELS[stage] ?? "Processing";
}

export function useIngestionProgress(paperId: string | null, enabled: boolean) {
  const [stage, setStage] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!paperId || !enabled) {
      setStage(null);
      setProgress(null);
      return;
    }

    const guestId = getGuestId();
    const url = `${API_BASE}/api/papers/${paperId}/ingestion-progress?guest_id=${encodeURIComponent(guestId)}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.error) {
          es.close();
          return;
        }
        setStage(data.stage ?? null);
        setProgress(data.progress ?? null);
        if (data.status === "ready" || data.status === "error") {
          es.close();
        }
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      es.close();
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [paperId, enabled]);

  return { stage, progress };
}
