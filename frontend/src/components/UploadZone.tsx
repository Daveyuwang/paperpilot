import React, { useCallback, useState } from "react";
import { Upload, Plus } from "lucide-react";
import clsx from "clsx";
import { usePaperStore } from "@/store/paperStore";
import { useWorkspaceStore } from "@/store/workspaceStore";

export function UploadZone() {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const uploadPaper = usePaperStore((s) => s.uploadPaper);
  const papers = usePaperStore((s) => s.papers);
  const hasPapers = papers.length > 0;
  const workspaceId = useWorkspaceStore((s) => s.getActiveWorkspace()?.id);

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        alert("Only PDF files are supported.");
        return;
      }
      setIsUploading(true);
      try {
        await uploadPaper(file, workspaceId);
      } catch (e) {
        alert(`Upload failed: ${e}`);
      } finally {
        setIsUploading(false);
      }
    },
    [uploadPaper, workspaceId]
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = "";
  };

  if (hasPapers) {
    return (
      <label
        className={clsx(
          "flex items-center gap-2 px-2.5 py-2 rounded-lg border border-dashed cursor-pointer transition-colors duration-150",
          isDragging
            ? "border-accent-500 bg-accent-50"
            : "border-surface-300 hover:border-surface-400 hover:bg-surface-100",
          isUploading && "opacity-60 pointer-events-none"
        )}
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
      >
        <input type="file" accept=".pdf" className="sr-only" onChange={onInputChange} disabled={isUploading} />
        {isUploading ? (
          <div className="w-4 h-4 rounded-full border-2 border-accent-400 border-t-transparent animate-spin flex-shrink-0" />
        ) : (
          <Plus className="w-4 h-4 text-surface-400 flex-shrink-0" />
        )}
        <span className="text-xs text-surface-500">{isUploading ? "Uploading…" : "Add paper"}</span>
      </label>
    );
  }

  return (
    <label
      className={clsx(
        "flex flex-col items-center justify-center gap-2 p-4 rounded-xl border-2 border-dashed cursor-pointer transition-colors duration-150",
        isDragging
          ? "border-accent-500 bg-accent-50"
          : "border-surface-300 hover:border-surface-400 hover:bg-surface-100",
        isUploading && "opacity-60 pointer-events-none"
      )}
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={onDrop}
    >
      <input type="file" accept=".pdf" className="sr-only" onChange={onInputChange} disabled={isUploading} />
      {isUploading ? (
        <>
          <div className="w-6 h-6 rounded-full border-2 border-accent-400 border-t-transparent animate-spin" />
          <span className="text-xs text-surface-500">Uploading…</span>
        </>
      ) : (
        <>
          <Upload className="w-6 h-6 text-surface-400" />
          <div className="text-center">
            <p className="text-xs font-medium text-surface-600">Drop a PDF here</p>
            <p className="text-[10px] text-surface-400 mt-0.5">or click to browse</p>
          </div>
        </>
      )}
    </label>
  );
}
