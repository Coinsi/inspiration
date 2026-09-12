// 人物/资产图谱:气泡布局 —— 引用镜头最多的居中放大,其余按黄金角螺旋环绕,大小随出场量。
import { blobUrl, type Asset } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export default function AssetGraph({
  assets,
  projectId,
  onOpen,
}: {
  assets: Asset[];
  projectId: string;
  onOpen: (id: string) => void;
}) {
  const { t: tr } = useI18n();
  const sorted = [...assets].sort((a, b) => (b.shot_count ?? 0) - (a.shot_count ?? 0));
  const max = Math.max(sorted[0]?.shot_count ?? 0, 1);

  const placed = sorted.map((a, i) => {
    if (i === 0) return { a, x: 0, y: 0, size: 200 };
    const ratio = (a.shot_count ?? 0) / max;
    const size = 84 + ratio * 64;
    const angle = i * 2.39996; // 黄金角(弧度),天然错开
    const dist = 165 + 46 * Math.sqrt(i) * 1.9;
    return { a, x: Math.cos(angle) * dist, y: Math.sin(angle) * dist * 0.74, size };
  });

  const width = Math.max(600, ...placed.map(p => (Math.abs(p.x) + p.size / 2 + 24) * 2));
  const height = Math.max(500, ...placed.map(p => (Math.abs(p.y) + p.size / 2 + 24) * 2));

  if (assets.length === 0) {
    return <p className="py-20 text-center text-sm text-muted-foreground">{tr("assets.empty")}</p>;
  }

  return (
    <div className="max-h-[70vh] overflow-auto rounded-lg border bg-surface" tabIndex={0} aria-label={tr("assets.viewGraph")}>
      <div className="relative" style={{ width, height }}>
      {placed.map(({ a, x, y, size }) => (
        <button
          key={a.id}
          onClick={() => onOpen(a.id)}
          title={`${a.name} · ${a.shot_count ?? 0} ${tr("assets.shotUnit")}`}
          className="group absolute overflow-hidden rounded-full ring-1 ring-border transition-all duration-200 hover:z-20 hover:scale-105 hover:ring-2 hover:ring-primary "
          style={{
            width: size,
            height: size,
            left: `calc(50% + ${x}px)`,
            top: `calc(50% + ${y}px)`,
            transform: "translate(-50%, -50%)",
            background: "hsl(var(--elevated))",
          }}
        >
          {a.representative_blob_hash && (
            <img
              src={blobUrl(projectId, a.representative_blob_hash)}
              alt={a.name}
              className="absolute inset-0 h-full w-full object-cover"
            />
          )}
          {/* 文字压层:有图时加暗化保证可读 */}
          <span
            className={`absolute inset-0 flex flex-col items-center justify-center px-2 text-center ${
              a.representative_blob_hash ? "bg-black/35 group-hover:bg-black/25" : ""
            }`}
          >
            <span
              className={`max-w-full truncate font-semibold ${a.representative_blob_hash ? "text-white" : "text-foreground"}`}
              style={{ fontSize: Math.max(12, size * 0.105) }}
            >
              {a.name}
            </span>
            <span className={a.representative_blob_hash ? "text-white/80" : "text-muted-foreground"} style={{ fontSize: Math.max(10, size * 0.075) }}>
              {a.shot_count ?? 0} {tr("assets.shotUnit")}
            </span>
          </span>
        </button>
      ))}
      </div>
    </div>
  );
}
