import { useEffect, useId, useRef, useState, type RefObject } from "react";
import {
  ArrowRight,
  FolderOpen,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import { useWorkspaceStore, type Workspace } from "@/store/workspaceStore";

export function WorkspaceHome() {
  const { workspaces, createWorkspace, openWorkspace, deleteWorkspace, renameWorkspace } =
    useWorkspaceStore();
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newObjective, setNewObjective] = useState("");

  const sorted = Object.values(workspaces).sort((a, b) => b.updatedAt - a.updatedAt);

  const closeCreate = () => {
    setCreating(false);
    setNewTitle("");
    setNewObjective("");
  };

  const handleCreate = () => {
    const title = newTitle.trim() || "Untitled research";
    const workspace = createWorkspace(title, newObjective.trim());
    closeCreate();
    openWorkspace(workspace.id);
  };

  return (
    <div className="min-h-screen bg-surface-50 text-surface-800">
      <header className="sticky top-0 z-30 border-b border-surface-200 bg-surface-50/95">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5 sm:px-8">
          <div className="flex items-center gap-2.5" aria-label="PaperPilot home">
            <BrandMark />
            <span className="text-sm font-semibold tracking-[-0.015em]">PaperPilot</span>
          </div>
          {sorted.length > 0 && (
            <button data-new-workspace className="btn-primary gap-2 text-sm" onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              New workspace
            </button>
          )}
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl px-5 py-12 sm:px-8 sm:py-16">
        {sorted.length === 0 ? (
          <section className="max-w-2xl py-12 sm:py-20" aria-labelledby="welcome-title">
            <p className="mb-5 text-sm font-semibold text-accent-700">Research, with receipts.</p>
            <h1
              id="welcome-title"
              className="max-w-xl text-4xl font-semibold leading-[1.08] tracking-[-0.035em] text-surface-900 sm:text-5xl"
            >
              Turn papers into work you can trace.
            </h1>
            <p className="mt-5 max-w-xl text-base leading-7 text-surface-500">
              Gather sources, ask citation-backed questions, and build drafts without losing the evidence trail.
            </p>
            <button data-start-workspace className="btn-primary mt-8 gap-2 px-5 py-2.5 text-sm" onClick={() => setCreating(true)}>
              Start a workspace
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </section>
        ) : (
          <section aria-labelledby="spaces-title">
            <div className="mb-8 flex items-end justify-between gap-6">
              <div>
                <h1 id="spaces-title" className="text-2xl font-semibold tracking-[-0.025em] text-surface-900">
                  Workspaces
                </h1>
                <p className="mt-1 text-sm text-surface-500">
                  Sources, conversations, and drafts stay together.
                </p>
              </div>
              <span className="hidden text-sm text-surface-400 sm:block">
                {sorted.length} {sorted.length === 1 ? "space" : "spaces"}
              </span>
            </div>

            <div className="overflow-visible border-y border-surface-200 bg-white">
              {sorted.map((workspace) => (
                <WorkspaceRow
                  key={workspace.id}
                  workspace={workspace}
                  onOpen={() => openWorkspace(workspace.id)}
                  onRename={(title) => renameWorkspace(workspace.id, title)}
                  onDelete={() => deleteWorkspace(workspace.id)}
                />
              ))}
            </div>
          </section>
        )}
      </main>

      {creating && (
        <CreateDialog
          title={newTitle}
          objective={newObjective}
          onTitleChange={setNewTitle}
          onObjectiveChange={setNewObjective}
          onConfirm={handleCreate}
          onCancel={closeCreate}
        />
      )}
    </div>
  );
}

function BrandMark() {
  return (
    <span
      className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-600 text-xs font-bold text-white"
      aria-hidden="true"
    >
      P
    </span>
  );
}

