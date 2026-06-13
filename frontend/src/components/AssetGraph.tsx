// 人物/资产图谱:气泡布局 —— 引用镜头最多的居中放大,其余按黄金角螺旋环绕,大小随出场量。
import { blobUrl, type Asset } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

// 与列表卡片一致的取色逻辑:按名字哈希,同名稳定同色
function bubbleGradient(name: string): string {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `linear-gradient(150deg, hsl(${h} 52% 44%), hsl(${(h + 45) % 360} 55% 26%))`;
}

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

  if (assets.length === 0) {
    return <p className="py-20 text-center text-sm text-muted-foreground">{tr("assets.empty")}</p>;
  }

  return (
    <div className="relative min-h-[600px] flex-1 overflow-hidden">
      {placed.map(({ a, x, y, size }) => (
        <button
          key={a.id}
          onClick={() => onOpen(a.id)}
          title={`${a.name} · ${a.shot_count ?? 0} ${tr("assets.shotUnit")}`}
          className="group absolute overflow-hidden rounded-full ring-1 ring-border transition-all duration-200 hover:z-20 hover:scale-105 hover:ring-2 hover:ring-primary hover:shadow-glow-sm"
          style={{
            width: size,
            height: size,
            left: `calc(50% + ${x}px)`,
            top: `calc(46% + ${y}px)`,
            transform: "translate(-50%, -50%)",
            background: a.representative_blob_hash ? undefined : bubbleGradient(a.name),
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
              className="max-w-full truncate font-semibold text-white drop-shadow"
              style={{ fontSize: Math.max(12, size * 0.105) }}
            >
              {a.name}
            </span>
            <span className="text-white/80 drop-shadow" style={{ fontSize: Math.max(10, size * 0.075) }}>
              {a.shot_count ?? 0} {tr("assets.shotUnit")}
            </span>
          </span>
        </button>
      ))}
    </div>
  );
}
