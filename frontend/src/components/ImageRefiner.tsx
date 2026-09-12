import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, blobUrl, type GenJob, type Generation } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useI18n } from "@/lib/i18n";

export function ImageRefiner({
  projectId,
  generation,
  onClose,
  onJob,
}: {
  projectId: string;
  generation: Generation;
  onClose: () => void;
  onJob: (id: string) => void;
}) {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const toast = useToast();
  const [crop, setCrop] = useState([0, 0, 1, 1]);
  const [rotate, setRotate] = useState(0);
  const [scale, setScale] = useState(1);
  const [brightness, setBrightness] = useState(1);
  const [contrast, setContrast] = useState(1);
  const [source, setSource] = useState<HTMLImageElement | null>(null);
  const [error, setError] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const anchor = useRef<number[] | null>(null);
  useEffect(() => {
    const c = canvas.current;
    if (!c || !source) return;
    const [x, y, w, h] = crop;
    const sw = Math.max(1, Math.round(source.naturalWidth * w));
    const sh = Math.max(1, Math.round(source.naturalHeight * h));
    const ratio = Math.min(1, 800 / Math.max(sw, sh));
    const cw = Math.round(sw * ratio),
      ch = Math.round(sh * ratio);
    c.width = rotate % 180 ? ch : cw;
    c.height = rotate % 180 ? cw : ch;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "white";
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.translate(c.width / 2, c.height / 2);
    ctx.rotate((rotate * Math.PI) / 180);
    ctx.filter = `brightness(${brightness}) contrast(${contrast})`;
    ctx.drawImage(
      source,
      Math.round(x * source.naturalWidth),
      Math.round(y * source.naturalHeight),
      sw,
      sh,
      -cw / 2,
      -ch / 2,
      cw,
      ch,
    );
  }, [source, crop, rotate, brightness, contrast]);
  const save = useMutation({
    mutationFn: () =>
      api.post<GenJob>(`/projects/${projectId}/generations/${generation.id}/refine`, {
        crop,
        rotate,
        scale,
        brightness,
        contrast,
      }),
    onSuccess: (j) => {
      onJob(j.id);
      onClose();
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const reset = () => {
    setCrop([0, 0, 1, 1]);
    setRotate(0);
    setScale(1);
    setBrightness(1);
    setContrast(1);
  };
  const point = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    ];
  };
  const changeCrop = (index: number, v: number) =>
    setCrop((c) => {
      const n = [...c];
      n[index] = Math.max(index < 2 ? 0 : 0.01, Math.min(1, v));
      n[0] = Math.min(n[0], 0.99);
      n[1] = Math.min(n[1], 0.99);
      n[2] = Math.min(n[2], 1 - n[0]);
      n[3] = Math.min(n[3], 1 - n[1]);
      return n;
    });
  const outW = source ? Math.round(source.naturalWidth * crop[2] * scale) : 0;
  const outH = source ? Math.round(source.naturalHeight * crop[3] * scale) : 0;
  const oversize = Math.max(outW, outH) > 8192 || outW * outH > 40_000_000;
  return (
    <Modal open onClose={onClose} title={zh ? "图片精修" : "Refine image"} width={960}>
      <p className="mb-4 text-xs text-muted-foreground">
        {zh
          ? "在原图上拖动选择裁剪区域，也可输入比例。保存后生成新版本，原图保留。"
          : "Drag on the source to crop, or enter proportions. Save creates a new variant."}
      </p>
      <div className="grid gap-5 md:grid-cols-2">
        <section className="min-w-0 space-y-3">
          <h3 className="text-sm font-medium">{zh ? "原图 / 裁剪选区" : "Source / crop"}</h3>
          <div
            className="relative touch-none select-none overflow-hidden rounded-lg border border-border"
            onPointerDown={(e) => {
              if (!source) return;
              anchor.current = point(e);
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => {
              if (!anchor.current) return;
              const [x, y] = point(e);
              const [ax, ay] = anchor.current;
              if (Math.abs(x - ax) > 0.01 && Math.abs(y - ay) > 0.01)
                setCrop([Math.min(x, ax), Math.min(y, ay), Math.abs(x - ax), Math.abs(y - ay)]);
            }}
            onPointerUp={() => {
              anchor.current = null;
            }}
            onPointerCancel={() => {
              anchor.current = null;
            }}
          >
            <img
              src={blobUrl(projectId, generation.output_blob_hash!)}
              alt={zh ? "精修原图" : "Source image"}
              draggable={false}
              className="block w-full"
              onLoad={(e) => setSource(e.currentTarget)}
              onError={() => setError(true)}
            />
            {source && (
              <div
                className="pointer-events-none absolute border-2 border-white"
                style={{
                  left: `${crop[0] * 100}%`,
                  top: `${crop[1] * 100}%`,
                  width: `${crop[2] * 100}%`,
                  height: `${crop[3] * 100}%`,
                  boxShadow: "0 0 0 9999px rgb(0 0 0 / .45)",
                }}
              />
            )}
          </div>
          {error && (
            <p role="alert" className="text-xs text-danger">
              {zh ? "原图不可读，请检查存储或重新上传。" : "Source unavailable. Check storage or re-upload."}
            </p>
          )}
          <div className="grid grid-cols-4 gap-2">
            {[
              zh ? "左侧 %" : "Left %",
              zh ? "顶部 %" : "Top %",
              zh ? "宽度 %" : "Width %",
              zh ? "高度 %" : "Height %",
            ].map((label, i) => (
              <label key={i} className="text-xs text-muted-foreground">
                {label}
                <input
                  type="number"
                  min={i < 2 ? 0 : 1}
                  max={100}
                  step={1}
                  className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
                  value={Math.round(crop[i] * 100)}
                  onChange={(e) => changeCrop(i, Number(e.target.value) / 100)}
                />
              </label>
            ))}
          </div>
        </section>
        <section className="min-w-0 space-y-3">
          <h3 className="text-sm font-medium">{zh ? "效果预览" : "Preview"}</h3>
          <div className="flex min-h-40 items-center justify-center overflow-hidden rounded-lg border bg-elevated p-2">
            <canvas ref={canvas} className="max-h-64 max-w-full object-contain" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs">
              {zh ? "顺时针旋转" : "Clockwise rotation"}
              <select
                aria-label={zh ? "旋转角度" : "Rotation"}
                className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
                value={rotate}
                onChange={(e) => setRotate(Number(e.target.value))}
              >
                {[0, 90, 180, 270].map((n) => (
                  <option key={n} value={n}>
                    {n}°
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs">
              {zh ? "尺寸倍率" : "Resize scale"}
              <select
                className="mt-1 h-9 w-full rounded-md border bg-bg px-2"
                value={scale}
                onChange={(e) => setScale(Number(e.target.value))}
              >
                {[0.5, 1, 2, 4].map((n) => (
                  <option key={n} value={n}>
                    {n}×
                  </option>
                ))}
              </select>
            </label>
          </div>
          {[
            [zh ? "亮度" : "Brightness", brightness, setBrightness],
            [zh ? "对比度" : "Contrast", contrast, setContrast],
          ].map(([label, value, setter]) => (
            <label key={label as string} className="block text-xs">
              {label as string} · {Math.round((value as number) * 100)}%
              <input
                type="range"
                min=".1"
                max="2"
                step=".05"
                value={value as number}
                onChange={(e) => (setter as (v: number) => void)(Number(e.target.value))}
                className="mt-2 block w-full"
              />
            </label>
          ))}
          <p className={`text-xs ${oversize ? "text-danger" : "text-muted-foreground"}`}>
            {zh ? "输出" : "Output"}: {rotate % 180 ? outH : outW} × {rotate % 180 ? outW : outH} px · PNG
          </p>
          <p className="text-xs leading-5 text-muted-foreground">
            {zh
              ? "尺寸放大使用图像插值；AI 重绘可返回生成区选择已接入的图片供应商。"
              : "Resizing uses image interpolation. AI edits are available in generation with a supported provider."}
          </p>
        </section>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={reset}>
          {zh ? "重置" : "Reset"}
        </Button>
        <Button disabled={!source || error || oversize || save.isPending} onClick={() => save.mutate()}>
          {zh ? "保存为新版本" : "Save new variant"}
        </Button>
      </div>
    </Modal>
  );
}
