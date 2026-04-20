import { useState } from "react";
import { Plus, MoreHorizontal, Pencil, Trash2, FolderOpen } from "lucide-react";
import { useWorkspaceStore, type Workspace } from "@/store/workspaceStore";
import clsx from "clsx";

export function WorkspaceHome() {
  const { workspaces, createWorkspace, openWorkspace, deleteWorkspace, renameWorkspace } =
    useWorkspaceStore();
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");

  const sorted = Object.values(workspaces).sort((a, b) => b.updatedAt - a.updatedAt);

  const handleCreate = () => {
    const title = newTitle.trim() || "Untitled Workspace";
    const ws = createWorkspace(title);
    setNewTitle("");
    setCreating(false);
    openWorkspace(ws.id);
  };

  if (sorted.length === 0 && !creating) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-surface-50">
        <div className="text-center max-w-sm">
          <h1 className="heading-serif text-2xl text-surface-800 mb-2">PaperPilot</h1>
          <p className="text-sm text-surface-500 mb-6">
            Create a workspace to start organizing your research.
          </p>
          <button
            onClick={() => setCreating(true)}
            className="btn-primary px-5 py-2.5 text-sm"
          >
            Create your first workspace
          </button>
        </div>
        {creating && (
          <CreateDialog
            value={newTitle}
            onChange={setNewTitle}
            onConfirm={handleCreate}
            onCancel={() => { setCreating(false); setNewTitle(""); }}
          />
        )}
      </div>
    );
  }

  return (
    <div className="h-screen bg-surface-50 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-8 py-12">
        <div className="flex items-center justify-between mb-8">
          <h1 className="heading-serif text-xl text-surface-800">Recent workspaces</h1>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {/* Create card */}
          <button
            onClick={() => setCreating(true)}
            className="group flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-surface-200 hover:border-accent-300 bg-white hover:bg-accent-50/30 p-6 min-h-[140px] transition-colors"
          >
            <Plus className="w-6 h-6 text-surface-300 group-hover:text-accent-500 transition-colors" />
            <span className="text-sm text-surface-400 group-hover:text-accent-600 transition-colors">
              New workspace
            </span>
          </button>

          {sorted.map((ws) => (
            <WorkspaceCard
              key={ws.id}
              workspace={ws}
              onOpen={() => openWorkspace(ws.id)}
              onRename={(title) => renameWorkspace(ws.id, title)}
              onDelete={() => deleteWorkspace(ws.id)}
            />
          ))}
        </div>
      </div>

      {creating && (
        <CreateDialog
          value={newTitle}
          onChange={setNewTitle}
          onConfirm={handleCreate}
          onCancel={() => { setCreating(false); setNewTitle(""); }}
        />
      )}
    </div>
  );
}

