import React from "react";

export function EditableUserMessage({
  content,
  onResubmit,
  onCancel,
}: {
  content: string;
  onResubmit: (newContent: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = React.useState(content);
  const taRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (taRef.current) {
      taRef.current.focus();
      taRef.current.style.height = "auto";
      taRef.current.style.height = Math.min(taRef.current.scrollHeight, 120) + "px";
    }
  }, []);

  return (
    <div className="max-w-[90%] space-y-2">
      <textarea
        ref={taRef}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          e.target.style.height = "auto";
          e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (text.trim()) onResubmit(text.trim());
          }
          if (e.key === "Escape") onCancel();
        }}
        className="w-full bg-accent-50 border border-accent-300 rounded-xl px-4 py-3 text-sm text-accent-700 resize-none focus:outline-none focus:ring-1 focus:ring-accent-400"
        style={{ minHeight: "40px", maxHeight: "120px" }}
      />
      <div className="flex items-center gap-2 justify-end">
        <button
          onClick={onCancel}
          className="text-xs text-surface-400 hover:text-surface-600 transition-colors px-2 py-1"
        >
          Cancel
        </button>
        <button
          onClick={() => text.trim() && onResubmit(text.trim())}
          className="text-xs text-white bg-accent-600 hover:bg-accent-700 transition-colors px-3 py-1 rounded-md"
        >
          Resubmit
        </button>
      </div>
    </div>
  );
}
