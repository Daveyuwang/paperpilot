import { useEffect, useMemo, useState } from "react";
import { X, Eye, EyeOff, Trash2, ChevronDown } from "lucide-react";
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
    setError("");
    setSuccess("");
    setApiKey("");
    setShowKey(false);
    setLoading(true);
    api.getLLMSettings()
      .then((r) => {
        setProtocol(r.protocol);
        setBaseUrl((r.protocol === "openai" || r.protocol === "gemini" || r.protocol === "anthropic")
          ? defaultBaseUrlForProtocol
          : (r.base_url ?? ""));
        setModel((r as any).model ?? "claude-sonnet-4-6");
        setLanguage((r as any).language ?? "en");
        setRemoteHasKey(r.has_key);
        setLLMSettingsLocal({ protocol: r.protocol, baseUrl: r.base_url ?? "", hasKey: r.has_key, language: (r as any).language ?? "en" });
      })
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [open, setLLMSettingsLocal]);

  if (!open) return null;

  const saveDisabled = saving || loading || !protocol || (!remoteHasKey && apiKey.trim().length === 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-[520px] max-w-[calc(100vw-24px)] rounded-2xl border border-white/10 bg-surface-900 shadow-xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <div>
            <div className="text-sm font-semibold text-gray-100">Settings</div>
            <div className="text-xs text-gray-500">LLM provider configuration (stored server-side per guest)</div>
          </div>
          <button className="btn-ghost p-1" onClick={onClose} title="Close">
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>

        <div className="px-4 py-4 space-y-4">
          {error && (
            <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
              {error}
            </div>
          )}
          {success && (
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
              {success}
            </div>
          )}

          <div className="space-y-2">
            <label className="text-xs font-semibold text-gray-400">Protocol</label>
            <div className="relative">
              <select
                className="w-full appearance-none rounded-xl border border-white/10 bg-surface-900 px-3 py-2 pr-9 text-sm text-gray-100 outline-none focus:border-white/20"
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
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold text-gray-400">Base URL</label>
            <input
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 outline-none focus:border-white/20"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={baseUrlPlaceholder}
              disabled={loading || saving || baseUrlLocked}
            />
            <div className="text-[11px] text-gray-600">
              For OpenAI-compatible providers, base URL should usually include <span className="text-gray-500">/v1</span>.
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold text-gray-400">Model</label>
            <input
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 outline-none focus:border-white/20"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder={defaultModelForProtocol}
              disabled={loading || saving}
            />
            {protocol === "openai_compatible" && (
              <div className="text-[11px] text-gray-600">
                Routers often require provider-prefixed model IDs, e.g. <span className="text-gray-500">anthropic/claude-sonnet-4-6</span>.
              </div>
            )}
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold text-gray-400">Trail language</label>
            <div className="relative">
              <select
                className="w-full appearance-none rounded-xl border border-white/10 bg-surface-900 px-3 py-2 pr-9 text-sm text-gray-100 outline-none focus:border-white/20"
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
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
            </div>
            <div className="text-[11px] text-gray-600">
              This affects newly generated guided trail questions.
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <label className="text-xs font-semibold text-gray-400">API key</label>
              <span className={clsx(
                "text-[11px] font-semibold px-2 py-0.5 rounded-full border",
                remoteHasKey
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                  : "border-amber-500/30 bg-amber-500/10 text-amber-200"
              )}>
                {remoteHasKey ? "Saved" : "Missing"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <input
                className="flex-1 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 outline-none focus:border-white/20"
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
                {showKey ? <EyeOff className="w-4 h-4 text-gray-400" /> : <Eye className="w-4 h-4 text-gray-400" />}
              </button>
            </div>
            <div className="text-[11px] text-gray-600">
              {remoteHasKey ? "A key is saved for this guest." : "No key saved yet — you must add one to use the app."}
            </div>
          </div>
        </div>

        <div className="px-4 py-3 border-t border-white/10 flex items-center justify-between gap-2">
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
                setModel((r as any).model ?? "claude-sonnet-4-6");
                setLanguage((r as any).language ?? "en");
                setApiKey("");
                setLLMSettingsLocal({ protocol: r.protocol, baseUrl: r.base_url ?? "", hasKey: r.has_key, language: (r as any).language ?? "en" });
                setSuccess("Cleared server-side settings.");
              } catch (e: any) {
                setError(String(e?.message ?? e));
              } finally {
                setClearing(false);
              }
            }}
            title="Clear server-side key/settings"
          >
            <Trash2 className="w-4 h-4 text-gray-400" />
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
                  ? "bg-white/5 text-gray-600 cursor-not-allowed"
                  : "bg-accent-600/30 text-accent-200 hover:bg-accent-600/40"
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
                  setLLMSettingsLocal({ protocol: r.protocol, baseUrl: r.base_url ?? "", hasKey: r.has_key, language: (r as any).language ?? language ?? "en" });
                  setSuccess("Saved.");
                } catch (e: any) {
                  setError(String(e?.message ?? e));
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

