import {
  ArrowLeft,
  LayoutDashboard,
  MessageSquare,
  BookOpen,
  FlaskConical,
  FileText,
  Settings,
} from "lucide-react";
import clsx from "clsx";
import { useWorkspaceStore, type NavItem } from "@/store/workspaceStore";

const NAV_ITEMS: { id: NavItem; label: string; icon: React.ElementType }[] = [
  { id: "workspace",     label: "Workspace",     icon: LayoutDashboard },
  { id: "console",       label: "Console",       icon: MessageSquare },
  { id: "reader",        label: "Reader",        icon: BookOpen },
  { id: "deep-research", label: "Deep Research", icon: FlaskConical },
  { id: "proposal",      label: "Proposal",      icon: FileText },
];

interface Props {
  onSettingsClick: () => void;
}

export function SidebarNav({ onSettingsClick }: Props) {
  const { selectedNav, setSelectedNav, getActiveWorkspace, goHome } = useWorkspaceStore();
  const workspace = getActiveWorkspace();

  return (
    <nav className="flex-shrink-0 w-44 flex flex-col border-r border-surface-200 bg-surface-50 h-full">
      {/* Back to home + workspace title */}
      <div className="px-3 py-4 border-b border-surface-200">
        <button
          onClick={goHome}
          className="flex items-center gap-2 text-left w-full group"
        >
          <ArrowLeft className="w-3.5 h-3.5 text-surface-400 group-hover:text-surface-600 transition-colors flex-shrink-0" />
          <span className="heading-serif text-sm text-surface-800 truncate">
            {workspace?.title ?? "Workspace"}
          </span>
        </button>
      </div>

      {/* Nav items */}
      <div className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setSelectedNav(id)}
            className={clsx(
              "w-full text-left",
              selectedNav === id ? "nav-item-active" : "nav-item"
            )}
          >
            <Icon className="w-4 h-4 flex-shrink-0" />
            <span>{label}</span>
          </button>
        ))}
      </div>

      {/* Settings at bottom */}
      <div className="px-2 py-3 border-t border-surface-200">
        <button
          onClick={() => {
            setSelectedNav("settings");
            onSettingsClick();
          }}
          className={clsx(
            "w-full text-left",
            selectedNav === "settings" ? "nav-item-active" : "nav-item"
          )}
        >
          <Settings className="w-4 h-4 flex-shrink-0" />
          <span>Settings</span>
        </button>
      </div>
    </nav>
  );
}
