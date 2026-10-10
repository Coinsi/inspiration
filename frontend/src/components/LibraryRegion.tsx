import { useRef, useState } from "react";
import type { SearchBBox } from "@/lib/library";
import { useI18n } from "@/lib/i18n";

export function RegionOverlay({
  bbox,
  width,
  height,
}: {
  bbox: SearchBBox;
  width: number;
  height: number;
}) {
  if (!width || !height) return null;
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
    >
      <rect
        x={bbox.x * width}
        y={bbox.y * height}
        width={bbox.w * width}
        height={bbox.h * height}
        fill="rgba(34,211,238,0.08)"
        stroke="#22d3ee"
        strokeWidth="3"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export function LibraryRegionSelector({
  src,
  label,
  value,
  onChange,
}: {
  src: string;
  label: string;
  value: SearchBBox | null;
  onChange: (box: SearchBBox | null) => void;
}) {
  const zh = useI18n().lang === "zh";
  const image = useRef<HTMLImageElement>(null);
  const anchor = useRef<{ x: number; y: number } | null>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const point = (event: React.PointerEvent) => {
    const rect = image.current?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return null;
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  };
  return (
    <div className="mt-3 space-y-2 rounded-lg border bg-elevated p-3">
      <p className="text-xs text-muted-foreground">
        {zh
          ? "拖动画框，选择要找的人脸或物体。也可用下方数值调整区域；不框选时由模型选择首个候选。"
          : "Drag to select one face or object, or adjust the values below. Without a region the model picks its first candidate."}
      </p>
      <div
        className="relative inline-block max-w-full touch-none select-none overflow-hidden rounded"
        onPointerDown={(event) => {
          if (event.button !== 0 || !dimensions.width) return;
          const p = point(event);
          if (!p) return;
          anchor.current = p;
          event.currentTarget.setPointerCapture(event.pointerId);
          onChange(null);
        }}
        onPointerMove={(event) => {
          const a = anchor.current,
            b = point(event);
          if (!a || !b) return;
          const w = Math.abs(a.x - b.x),
            h = Math.abs(a.y - b.y);
          if (w > 0.005 && h > 0.005)
            onChange({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w, h });
        }}
        onPointerUp={() => {
          anchor.current = null;
        }}
        onPointerCancel={() => {
          anchor.current = null;
          onChange(null);
        }}
      >
        <img
          ref={image}
          src={src}
          alt={label}
          draggable={false}
          className="block max-h-72 max-w-full"
          onLoad={(event) =>
            setDimensions({
              width: event.currentTarget.naturalWidth,
              height: event.currentTarget.naturalHeight,
            })
          }
        />
        {value && <RegionOverlay bbox={value} {...dimensions} />}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        {(["x", "y", "w", "h"] as const).map((key) => (
          <label key={key} className="space-y-1 text-xs text-muted-foreground">
            <span className="block">
              {
                {
                  x: zh ? "左侧 %" : "Left %",
                  y: zh ? "顶部 %" : "Top %",
                  w: zh ? "宽度 %" : "Width %",
                  h: zh ? "高度 %" : "Height %",
                }[key]
              }
            </span>
            <input
              type="number"
              min={key === "w" || key === "h" ? 1 : 0}
              max={100}
              step={1}
              className="h-8 w-20 rounded border bg-bg px-2"
              value={value ? Math.round(value[key] * 100) : ""}
              onChange={(event) => {
                const n = Number(event.target.value);
                if (!Number.isFinite(n)) return;
                const next = {
                  ...(value || { x: 0, y: 0, w: 1, h: 1 }),
                  [key]: Math.max(0, Math.min(1, n / 100)),
                };
                next.x = Math.min(next.x, 0.99);
                next.y = Math.min(next.y, 0.99);
                next.w = Math.max(0.01, Math.min(next.w, 1 - next.x));
                next.h = Math.max(0.01, Math.min(next.h, 1 - next.y));
                onChange(next);
              }}
            />
          </label>
        ))}
        <button
          type="button"
          className="studio-link py-2 text-xs"
          onClick={() => onChange(null)}
        >
          {zh ? "清除区域" : "Clear region"}
        </button>
      </div>
    </div>
  );
}
