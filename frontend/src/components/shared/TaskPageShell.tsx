import type { ReactNode } from "react";

interface Props {
  icon: ReactNode;
  title: string;
  description?: string;
  children: ReactNode;
}

export function TaskPageShell({ icon, title, description, children }: Props) {
  return (
    <div className="flex h-full min-w-0 flex-col bg-white">
      <header className="flex flex-shrink-0 items-start gap-2.5 border-b border-surface-200 bg-white px-5 py-4 sm:px-8">
        <span className="mt-0.5" aria-hidden="true">{icon}</span>
        <div className="min-w-0">
          <h1 className="text-base font-semibold tracking-[-0.015em] text-surface-900">{title}</h1>
        {description && (
            <p className="mt-0.5 text-xs text-surface-500">{description}</p>
        )}
        </div>
      </header>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="mx-auto max-w-[1000px] space-y-6 px-5 py-6 sm:px-8 sm:py-8">
          {children}
        </div>
      </div>
    </div>
  );
}
