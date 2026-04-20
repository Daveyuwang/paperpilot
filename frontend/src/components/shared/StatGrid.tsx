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
    <div className={`grid ${gridClass} gap-2`}>
      {stats.map((s) => (
        <div key={s.label} className="bg-white border border-surface-200 rounded-lg px-3 py-2">
          <div className="text-lg font-semibold text-surface-800">{s.value}</div>
          <div className="text-[10px] text-surface-400">{s.label}</div>
        </div>
      ))}
    </div>
  );
}
