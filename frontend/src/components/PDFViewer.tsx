import { useEffect, useRef, useState, useCallback } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut, AlertCircle } from "lucide-react";
import type { ChunkBBox } from "@/types";
import { api } from "@/api/client";
import * as pdfjs from "pdfjs-dist";
import PDFWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = PDFWorkerUrl;

interface Props {
  paperId: string;
  highlightBboxes: ChunkBBox[];
  targetPage?: number;
  /** Increment to re-trigger jump even to the same page */
  jumpCounter?: number;
}

export function PDFViewer({ paperId, highlightBboxes, targetPage, jumpCounter }: Props) {
  const canvasRef      = useRef<HTMLCanvasElement>(null);
  const overlayRef     = useRef<HTMLCanvasElement>(null);
  const containerRef   = useRef<HTMLDivElement>(null);
  const pdfRef         = useRef<pdfjs.PDFDocumentProxy | null>(null);
  const renderTaskRef  = useRef<pdfjs.RenderTask | null>(null);

  const [currentPage,   setCurrentPage]   = useState(1);
  const [totalPages,    setTotalPages]    = useState(0);
  const [scale,         setScale]         = useState(1.2);
  const [loading,       setLoading]       = useState(true);
  const [error,         setError]         = useState<string | null>(null);
  const [isFlashing,    setIsFlashing]    = useState(false);
  const [documentReady, setDocumentReady] = useState(false);
  const [userZoomed,    setUserZoomed]    = useState(false);

  // ── Load document ────────────────────────────────────────────────────────
  useEffect(() => {
    setLoading(true);
    setError(null);
    setDocumentReady(false);
    setUserZoomed(false);
    const url = api.getPdfUrl(paperId);

    pdfjs.getDocument({ url, withCredentials: false }).promise
      .then(async (doc) => {
        pdfRef.current = doc;
        setTotalPages(doc.numPages);

        // Compute fit-to-width scale from first page
        const firstPage = await doc.getPage(1);
        const baseViewport = firstPage.getViewport({ scale: 1 });
        const containerWidth = containerRef.current?.clientWidth;
        if (containerWidth && baseViewport.width > 0) {
          const fitScale = (containerWidth - 48) / baseViewport.width; // 48px = padding
          setScale(Math.max(0.5, Math.min(3, fitScale)));
        }

        // Use targetPage if provided, otherwise page 1
        const startPage = (targetPage && targetPage >= 1 && targetPage <= doc.numPages)
          ? targetPage : 1;
        setCurrentPage(startPage);
        setLoading(false);
        setDocumentReady(true);
      })
      .catch((err) => {
        setError(`Failed to load PDF: ${err?.message ?? err}`);
        setLoading(false);
      });

    return () => {
      renderTaskRef.current?.cancel();
      pdfRef.current?.destroy();
      pdfRef.current = null;
    };
  }, [paperId]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Render page ──────────────────────────────────────────────────────────
  const renderPage = useCallback(async (pageNum: number, sc: number, bboxes: ChunkBBox[]) => {
    const pdf    = pdfRef.current;
    const canvas = canvasRef.current;
    if (!pdf || !canvas) return;

    renderTaskRef.current?.cancel();

    const page     = await pdf.getPage(pageNum);
    const viewport = page.getViewport({ scale: sc });
    canvas.width   = viewport.width;
    canvas.height  = viewport.height;

    const ctx  = canvas.getContext("2d")!;
    const task = page.render({ canvasContext: ctx, viewport });
    renderTaskRef.current = task;

    try {
      await task.promise;
    } catch (e: any) {
      if (e?.name === "RenderingCancelledException") return;
      throw e;
    }

    // Draw highlight overlay
    const overlay = overlayRef.current;
    if (!overlay) return;
    overlay.width  = viewport.width;
    overlay.height = viewport.height;
    const ovCtx    = overlay.getContext("2d")!;
    ovCtx.clearRect(0, 0, overlay.width, overlay.height);

    const pageBboxes = bboxes.filter((b) => b.page === pageNum);
    if (pageBboxes.length > 0) {
      ovCtx.fillStyle   = "rgba(116, 192, 252, 0.22)";
      ovCtx.strokeStyle = "rgba(116, 192, 252, 0.65)";
      ovCtx.lineWidth   = 1.5;
      for (const bbox of pageBboxes) {
        const x = bbox.x0 * sc;
        const y = viewport.height - bbox.y1 * sc;
        const w = (bbox.x1 - bbox.x0) * sc;
        const h = (bbox.y1 - bbox.y0) * sc;
        ovCtx.fillRect(x, y, w, h);
        ovCtx.strokeRect(x, y, w, h);
      }
    }
  }, []);

  // ── Flash animation when navigating to a citation ────────────────────────
  const flashPage = useCallback(() => {
    setIsFlashing(true);
    setTimeout(() => setIsFlashing(false), 600);
  }, []);

  // ── Re-render when page / scale / highlights change ──────────────────────
  useEffect(() => {
    if (documentReady && !error) {
      renderPage(currentPage, scale, highlightBboxes);
    }
  }, [currentPage, scale, documentReady, error, highlightBboxes, renderPage]);

  // ── Jump to targetPage when citation is clicked ──────────────────────────
  useEffect(() => {
    if (!documentReady || !targetPage || targetPage < 1) return;
    console.debug("[PaperPilot] pdf_page_change", { targetPage, currentPage });
    if (targetPage !== currentPage) {
      setCurrentPage(targetPage);
      flashPage();
    } else {
      flashPage();
    }
  }, [targetPage, jumpCounter, documentReady]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Manual zoom handlers ─────────────────────────────────────────────────
  const zoomOut = () => { setUserZoomed(true); setScale((s) => Math.max(0.5, s - 0.2)); };
  const zoomIn  = () => { setUserZoomed(true); setScale((s) => Math.min(3, s + 0.2)); };

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/5 flex-shrink-0">
        <div className="flex items-center gap-1">
          <button className="btn-ghost p-1.5" onClick={zoomOut}>
            <ZoomOut className="w-4 h-4" />
          </button>
          <span className="text-xs text-gray-400 w-12 text-center">{Math.round(scale * 100)}%</span>
          <button className="btn-ghost p-1.5" onClick={zoomIn}>
            <ZoomIn className="w-4 h-4" />
          </button>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-ghost p-1.5"
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            disabled={currentPage <= 1}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-xs text-gray-400 tabular-nums">{currentPage} / {totalPages}</span>
          <button
            className="btn-ghost p-1.5"
            onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
            disabled={currentPage >= totalPages}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Canvas area */}
      <div ref={containerRef} className="flex-1 overflow-auto bg-gray-950 flex justify-center p-4">
        {loading ? (
          <div className="flex items-center justify-center text-gray-500">
            <div className="w-6 h-6 rounded-full border-2 border-accent-400 border-t-transparent animate-spin mr-2" />
            Loading PDF…
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center gap-2 text-red-400 text-sm max-w-sm text-center">
            <AlertCircle className="w-6 h-6" />
            <p>{error}</p>
          </div>
        ) : (
          <div className="relative inline-block shadow-2xl">
            <canvas ref={canvasRef} className="block" />
            <canvas ref={overlayRef} className="absolute inset-0 pointer-events-none" />
            {isFlashing && (
              <div
                className="absolute inset-0 pointer-events-none rounded"
                style={{
                  background: "rgba(116, 192, 252, 0.12)",
                  boxShadow: "inset 0 0 0 2px rgba(116, 192, 252, 0.5)",
                  animation: "flashFade 600ms ease-out forwards",
                }}
              />
            )}
          </div>
        )}
      </div>

      <style>{`
        @keyframes flashFade {
          0%   { opacity: 1; }
          100% { opacity: 0; }
        }
      `}</style>
    </div>
  );
}
