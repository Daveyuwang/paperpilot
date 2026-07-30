import {
  ArrowLeft,
  BookOpen,
  FileText,
  FlaskConical,
  LayoutDashboard,
  MessageSquare,
  Settings,
} from "lucide-react";
import clsx from "clsx";
import { useWorkspaceStore, type NavItem } from "@/store/workspaceStore";

const NAV_ITEMS: { id: NavItem; label: string; icon: React.ElementType }[] = [
  { id: "workspace", label: "Overview", icon: LayoutDashboard },
  { id: "console", label: "Ask", icon: MessageSquare },
  { id: "reader", label: "Library", icon: BookOpen },
  { id: "deep-research", label: "Research", icon: FlaskConical },
  { id: "proposal", label: "Write", icon: FileText },
];

interface Props {
  onSettingsClick: () => void;
}

export function SidebarNav({ onSettingsClick }: Props) {
  const { selectedNav, setSelectedNav, getActiveWorkspace, goHome } = useWorkspaceStore();
  const workspace = getActiveWorkspace();

  return (
    <>
      <aside className="hidden h-full w-56 flex-shrink-0 flex-col border-r border-surface-200 bg-surface-50 md:flex">
        <div className="border-b border-surface-200 px-3 py-3">
          <button
            onClick={goHome}
            className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-surface-100"
            aria-label="Back to workspaces"
          >
            <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-accent-600 text-[11px] font-bold text-white" aria-hidden="true">
              P
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-xs font-semibold text-surface-800">PaperPilot</span>
              <span className="block truncate text-xs text-surface-400">{workspace?.title ?? "Workspace"}</span>
            </span>
            <ArrowLeft className="h-3.5 w-3.5 flex-shrink-0 text-surface-400" aria-hidden="true" />
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-2 py-3" aria-label="Workspace">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setSelectedNav(id)}
              className={clsx(selectedNav === id ? "nav-item-active" : "nav-item")}
              aria-current={selectedNav === id ? "page" : undefined}
            >
              <Icon className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </nav>

        <div className="border-t border-surface-200 px-2 py-3">
          <button onClick={onSettingsClick} className="nav-item">
            <Settings className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            <span>Settings</span>
          </button>
        </div>
      </aside>

      <div className="flex w-full flex-shrink-0 flex-col border-b border-surface-200 bg-surface-50 md:hidden">
        <div className="flex h-12 items-center gap-2 px-3">
          <button
            className="rounded-lg p-2 text-surface-500 hover:bg-surface-100 hover:text-surface-800"
            onClick={goHome}
            aria-label="Back to workspaces"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          </button>
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-surface-800">
            {workspace?.title ?? "Workspace"}
          </span>
          <button
            className="rounded-lg p-2 text-surface-500 hover:bg-surface-100 hover:text-surface-800"
            onClick={onSettingsClick}
            aria-label="Open settings"
          >
            <Settings className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <nav className="grid grid-cols-5 gap-1 px-2 pb-2" aria-label="Workspace">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setSelectedNav(id)}
              className={clsx(
                "flex min-h-11 min-w-0 flex-col items-center justify-center gap-1 rounded-lg px-1 py-1.5 text-[10px] font-medium leading-none transition-colors",
                selectedNav === id
                  ? "bg-accent-50 text-accent-700"
                  : "text-surface-500 hover:bg-surface-100 hover:text-surface-800",
              )}
              aria-current={selectedNav === id ? "page" : undefined}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              <span className="whitespace-nowrap">{label}</span>
            </button>
          ))}
        </nav>
      </div>
    </>
  );
}
