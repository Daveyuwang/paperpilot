import { useEffect, useRef, useState, useCallback } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut, AlertCircle, Maximize, ArrowLeftRight } from "lucide-react";
import clsx from "clsx";
import type { ChunkBBox } from "@/types";
import { api } from "@/api/client";
import * as pdfjs from "pdfjs-dist";
import PDFWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = PDFWorkerUrl;

type ViewMode = "fit-width" | "fit-page" | "custom";

interface Props {
  paperId: string;
  highlightBboxes: ChunkBBox[];
  targetPage?: number;
  jumpCounter?: number;
}

export function PDFViewer({ paperId, highlightBboxes, targetPage, jumpCounter }: Props) {
  const canvasRef      = useRef<HTMLCanvasElement>(null);
  const overlayRef     = useRef<HTMLCanvasElement>(null);
  const containerRef   = useRef<HTMLDivElement>(null);
  const pdfRef         = useRef<pdfjs.PDFDocumentProxy | null>(null);
  const renderTaskRef  = useRef<pdfjs.RenderTask | null>(null);
  const baseDimsRef    = useRef<{ width: number; height: number } | null>(null);

  const [currentPage,   setCurrentPage]   = useState(1);
  const [totalPages,    setTotalPages]    = useState(0);
  const [scale,         setScale]         = useState(1.2);
  const [viewMode,      setViewMode]      = useState<ViewMode>("fit-width");
  const [loading,       setLoading]       = useState(true);
  const [error,         setError]         = useState<string | null>(null);
  const [isFlashing,    setIsFlashing]    = useState(false);
  const [documentReady, setDocumentReady] = useState(false);

  const computeFitWidth = useCallback(() => {
    const dims = baseDimsRef.current;
    const cw = containerRef.current?.clientWidth;
    if (!dims || !cw || dims.width <= 0) return null;
    return Math.max(0.5, Math.min(3, (cw - 32) / dims.width));
  }, []);

  const computeFitPage = useCallback(() => {
    const dims = baseDimsRef.current;
    const cw = containerRef.current?.clientWidth;
    const ch = containerRef.current?.clientHeight;
    if (!dims || !cw || !ch || dims.width <= 0 || dims.height <= 0) return null;
    const sw = (cw - 32) / dims.width;
    const sh = (ch - 32) / dims.height;
    return Math.max(0.5, Math.min(3, Math.min(sw, sh)));
  }, []);

  // Load document
  useEffect(() => {
    setLoading(true);
    setError(null);
    setDocumentReady(false);
    setViewMode("fit-width");
    baseDimsRef.current = null;
    const url = api.getPdfUrl(paperId);

    pdfjs.getDocument({ url, withCredentials: false }).promise
      .then(async (doc) => {
        pdfRef.current = doc;
        setTotalPages(doc.numPages);

        const firstPage = await doc.getPage(1);
        const baseViewport = firstPage.getViewport({ scale: 1 });
        baseDimsRef.current = { width: baseViewport.width, height: baseViewport.height };

        const containerWidth = containerRef.current?.clientWidth;
        if (containerWidth && baseViewport.width > 0) {
          const fitScale = (containerWidth - 32) / baseViewport.width;
          setScale(Math.max(0.5, Math.min(3, fitScale)));
        }

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

  // Render page
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

  const flashPage = useCallback(() => {
    setIsFlashing(true);
    setTimeout(() => setIsFlashing(false), 600);
  }, []);

  // Re-render when page / scale / highlights change
  useEffect(() => {
    if (documentReady && !error) {
      renderPage(currentPage, scale, highlightBboxes);
    }
  }, [currentPage, scale, documentReady, error, highlightBboxes, renderPage]);

  // Jump to targetPage when citation is clicked
  useEffect(() => {
    if (!documentReady || !targetPage || targetPage < 1) return;
    if (targetPage !== currentPage) {
      setCurrentPage(targetPage);
      flashPage();
    } else {
      flashPage();
    }
  }, [targetPage, jumpCounter, documentReady]); // eslint-disable-line react-hooks/exhaustive-deps

  // Zoom handlers
  const zoomOut = () => { setViewMode("custom"); setScale((s) => Math.max(0.5, +(s - 0.15).toFixed(2))); };
  const zoomIn  = () => { setViewMode("custom"); setScale((s) => Math.min(3, +(s + 0.15).toFixed(2))); };

  const handleFitWidth = () => {
    const s = computeFitWidth();
    if (s != null) { setScale(s); setViewMode("fit-width"); }
  };

  const handleFitPage = () => {
    const s = computeFitPage();
    if (s != null) { setScale(s); setViewMode("fit-page"); }
  };

  const handleActualSize = () => {
    setScale(1);
    setViewMode("custom");
  };

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-surface-200 flex-shrink-0 gap-2">
        <div className="flex items-center gap-0.5">
          <button className="btn-ghost p-1.5" onClick={zoomOut} title="Zoom out">
            <ZoomOut className="w-3.5 h-3.5" />
          </button>
          <button
            className="text-xs text-surface-500 hover:text-surface-700 px-1.5 py-1 rounded hover:bg-surface-100 transition-colors tabular-nums min-w-[3rem] text-center"
            onClick={handleActualSize}
            title="Reset to 100%"
          >
            {Math.round(scale * 100)}%
          </button>
          <button className="btn-ghost p-1.5" onClick={zoomIn} title="Zoom in">
            <ZoomIn className="w-3.5 h-3.5" />
          </button>
          <div className="w-px h-4 bg-surface-200 mx-1" />
          <button
            className={clsx(
              "text-[10px] px-2 py-1 rounded transition-colors",
              viewMode === "fit-width" ? "bg-surface-200 text-surface-700" : "text-surface-400 hover:text-surface-600 hover:bg-surface-100"
            )}
            onClick={handleFitWidth}
            title="Fit width"
          >
            <ArrowLeftRight className="w-3 h-3" />
          </button>
          <button
            className={clsx(
              "text-[10px] px-2 py-1 rounded transition-colors",
              viewMode === "fit-page" ? "bg-surface-200 text-surface-700" : "text-surface-400 hover:text-surface-600 hover:bg-surface-100"
            )}
            onClick={handleFitPage}
            title="Fit page"
          >
            <Maximize className="w-3 h-3" />
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
          <span className="text-xs text-surface-500 tabular-nums">{currentPage} / {totalPages}</span>
          <button
            className="btn-ghost p-1.5"
            onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
            disabled={currentPage >= totalPages}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Canvas area — items-start prevents vertical centering that clips top at high zoom */}
      <div ref={containerRef} className="flex-1 min-h-0 overflow-auto bg-surface-200 flex items-start justify-center p-4">
        {loading ? (
          <div className="flex items-center justify-center h-full w-full text-surface-500">
            <div className="w-6 h-6 rounded-full border-2 border-accent-400 border-t-transparent animate-spin mr-2" />
            Loading PDF…
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center h-full w-full gap-2 text-red-500 text-sm max-w-sm text-center">
            <AlertCircle className="w-6 h-6" />
            <p>{error}</p>
          </div>
        ) : (
          <div className="relative inline-block shadow-lg rounded overflow-hidden flex-shrink-0">
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
