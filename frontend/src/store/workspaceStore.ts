import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export type ViewerTab = "reader" | "deliverable" | "sources" | "agenda" | "concepts";
export type NavItem = "workspace" | "console" | "reader" | "deep-research" | "proposal" | "settings";

export interface Workspace {
  id: string;
  title: string;
  objective: string;
  createdAt: number;
  updatedAt: number;
  activePaperId: string | null;
  activeViewerTab: ViewerTab;
}

export type ConsolePanelTab = "deliverable" | "sources";

interface WorkspaceStore {
  workspaces: Record<string, Workspace>;
  activeWorkspaceId: string | null;
  appView: "home" | "shell";
  selectedNav: NavItem;
  consolePanelOpen: boolean;
  consolePanelTab: ConsolePanelTab;

  createWorkspace: (title: string, objective?: string) => Workspace;
  deleteWorkspace: (id: string) => void;
  renameWorkspace: (id: string, title: string) => void;
  setObjective: (id: string, obj: string) => void;
  openWorkspace: (id: string) => void;
  goHome: () => void;

  getActiveWorkspace: () => Workspace | null;
  setActiveViewerTab: (tab: ViewerTab) => void;
  setActivePaperId: (id: string | null) => void;
  setSelectedNav: (item: NavItem) => void;
  setConsolePanelOpen: (open: boolean) => void;
  setConsolePanelTab: (tab: ConsolePanelTab) => void;
}

const VALID_VIEWER_TABS: ViewerTab[] = ["reader", "deliverable", "sources", "agenda", "concepts"];
const VALID_NAV_ITEMS: NavItem[] = ["workspace", "console", "reader", "deep-research", "proposal", "settings"];

function wsId(): string {
  return `ws_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export const useWorkspaceStore = create<WorkspaceStore>()(
  persist(
    (set, get) => ({
      workspaces: {},
      activeWorkspaceId: null,
      appView: "home",
      selectedNav: "workspace",
      consolePanelOpen: true,
      consolePanelTab: "deliverable",

      createWorkspace: (title, objective) => {
        const id = wsId();
        const now = Date.now();
        const ws: Workspace = {
          id,
          title,
          objective: objective ?? "",
          createdAt: now,
          updatedAt: now,
          activePaperId: null,
          activeViewerTab: "reader",
        };
        set((s) => ({ workspaces: { ...s.workspaces, [id]: ws } }));
        return ws;
      },

      deleteWorkspace: (id) => {
        // Clean up related store data
        import("@/store/deliverableStore").then((m) => m.useDeliverableStore.getState().clearWorkspace(id));
        import("@/store/sourceStore").then((m) => m.useSourceStore.getState().clearWorkspace(id));
        import("@/store/chatStore").then((m) => m.useChatStore.getState().clearWorkspace(id));
        import("@/store/paperStore").then((m) => m.clearWorkspaceStorage(id));
        import("@/store/deepResearchStore").then((m) => m.useDeepResearchStore.getState().reset());
        import("@/store/proposalPlanStore").then((m) => m.useProposalPlanStore.getState().reset());

        set((s) => {
          const { [id]: _, ...rest } = s.workspaces;
          const patch: Partial<WorkspaceStore> = { workspaces: rest };
          if (s.activeWorkspaceId === id) {
            patch.activeWorkspaceId = null;
            patch.appView = "home";
          }
          return patch as Partial<WorkspaceStore>;
        });
      },

      renameWorkspace: (id, title) =>
        set((s) => {
          const ws = s.workspaces[id];
          if (!ws) return s;
          return {
            workspaces: { ...s.workspaces, [id]: { ...ws, title, updatedAt: Date.now() } },
          };
        }),

      setObjective: (id, obj) =>
        set((s) => {
          const ws = s.workspaces[id];
          if (!ws) return s;
          return {
            workspaces: { ...s.workspaces, [id]: { ...ws, objective: obj, updatedAt: Date.now() } },
          };
        }),

      openWorkspace: (id) =>
        set({ activeWorkspaceId: id, appView: "shell", selectedNav: "workspace" }),

      goHome: () => set({ appView: "home" }),

      getActiveWorkspace: () => {
        const { workspaces, activeWorkspaceId } = get();
        if (!activeWorkspaceId) return null;
        return workspaces[activeWorkspaceId] ?? null;
      },

      setActiveViewerTab: (tab) =>
        set((s) => {
          const ws = s.activeWorkspaceId ? s.workspaces[s.activeWorkspaceId] : null;
          if (!ws) return s;
          return {
            workspaces: { ...s.workspaces, [ws.id]: { ...ws, activeViewerTab: tab, updatedAt: Date.now() } },
          };
        }),

      setActivePaperId: (id) =>
        set((s) => {
          const ws = s.activeWorkspaceId ? s.workspaces[s.activeWorkspaceId] : null;
          if (!ws) return s;
          return {
            workspaces: { ...s.workspaces, [ws.id]: { ...ws, activePaperId: id, updatedAt: Date.now() } },
          };
        }),

      setSelectedNav: (item) => set({ selectedNav: item }),
      setConsolePanelOpen: (open) => set({ consolePanelOpen: open }),
      setConsolePanelTab: (tab) => set({ consolePanelTab: tab }),
    }),
    {
      name: "pp_workspace",
      storage: createJSONStorage(() => localStorage),
      onRehydrateStorage: () => (state) => {
        if (!state) return;

        // Migrate from old single-workspace shape
        const raw = state as unknown as Record<string, unknown>;
        if (raw.workspace && !raw.workspaces) {
          const old = raw.workspace as Record<string, unknown>;
          const now = Date.now();
          const migrated: Workspace = {
            id: "default",
            title: (old.title as string) || "My Research Workspace",
            objective: (old.objective as string) || "",
            createdAt: now,
            updatedAt: now,
            activePaperId: (old.activePaperId as string) ?? null,
            activeViewerTab: VALID_VIEWER_TABS.includes(old.activeViewerTab as ViewerTab)
              ? (old.activeViewerTab as ViewerTab)
              : old.activeViewerTab === "trail" ? "agenda" : "reader",
          };
          state.workspaces = { default: migrated };
          state.activeWorkspaceId = "default";
          state.appView = "shell";
          delete raw.workspace;
        }

        // Validate nav
        if (!VALID_NAV_ITEMS.includes(state.selectedNav)) {
          state.selectedNav = "workspace";
        }

        // Validate active workspace viewer tab
        if (state.activeWorkspaceId && state.workspaces[state.activeWorkspaceId]) {
          const ws = state.workspaces[state.activeWorkspaceId];
          if (!VALID_VIEWER_TABS.includes(ws.activeViewerTab)) {
            ws.activeViewerTab = "reader";
          }
        }
      },
    }
  )
);