function WorkspaceCard({
  workspace,
  onOpen,
  onRename,
  onDelete,
}: {
  workspace: Workspace;
  onOpen: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState(workspace.title);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const age = formatRelative(workspace.updatedAt);

  if (renaming) {
    return (
      <div className="rounded-xl border border-accent-200 bg-white p-4 min-h-[140px] flex flex-col">
        <input
          autoFocus
          className="input-base text-sm mb-2"
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { onRename(renameValue.trim() || workspace.title); setRenaming(false); }
            if (e.key === "Escape") setRenaming(false);
          }}
        />
        <div className="flex gap-2 mt-auto">
          <button
            className="btn-primary px-3 py-1 text-xs"
            onClick={() => { onRename(renameValue.trim() || workspace.title); setRenaming(false); }}
          >
            Save
          </button>
          <button className="btn-ghost px-3 py-1 text-xs" onClick={() => setRenaming(false)}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className="group relative rounded-xl border border-surface-200 bg-white hover:border-surface-300 hover:shadow-sm p-4 min-h-[140px] flex flex-col cursor-pointer transition-all"
      onClick={onOpen}
    >
      <div className="flex items-start justify-between mb-2">
        <h3 className="heading-serif text-sm text-surface-800 leading-snug line-clamp-2 pr-6">
          {workspace.title}
        </h3>
        <button
          onClick={(e) => { e.stopPropagation(); setMenuOpen(!menuOpen); }}
          className="absolute top-3 right-3 p-1 rounded-md text-surface-300 hover:text-surface-500 hover:bg-surface-100 opacity-0 group-hover:opacity-100 transition-all"
        >
          <MoreHorizontal className="w-4 h-4" />
        </button>
      </div>

      {workspace.objective && (
        <p className="text-xs text-surface-400 line-clamp-2 mb-auto leading-relaxed">
          {workspace.objective}
        </p>
      )}

      <div className="mt-auto pt-3 flex items-center gap-3 text-[10px] text-surface-400">
        <span>{age}</span>
      </div>

      {menuOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={(e) => { e.stopPropagation(); setMenuOpen(false); }} />
          <div className="absolute top-10 right-3 z-50 w-36 rounded-lg border border-surface-200 bg-white shadow-lg py-1">
            <button
              className="w-full text-left px-3 py-1.5 text-xs text-surface-600 hover:bg-surface-50 flex items-center gap-2"
              onClick={(e) => { e.stopPropagation(); setMenuOpen(false); onOpen(); }}
            >
              <FolderOpen className="w-3 h-3" /> Open
            </button>
            <button
              className="w-full text-left px-3 py-1.5 text-xs text-surface-600 hover:bg-surface-50 flex items-center gap-2"
              onClick={(e) => { e.stopPropagation(); setMenuOpen(false); setRenameValue(workspace.title); setRenaming(true); }}
            >
              <Pencil className="w-3 h-3" /> Rename
            </button>
            <button
              className="w-full text-left px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 flex items-center gap-2"
              onClick={(e) => { e.stopPropagation(); setMenuOpen(false); setConfirmDelete(true); }}
            >
              <Trash2 className="w-3 h-3" /> Delete
            </button>
          </div>
        </>
      )}

      {confirmDelete && (
        <DeleteConfirm
          title={workspace.title}
          onConfirm={() => { setConfirmDelete(false); onDelete(); }}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

function CreateDialog({
  value,
  onChange,
  onConfirm,
  onCancel,
}: {
  value: string;
  onChange: (v: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/30" onClick={onCancel} />
      <div className="relative w-[400px] max-w-[calc(100vw-24px)] rounded-2xl border border-surface-200 bg-white shadow-lg">
        <div className="px-5 py-4 border-b border-surface-200">
          <h2 className="text-sm font-semibold text-surface-800">New workspace</h2>
        </div>
        <div className="px-5 py-4">
          <label className="text-xs text-surface-500 mb-1.5 block">Workspace name</label>
          <input
            autoFocus
            className="input-base w-full text-sm"
            placeholder="e.g. Vision Retrieval Survey"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") onConfirm(); if (e.key === "Escape") onCancel(); }}
          />
        </div>
        <div className="px-5 py-3 border-t border-surface-100 flex justify-end gap-2">
          <button className="btn-ghost px-3 py-2 text-xs" onClick={onCancel}>Cancel</button>
          <button className="btn-primary px-4 py-2 text-xs" onClick={onConfirm}>Create</button>
        </div>
      </div>
    </div>
  );
}

function DeleteConfirm({
  title,
  onConfirm,
  onCancel,
}: {
  title: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
      <div className="absolute inset-0 bg-black/30" onClick={onCancel} />
      <div className="relative w-[400px] max-w-[calc(100vw-24px)] rounded-2xl border border-surface-200 bg-white shadow-lg">
        <div className="px-5 py-4 border-b border-surface-200">
          <h2 className="text-sm font-semibold text-surface-800">Delete workspace?</h2>
          <p className="text-xs text-surface-500 mt-1">
            This will permanently delete "{title}" and all its data.
          </p>
        </div>
        <div className="px-5 py-3 flex justify-end gap-2">
          <button className="btn-ghost px-3 py-2 text-xs" onClick={onCancel}>Cancel</button>
          <button
            className="px-3 py-2 rounded-lg text-xs font-semibold bg-red-50 text-red-600 hover:bg-red-100 border border-red-200 transition-colors"
            onClick={onConfirm}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

function formatRelative(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(ts).toLocaleDateString();
}