function WorkspaceRow({
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
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    menuRef.current?.querySelector<HTMLElement>("[role='menuitem']")?.focus();
  }, [menuOpen]);

  const commitRename = () => {
    onRename(renameValue.trim() || workspace.title);
    setRenaming(false);
  };

  if (renaming) {
    return (
      <div className="flex min-h-24 flex-col gap-3 border-b border-surface-200 px-4 py-4 last:border-b-0 sm:flex-row sm:items-center sm:px-5">
        <input
          autoFocus
          aria-label="Workspace name"
          className="input-base min-w-0 flex-1"
          value={renameValue}
          onChange={(event) => setRenameValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") commitRename();
            if (event.key === "Escape") setRenaming(false);
          }}
        />
        <div className="flex gap-2">
          <button className="btn-primary text-xs" onClick={commitRename}>Save</button>
          <button className="btn-ghost text-xs" onClick={() => setRenaming(false)}>Cancel</button>
        </div>
      </div>
    );
  }

  return (
    <div className="group relative flex min-h-24 items-stretch border-b border-surface-200 last:border-b-0 hover:bg-surface-50">
      <button
        data-workspace-open={workspace.id}
        className="flex min-w-0 flex-1 items-center gap-4 px-4 py-4 text-left sm:px-5"
        onClick={onOpen}
      >
        <span className="hidden h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-surface-100 text-surface-500 sm:flex">
          <FolderOpen className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-semibold text-surface-800">{workspace.title}</span>
          <span className="mt-1 block truncate text-sm text-surface-400">
            {workspace.objective || "No research goal yet"}
          </span>
        </span>
        <span className="hidden flex-shrink-0 text-xs text-surface-400 sm:block">
          {formatRelative(workspace.updatedAt)}
        </span>
        <ArrowRight className="h-4 w-4 flex-shrink-0 text-surface-300 transition-transform group-hover:translate-x-0.5 group-hover:text-surface-500" aria-hidden="true" />
      </button>

      <button
        ref={menuTriggerRef}
        type="button"
        className="mr-3 self-center rounded-lg p-2 text-surface-400 hover:bg-surface-100 hover:text-surface-700"
        onClick={() => setMenuOpen((open) => !open)}
        aria-label={`More actions for ${workspace.title}`}
        aria-expanded={menuOpen}
        aria-haspopup="menu"
      >
        <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
      </button>

      {menuOpen && (
        <>
          <div
            className="fixed inset-0 z-20 cursor-default"
            onClick={() => setMenuOpen(false)}
            aria-hidden="true"
          />
          <div
            ref={menuRef}
            className="absolute right-3 top-14 z-30 w-40 rounded-lg border border-surface-200 bg-white p-1 shadow-md"
            role="menu"
            aria-label={`Actions for ${workspace.title}`}
            onKeyDown={(event) => {
              const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>("[role='menuitem']") ?? []);
              const currentIndex = items.indexOf(document.activeElement as HTMLElement);
              if (event.key === "Escape") {
                event.preventDefault();
                setMenuOpen(false);
                menuTriggerRef.current?.focus();
              } else if (event.key === "Tab") {
                event.preventDefault();
                const candidates = Array.from(document.querySelectorAll<HTMLElement>(
                  "button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex='-1'])",
                )).filter((candidate) => !menuRef.current?.contains(candidate) && candidate.getClientRects().length > 0);
                const triggerIndex = candidates.indexOf(menuTriggerRef.current as HTMLElement);
                const target = candidates[triggerIndex + (event.shiftKey ? -1 : 1)] ?? menuTriggerRef.current;
                setMenuOpen(false);
                window.requestAnimationFrame(() => target?.focus());
              } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const delta = event.key === "ArrowDown" ? 1 : -1;
                items[(currentIndex + delta + items.length) % items.length]?.focus();
              } else if (event.key === "Home") {
                event.preventDefault();
                items[0]?.focus();
              } else if (event.key === "End") {
                event.preventDefault();
                items.at(-1)?.focus();
              }
            }}
          >
            <MenuButton icon={<FolderOpen />} label="Open" onClick={() => { setMenuOpen(false); onOpen(); }} />
            <MenuButton
              icon={<Pencil />}
              label="Rename"
              onClick={() => {
                setMenuOpen(false);
                setRenameValue(workspace.title);
                setRenaming(true);
              }}
            />
            <MenuButton
              icon={<Trash2 />}
              label="Delete"
              danger
              onClick={() => { setMenuOpen(false); setConfirmDelete(true); }}
            />
          </div>
        </>
      )}

      {confirmDelete && (
        <DeleteConfirm
          title={workspace.title}
          onConfirm={() => {
            const workspaceButtons = Array.from(document.querySelectorAll<HTMLElement>("[data-workspace-open]"));
            const index = workspaceButtons.findIndex((button) => button.dataset.workspaceOpen === workspace.id);
            const nextWorkspaceId = workspaceButtons[index + 1]?.dataset.workspaceOpen
              ?? workspaceButtons[index - 1]?.dataset.workspaceOpen
              ?? null;
            setConfirmDelete(false);
            onDelete();
            window.requestAnimationFrame(() => {
              const nextTarget = nextWorkspaceId
                ? document.querySelector<HTMLElement>(`[data-workspace-open="${CSS.escape(nextWorkspaceId)}"]`)
                : document.querySelector<HTMLElement>("[data-start-workspace], [data-new-workspace]");
              nextTarget?.focus();
            });
          }}
          onCancel={() => setConfirmDelete(false)}
          returnFocusRef={menuTriggerRef}
        />
      )}
    </div>
  );
}

