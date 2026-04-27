import { useState, useRef } from "react";
import { usePaperStore } from "@/store/paperStore";

interface Props {
  chunkId: string;
  children: React.ReactNode;
}

export function CitationPreview({ chunkId, children }: Props) {
  const [show, setShow] = useState(false);
  const [position, setPosition] = useState<"above" | "below">("below");
  const ref = useRef<HTMLSpanElement>(null);
  const chunks = usePaperStore((s) => s.chunks);
  const activePaper = usePaperStore((s) => s.activePaper);

  const chunk = chunks.find((c) => c.id === chunkId);

  const handleMouseEnter = () => {
    if (!chunk) return;
    const rect = ref.current?.getBoundingClientRect();
    if (rect && rect.bottom > window.innerHeight - 200) {
      setPosition("above");
    } else {
      setPosition("below");
    }
    setShow(true);
  };

  if (!chunk) return <>{children}</>;

  return (
    <span
      ref={ref}
      className="relative inline"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <div
          className={`absolute z-50 w-72 p-3 rounded-xl border border-surface-200 bg-white shadow-lg text-xs ${
            position === "above" ? "bottom-full mb-2" : "top-full mt-2"
          } left-0`}
        >
          {activePaper?.title && (
            <p className="font-semibold text-surface-700 mb-1 line-clamp-1">
              {activePaper.title}
            </p>
          )}
          <p className="text-surface-500 leading-relaxed line-clamp-4">
            {chunk.content.slice(0, 200)}
            {chunk.content.length > 200 ? "..." : ""}
          </p>
          {chunk.page_number && (
            <p className="mt-1.5 text-accent-600 text-[10px]">
              Page {chunk.page_number} · {chunk.section_title || ""}
            </p>
          )}
        </div>
      )}
    </span>
  );
}
