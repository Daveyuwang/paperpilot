import type { ReactNode } from "react";

interface Props {
  icon: ReactNode;
  title: string;
  description?: string;
  children: ReactNode;
}

export function TaskPageShell({ icon, title, description, children }: Props) {
  return (
    <div className="flex flex-col h-full min-w-0">
      <div className="flex-shrink-0 flex items-center gap-2 px-6 py-3 border-b border-surface-200 bg-white">
        {icon}
        <h2 className="heading-serif text-base text-surface-800">{title}</h2>
        {description && (
          <span className="text-xs text-surface-400 truncate hidden md:block">{description}</span>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="max-w-[1000px] mx-auto px-6 py-6 space-y-6">
          {children}
        </div>
      </div>
    </div>
  );
}