function MenuButton({ icon, label, danger = false, onClick }: {
  icon: React.ReactElement;
  label: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      role="menuitem"
      className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs transition-colors ${
        danger ? "text-red-600 hover:bg-red-50" : "text-surface-600 hover:bg-surface-100"
      }`}
      onClick={onClick}
    >
      <span className="[&>svg]:h-3.5 [&>svg]:w-3.5" aria-hidden="true">{icon}</span>
      {label}
    </button>
  );
}

function CreateDialog({
  title,
  objective,
  onTitleChange,
  onObjectiveChange,
  onConfirm,
  onCancel,
}: {
  title: string;
  objective: string;
  onTitleChange: (value: string) => void;
  onObjectiveChange: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const headingId = useId();
  const dialogRef = useDialogFocus(onCancel);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6">
      <button className="absolute inset-0 bg-surface-900/35" onClick={onCancel} aria-label="Close dialog" />
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        className="relative max-h-[calc(100vh-24px)] w-full max-w-lg overflow-y-auto rounded-xl border border-surface-200 bg-white shadow-md"
      >
        <div className="border-b border-surface-200 px-5 py-4">
          <h2 id={headingId} className="text-base font-semibold text-surface-800">New workspace</h2>
        </div>
        <div className="space-y-4 px-5 py-5">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-surface-600">Name</span>
            <input
              data-dialog-initial-focus
              className="input-base w-full"
              placeholder="e.g. Long-context retrieval"
              value={title}
              onChange={(event) => onTitleChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) onConfirm();
              }}
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-surface-600">Research goal <span className="font-normal text-surface-400">optional</span></span>
            <textarea
              className="input-base min-h-24 w-full resize-y"
              placeholder="What are you trying to understand or produce?"
              value={objective}
              onChange={(event) => onObjectiveChange(event.target.value)}
            />
          </label>
        </div>
        <div className="flex justify-end gap-2 border-t border-surface-200 px-5 py-3">
          <button className="btn-ghost text-xs" onClick={onCancel}>Cancel</button>
          <button className="btn-primary text-xs" onClick={onConfirm}>Create workspace</button>
        </div>
      </section>
    </div>
  );
}

function DeleteConfirm({ title, onConfirm, onCancel, returnFocusRef }: {
  title: string;
  onConfirm: () => void;
  onCancel: () => void;
  returnFocusRef: RefObject<HTMLButtonElement>;
}) {
  const headingId = useId();
  const dialogRef = useDialogFocus(onCancel, returnFocusRef);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3" onClick={(event) => event.stopPropagation()}>
      <button className="absolute inset-0 bg-surface-900/35" onClick={onCancel} aria-label="Close dialog" />
      <section
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={headingId}
        className="relative w-full max-w-md rounded-xl border border-surface-200 bg-white shadow-md"
      >
        <div className="px-5 py-5">
          <h2 id={headingId} className="text-base font-semibold text-surface-800">Delete this workspace?</h2>
          <p className="mt-2 text-sm leading-6 text-surface-500">
            “{title}” and its local workspace data will be permanently removed.
          </p>
        </div>
        <div className="flex justify-end gap-2 border-t border-surface-200 px-5 py-3">
          <button data-dialog-initial-focus className="btn-ghost text-xs" onClick={onCancel}>Cancel</button>
          <button className="rounded-lg bg-red-600 px-4 py-2 text-xs font-semibold text-white hover:bg-red-700" onClick={onConfirm}>
            Delete workspace
          </button>
        </div>
      </section>
    </div>
  );
}

function useDialogFocus<T extends HTMLElement>(
  onClose: () => void,
  returnFocusRef?: RefObject<HTMLElement>,
) {
  const dialogRef = useRef<T>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const focusableSelector = [
      "button:not([disabled])",
      "input:not([disabled])",
      "textarea:not([disabled])",
      "select:not([disabled])",
      "[href]",
      "[tabindex]:not([tabindex='-1'])",
    ].join(",");

    const focusInitial = window.requestAnimationFrame(() => {
      const initial = dialog?.querySelector<HTMLElement>("[data-dialog-initial-focus]");
      const first = dialog?.querySelector<HTMLElement>(focusableSelector);
      (initial ?? first ?? dialog)?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialog) return;

      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector));
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusInitial);
      document.removeEventListener("keydown", handleKeyDown);
      (returnFocusRef?.current ?? previousFocus)?.focus();
    };
  }, [returnFocusRef]);

  return dialogRef;
}

function formatRelative(timestamp: number): string {
  const diff = Date.now() - timestamp;
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(timestamp).toLocaleDateString();
}
