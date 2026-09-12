import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, blobUrl, type GenJob, type Generation } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

type Stroke = { points: [number, number][]; radius: number; erase: boolean };
export function ImageInpainter({
  projectId,
  generation,
  provider,
  onClose,
  onJob,
}: {
  projectId: string;
  generation: Generation;
  provider: string;
  onClose: () => void;
  onJob: (id: string) => void;
}) {
  const zh = useI18n().lang === "zh";
  const [size, setSize] = useState<[number, number] | null>(null);
  const [failed, setFailed] = useState(false);
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [redo, setRedo] = useState<Stroke[]>([]);
  const [radius, setRadius] = useState(0.04);
  const [erase, setErase] = useState(false);
  const [prompt, setPrompt] = useState("");
  const painting = useRef(false);
  const estimate = useQuery({
    queryKey: ["inpaint-estimate", projectId, generation.id, provider],
    queryFn: () =>
      api.post<{ points: number }>(
        `/projects/${projectId}/${generation.target_type}s/${generation.target_id}/estimate`,
        {
          provider,
          count: 1,
          use_references: false,
          source_generation_id: generation.id,
        },
      ),
  });
  const save = useMutation({
    mutationFn: () =>
      api.post<GenJob>(
        `/projects/${projectId}/generations/${generation.id}/inpaint`,
        { provider, prompt, strokes },
      ),
    onSuccess: (job) => {
      onJob(job.id);
      onClose();
    },
  });
  const point = (e: React.PointerEvent<SVGSVGElement>): [number, number] => {
    const rect = e.currentTarget.getBoundingClientRect();
    return [
      Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    ];
  };
  const busy = save.isPending;
  return (
    <Modal
      open
      onClose={() => {
        if (!busy) onClose();
      }}
      title={zh ? "局部重绘" : "Edit selected region"}
      width={960}
    >
      <p className="mb-3 text-xs text-muted-foreground">
        {zh
          ? "涂亮需要修改的区域，其他区域请保留。AI 可能调整周边细节，完成后请检查；原图保留为独立版本。"
          : "Paint the region to change. AI may alter nearby details; review the result. Your original is preserved."}
      </p>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant={erase ? "ghost" : "default"}
          disabled={busy}
          onClick={() => setErase(false)}
        >
          {zh ? "画笔" : "Brush"}
        </Button>
        <Button
          size="sm"
          variant={erase ? "default" : "ghost"}
          disabled={busy}
          onClick={() => setErase(true)}
        >
          {zh ? "擦除选区" : "Erase selection"}
        </Button>
        <label className="flex items-center gap-2 text-xs">
          {zh ? "画笔大小" : "Brush size"}
          <input
            aria-label={zh ? "画笔大小" : "Brush size"}
            type="range"
            min="0.005"
            max="0.15"
            step="0.005"
            value={radius}
            disabled={busy}
            onChange={(e) => setRadius(Number(e.target.value))}
          />
        </label>
        <Button
          size="sm"
          variant="ghost"
          disabled={!strokes.length || busy}
          onClick={() => {
            setRedo([...redo, strokes[strokes.length - 1]]);
            setStrokes(strokes.slice(0, -1));
          }}
        >
          {zh ? "撤销笔画" : "Undo stroke"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!redo.length || busy}
          onClick={() => {
            setStrokes([...strokes, redo[redo.length - 1]]);
            setRedo(redo.slice(0, -1));
          }}
        >
          {zh ? "重做笔画" : "Redo stroke"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!strokes.length || busy}
          onClick={() => {
            setStrokes([]);
            setRedo([]);
          }}
        >
          {zh ? "清空选区" : "Clear"}
        </Button>
      </div>
      <div className="mx-auto max-w-[760px] overflow-hidden rounded-lg border bg-elevated">
        <div className="relative">
          <img
            src={blobUrl(projectId, generation.output_blob_hash!)}
            alt={zh ? "局部重绘原图" : "Original for masked edit"}
            className="block w-full"
            draggable={false}
            onLoad={(e) =>
              setSize([
                e.currentTarget.naturalWidth,
                e.currentTarget.naturalHeight,
              ])
            }
            onError={() => setFailed(true)}
          />
          {size && (
            <svg
              aria-label={zh ? "涂选修改区域" : "Paint edit region"}
              role="img"
              viewBox={`0 0 ${size[0]} ${size[1]}`}
              className="absolute inset-0 h-full w-full touch-none cursor-crosshair"
              onPointerDown={(e) => {
                if (busy || strokes.length >= 100 || e.button !== 0) return;
                e.currentTarget.setPointerCapture(e.pointerId);
                painting.current = true;
                setStrokes([...strokes, { points: [point(e)], radius, erase }]);
                setRedo([]);
              }}
              onPointerMove={(e) => {
                if (!painting.current) return;
                const p = point(e);
                setStrokes((s) =>
                  s.map((v, i) =>
                    i === s.length - 1 && v.points.length < 2000
                      ? { ...v, points: [...v.points, p] }
                      : v,
                  ),
                );
              }}
              onPointerUp={() => {
                painting.current = false;
              }}
              onPointerCancel={() => {
                painting.current = false;
              }}
              onLostPointerCapture={() => {
                painting.current = false;
              }}
            >
              <defs>
                <mask id="inpaint-selection">
                  <rect width="100%" height="100%" fill="black" />
                  {strokes.map((s, i) => (
                    <g
                      key={i}
                      fill={s.erase ? "black" : "white"}
                      stroke={s.erase ? "black" : "white"}
                      strokeWidth={s.radius * Math.min(...size) * 2}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline
                        fill="none"
                        points={s.points
                          .map(
                            ([x, y]) =>
                              `${x * (size[0] - 1)},${y * (size[1] - 1)}`,
                          )
                          .join(" ")}
                      />
                      <circle
                        stroke="none"
                        cx={s.points[0][0] * (size[0] - 1)}
                        cy={s.points[0][1] * (size[1] - 1)}
                        r={s.radius * Math.min(...size)}
                      />
                    </g>
                  ))}
                </mask>
              </defs>
              <rect
                width="100%"
                height="100%"
                fill="#e6b85c"
                fillOpacity="0.6"
                mask="url(#inpaint-selection)"
              />
            </svg>
          )}
        </div>
      </div>
      <label className="mt-4 block text-sm">
        {zh ? "修改要求" : "Edit instruction"}
        <textarea
          aria-label={zh ? "修改要求" : "Edit instruction"}
          rows={3}
          maxLength={8000}
          value={prompt}
          disabled={busy}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={
            zh
              ? "例如：把选中的衣服改为深蓝色，保留褶皱和光照"
              : "Describe the change within the selection"
          }
          className="mt-1 w-full rounded-md border bg-bg p-2"
        />
      </label>
      {(failed || save.error || estimate.error) && (
        <p role="alert" className="mt-2 text-xs text-danger">
          {failed
            ? zh
              ? "原图加载失败"
              : "Image failed to load"
            : (save.error || estimate.error)?.message}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {zh
            ? `生成 1 张新版本 · ${provider} · 预计 ${estimate.data?.points ?? "…"} 点`
            : `1 new variant · ${provider} · estimated ${estimate.data?.points ?? "…"} points`}
        </span>
        <Button
          disabled={
            busy ||
            !size ||
            failed ||
            !strokes.some((s) => !s.erase) ||
            !prompt.trim() ||
            !estimate.data
          }
          onClick={() => save.mutate()}
        >
          {busy
            ? zh
              ? "正在提交…"
              : "Submitting…"
            : zh
              ? "提交局部重绘"
              : "Generate masked edit"}
        </Button>
      </div>
    </Modal>
  );
}
