import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { LLMProtocol } from "@/types";

interface SettingsState {
  llmProtocol: LLMProtocol;
  llmBaseUrl: string;
  hasKey: boolean;
  trailLanguage: string;
  setLLMSettingsLocal: (next: { protocol: LLMProtocol; baseUrl: string; hasKey: boolean; language?: string }) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      llmProtocol: "anthropic",
      llmBaseUrl: "",
      hasKey: false,
      trailLanguage: "en",
      setLLMSettingsLocal: (next) => set({
        llmProtocol: next.protocol,
        llmBaseUrl: next.baseUrl,
        hasKey: next.hasKey,
        trailLanguage: next.language ?? "en",
      }),
    }),
    {
      name: "pp_settings",
      storage: createJSONStorage(() => localStorage),
    }
  )
);

