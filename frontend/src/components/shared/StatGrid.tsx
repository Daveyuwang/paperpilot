interface StatItem {
  label: string;
  value: number;
}

interface Props {
  stats: StatItem[];
  columns?: 2 | 3;
}

export function StatGrid({ stats, columns = 2 }: Props) {
  const gridClass = columns === 3 ? "grid-cols-3" : "grid-cols-2";
  return (
    <dl className={`grid ${gridClass} divide-x divide-surface-200 border-y border-surface-200`}>
      {stats.map((s) => (
        <div key={s.label} className="px-3 py-2.5 text-center">
          <dt className="text-xs text-surface-500">{s.label}</dt>
          <dd className="mt-0.5 text-sm font-semibold tabular-nums text-surface-800">{s.value}</dd>
        </div>
      ))}
    </dl>
  );
}
