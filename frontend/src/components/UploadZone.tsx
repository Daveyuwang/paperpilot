import React, { useCallback, useState } from "react"; // React needed for React.DragEvent / React.ChangeEvent types
import { Upload, FileText } from "lucide-react";
import clsx from "clsx";
import { usePaperStore } from "@/store/paperStore";

export function UploadZone() {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const uploadPaper = usePaperStore((s) => s.uploadPaper);

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        alert("Only PDF files are supported.");
        return;
      }
      setIsUploading(true);
      try {
        await uploadPaper(file);
      } catch (e) {
        alert(`Upload failed: ${e}`);
      } finally {
        setIsUploading(false);
      }
    },
    [uploadPaper]
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

  return (
    <label
      className={clsx(
        "flex flex-col items-center justify-center gap-3 p-10 rounded-xl border-2 border-dashed cursor-pointer transition-colors duration-150",
        isDragging
          ? "border-accent-500 bg-accent-600/10"
          : "border-white/10 hover:border-white/20 hover:bg-white/5",
        isUploading && "opacity-60 pointer-events-none"
      )}
      onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={onDrop}
    >
      <input
        type="file"
        accept=".pdf"
        className="sr-only"
        onChange={onInputChange}
        disabled={isUploading}
      />
      {isUploading ? (
        <>
          <div className="w-8 h-8 rounded-full border-2 border-accent-400 border-t-transparent animate-spin" />
          <span className="text-sm text-gray-400">Uploading…</span>
        </>
      ) : (
        <>
          <Upload className="w-8 h-8 text-gray-500" />
          <div className="text-center">
            <p className="text-sm font-medium text-gray-300">Drop a PDF here</p>
            <p className="text-xs text-gray-500 mt-0.5">or click to browse · max 50 MB</p>
          </div>
        </>
      )}
    </label>
  );
}
