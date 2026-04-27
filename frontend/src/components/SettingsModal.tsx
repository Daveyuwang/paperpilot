import { useEffect, useMemo, useState, useCallback } from "react";
import { X, Eye, EyeOff, Trash2, ChevronDown, RefreshCw, Loader2 } from "lucide-react";
import clsx from "clsx";

import type { LLMProtocol } from "@/types";
import { api } from "@/api/client";
import { useSettingsStore } from "@/store/settingsStore";

interface Props {
  open: boolean;
  onClose: () => void;
}

const protocolLabel: Record<LLMProtocol, string> = {
  openai: "OpenAI",
  openai_compatible: "OpenAI-compatible",
  anthropic: "Anthropic",
  gemini: "Gemini",
};

export function SettingsModal({ open, onClose }: Props) {
  const { llmProtocol, llmBaseUrl, hasKey, trailLanguage, setLLMSettingsLocal } = useSettingsStore();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);

  const [protocol, setProtocol] = useState<LLMProtocol>(llmProtocol);
  const [baseUrl, setBaseUrl] = useState<string>(llmBaseUrl);
  const [model, setModel] = useState<string>("claude-sonnet-4-6");
  const [language, setLanguage] = useState<string>(trailLanguage || "en");
  const [apiKey, setApiKey] = useState<string>("");
  const [showKey, setShowKey] = useState(false);
  const [remoteHasKey, setRemoteHasKey] = useState<boolean>(hasKey);
  const [error, setError] = useState<string>("");
  const [success, setSuccess] = useState<string>("");
  const [fetchFailed, setFetchFailed] = useState(false);

  const defaultBaseUrlForProtocol = useMemo(() => {
    if (protocol === "openai") return "https://api.openai.com/v1";
    if (protocol === "gemini") return "https://generativelanguage.googleapis.com";
    if (protocol === "anthropic") return "";
    return "";
  }, [protocol]);

  const baseUrlLocked = protocol === "openai" || protocol === "gemini" || protocol === "anthropic";

  const defaultModelForProtocol = useMemo(() => {
    if (protocol === "openai") return "gpt-5.4";
    if (protocol === "openai_compatible") return "anthropic/claude-sonnet-4-6";
    if (protocol === "gemini") return "gemini-3-flash-preview";
    return "claude-sonnet-4-6";
  }, [protocol]);

  const baseUrlPlaceholder = useMemo(() => {
    if (protocol === "openai") return defaultBaseUrlForProtocol;
    if (protocol === "openai_compatible") return "https://openrouter.ai/api/v1 (or any OpenAI-compatible base URL)";
    if (protocol === "gemini") return defaultBaseUrlForProtocol;
    return "Leave blank to use default";
  }, [protocol, defaultBaseUrlForProtocol]);

  const loadSettings = useCallback(() => {
    setError("");
    setFetchFailed(false);
    setLoading(true);
    api.getLLMSettings()
      .then((r) => {
        setProtocol(r.protocol);
        setBaseUrl((r.protocol === "openai" || r.protocol === "gemini" || r.protocol === "anthropic")
          ? defaultBaseUrlForProtocol
          : (r.base_url ?? ""));
        setModel(r.model ?? "claude-sonnet-4-6");
        setLanguage(r.language ?? "en");
        setRemoteHasKey(r.has_key);
        setLLMSettingsLocal({ protocol: r.protocol, baseUrl: r.base_url ?? "", hasKey: r.has_key, language: r.language ?? "en" });
      })
      .catch((e) => {
        setError(String(e?.message ?? e));
        setFetchFailed(true);
      })
      .finally(() => setLoading(false));
  }, [defaultBaseUrlForProtocol, setLLMSettingsLocal]);

  useEffect(() => {
    if (!open) return;
    if (!baseUrlLocked) return;
    setBaseUrl(defaultBaseUrlForProtocol);
  }, [open, protocol]); // avoid overriding while typing; protocol switch should reset

  useEffect(() => {
    if (!open) return;
    // If user hasn't customized model yet, adopt protocol default.
    if (model.trim() === "claude-sonnet-4-6") {
      setModel(defaultModelForProtocol);
    }
  }, [open, protocol]); // intentionally omit model/defaultModelForProtocol to avoid overriding user edits

  useEffect(() => {
    if (!open) return;
    setApiKey("");
    setShowKey(false);
    setSuccess("");
    loadSettings();
  }, [open, loadSettings]);

  if (!open) return null;

  const saveDisabled = saving || loading || !protocol || (!remoteHasKey && apiKey.trim().length === 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-[520px] max-w-[calc(100vw-24px)] rounded-2xl border border-surface-200 bg-white shadow-lg">
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-200">
          <div>
            <div className="text-sm font-semibold text-surface-800">Settings</div>
            <div className="text-xs text-surface-500">LLM provider configuration (stored server-side per guest)</div>
          </div>
          <button className="btn-ghost p-1" onClick={onClose} title="Close">
            <X className="w-4 h-4 text-surface-400" />
          </button>
        </div>

        <div className="px-4 py-4 space-y-4">
          {error && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600 flex items-center justify-between gap-2">
              <span>{error}</span>
              {fetchFailed && (
                <button
                  onClick={loadSettings}
                  className="flex items-center gap-1 px-2 py-1 rounded bg-red-100 hover:bg-red-200 text-red-700 transition-colors flex-shrink-0"
                >
                  <RefreshCw className="w-3 h-3" />
                  Retry
                </button>
              )}
            </div>
          )}
          {success && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              {success}
            </div>
          )}

          {loading && !fetchFailed ? (
            <div className="space-y-4 animate-pulse">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="space-y-2">
                  <div className="h-3 w-20 bg-surface-200 rounded" />
                  <div className="h-9 w-full bg-surface-100 rounded-xl" />
                </div>
              ))}
            </div>
          ) : (
          <>
          <div className="space-y-2">
            <label className="text-xs font-semibold text-surface-500">Protocol</label>
            <div className="relative">
              <select
                className="w-full appearance-none rounded-xl border border-surface-200 bg-surface-50 px-3 py-2 pr-9 text-sm text-surface-800 outline-none focus:ring-1 focus:ring-accent-400"
                value={protocol}
                onChange={(e) => setProtocol(e.target.value as LLMProtocol)}
                disabled={loading || saving}
              >
                {(Object.keys(protocolLabel) as LLMProtocol[]).map((p) => (
                  <option key={p} value={p}>
                    {protocolLabel[p]}
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-surface-400" />
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold text-surface-500">Base URL</label>
            <input
              className="w-full rounded-xl border border-surface-200 bg-surface-50 px-3 py-2 text-sm text-surface-800 placeholder:text-surface-400 outline-none focus:ring-1 focus:ring-accent-400"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={baseUrlPlaceholder}
              disabled={loading || saving || baseUrlLocked}
            />
            <div className="text-[11px] text-surface-400">
              For OpenAI-compatible providers, base URL should usually include <span className="text-surface-500">/v1</span>.
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold text-surface-500">Model</label>
            <input
              className="w-full rounded-xl border border-surface-200 bg-surface-50 px-3 py-2 text-sm text-surface-800 placeholder:text-surface-400 outline-none focus:ring-1 focus:ring-accent-400"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={defaultModelForProtocol}
              disabled={loading || saving}
            />
            {protocol === "openai_compatible" && (
              <div className="text-[11px] text-surface-400">
                Routers often require provider-prefixed model IDs, e.g. <span className="text-surface-500">anthropic/claude-sonnet-4-6</span>.
              </div>
            )}
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold text-surface-500">Trail language</label>
            <div className="relative">
              <select
                className="w-full appearance-none rounded-xl border border-surface-200 bg-surface-50 px-3 py-2 pr-9 text-sm text-surface-800 outline-none focus:ring-1 focus:ring-accent-400"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                disabled={loading || saving}
              >
                <option value="en">English</option>
                <option value="zh-CN">中文（简体）</option>
                <option value="zh-Hant">中文（繁體）</option>
                <option value="ja">日本語</option>
                <option value="ko">한국어</option>
                <option value="es">Español</option>
                <option value="fr">Français</option>
                <option value="de">Deutsch</option>
                <option value="pt-BR">Português (Brasil)</option>
                <option value="ru">Русский</option>
              </select>
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-surface-400" />
            </div>
            <div className="text-[11px] text-surface-400">
              This affects newly generated guided trail questions.
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <label className="text-xs font-semibold text-surface-500">API key</label>
              <span className={clsx(
                "text-[11px] font-semibold px-2 py-0.5 rounded-full border",
                remoteHasKey
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-amber-200 bg-amber-50 text-amber-700"
              )}>
                {remoteHasKey ? "Saved" : "Missing"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <input
                className="flex-1 rounded-xl border border-surface-200 bg-surface-50 px-3 py-2 text-sm text-surface-800 placeholder:text-surface-400 outline-none focus:ring-1 focus:ring-accent-400"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                type={showKey ? "text" : "password"}
                placeholder={remoteHasKey ? "Saved on server (enter to replace)" : "Paste your key here"}
                disabled={loading || saving}
              />
              <button
                className={clsx("btn-ghost p-2", (loading || saving) && "opacity-50 pointer-events-none")}
                onClick={() => setShowKey((v) => !v)}
                title={showKey ? "Hide" : "Show"}
              >
                {showKey ? <EyeOff className="w-4 h-4 text-surface-400" /> : <Eye className="w-4 h-4 text-surface-400" />}
              </button>
            </div>
            <div className="text-[11px] text-surface-400">
              {remoteHasKey ? "A key is saved for this guest." : "No key saved yet — you must add one to use the app."}
            </div>
          </div>
          </>
          )}
        </div>

        <div className="px-4 py-3 border-t border-surface-200 flex items-center justify-between gap-2">
          <button
            className={clsx("btn-ghost flex items-center gap-2 px-3 py-2 text-xs", clearing && "opacity-60 pointer-events-none")}
            onClick={async () => {
              setError("");
              setSuccess("");
              setClearing(true);
              try {
                const r = await api.clearLLMSettings();
                setRemoteHasKey(r.has_key);
                setProtocol(r.protocol);
                setBaseUrl(r.base_url ?? "");
                setModel(r.model ?? "claude-sonnet-4-6");
                setLanguage(r.language ?? "en");
                setApiKey("");
                setLLMSettingsLocal({ protocol: r.protocol, baseUrl: r.base_url ?? "", hasKey: r.has_key, language: r.language ?? "en" });
                setSuccess("Cleared server-side settings.");
              } catch (e: unknown) {
                setError(e instanceof Error ? e.message : String(e));
              } finally {
                setClearing(false);
              }
            }}
            title="Clear server-side key/settings"
          >
            <Trash2 className="w-4 h-4 text-surface-400" />
            Clear
          </button>

          <div className="flex items-center gap-2">
            <button className="btn-ghost px-3 py-2 text-xs" onClick={onClose} disabled={saving || loading}>
              Cancel
            </button>
            <button
              className={clsx(
                "px-3 py-2 rounded-xl text-xs font-semibold",
                saveDisabled
                  ? "bg-surface-100 text-surface-400 cursor-not-allowed"
                  : "bg-accent-600 text-white hover:bg-accent-500"
              )}
              disabled={saveDisabled}
              onClick={async () => {
                setError("");
                setSuccess("");
                setSaving(true);
                try {
                  const r = await api.setLLMSettings({
                    protocol,
                    base_url: (baseUrlLocked ? (defaultBaseUrlForProtocol || null) : (baseUrl.trim() ? baseUrl.trim() : null)),
                    model: model.trim() ? model.trim() : defaultModelForProtocol,
                    language: language || "en",
                    ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
                  });
                  setRemoteHasKey(r.has_key);
                  setApiKey("");
                  setLLMSettingsLocal({ protocol: r.protocol, baseUrl: r.base_url ?? "", hasKey: r.has_key, language: r.language ?? language ?? "en" });
                  setSuccess("Saved.");
                } catch (e: unknown) {
                  setError(e instanceof Error ? e.message : String(e));
                } finally {
                  setSaving(false);
                }
              }}
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

