import type { ReactNode } from "react";

interface Props {
  icon: ReactNode;
  heading: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({ icon, heading, description, action }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-surface-400">
      <div className="opacity-30">{icon}</div>
      <div className="text-center">
        <p className="heading-serif text-sm text-surface-500">{heading}</p>
        {description && (
          <p className="text-xs text-surface-400 mt-1 max-w-[240px]">{description}</p>
        )}
      </div>
      {action && (
        <button
          onClick={action.onClick}
          className="btn-ghost text-xs px-3 py-1.5 mt-1"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
