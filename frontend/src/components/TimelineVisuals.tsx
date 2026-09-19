import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Layers, Plus, Trash2 } from "lucide-react";
import { api, blobUrl, type Generation, type Shot } from "@/lib/api";
import {
  type TimelineContent,
  type VisualClip,
  timelineDuration,
  visualIssue,
} from "@/lib/timeline-edit";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

export function TimelineVisuals({
  projectId,
  shots,
  content,
  selected,
  onSelect,
  onChange,
  disabled,
}: {
  projectId: string;
  shots: Shot[];
  content: TimelineContent;
  selected: string;
  onSelect: (id: string, time: number) => void;
  onChange: (c: TimelineContent) => void;
  disabled: boolean;
}) {
  const zh = useI18n().lang === "zh";
  const [addError, setAddError] = useState("");
  const [shot, setShot] = useState("");
  const [generation, setGeneration] = useState("");
  const total = timelineDuration(content.items),
    visuals = content.visuals;
  const current = visuals.find((v) => v.id === selected) || visuals[0];
  const sources = useQuery({
    queryKey: ["gens", "shot", shot],
    enabled: !!shot,
    queryFn: () =>
      api.get<Generation[]>(
        `/projects/${projectId}/generations?target_type=shot&target_id=${shot}`,
      ),
  });
  const candidate = sources.data?.find((g) => g.id === generation);
  const info = useQuery({
    queryKey: ["video-info", projectId, generation],
    enabled: candidate?.output_type === "video",
    queryFn: () =>
      api.get<{ duration_ms: number }>(
        `/projects/${projectId}/generations/${generation}/video-info`,
      ),
  });
  const change = (next: VisualClip[]) =>
    onChange({ ...content, visuals: next });
  const patch = (next: Partial<VisualClip>) =>
    change(visuals.map((v) => (v.id === current?.id ? { ...v, ...next } : v)));
  const add = () => {
    let track = 1,
      start = 0;
    const length = Math.min(
      3000,
      total,
      candidate?.output_type === "video" ? info.data?.duration_ms || 0 : total,
    );
    for (; track <= 3; track++) {
      start = 0;
      for (const v of visuals
        .filter((v) => v.track === track)
        .sort((a, b) => a.start_ms - b.start_ms)) {
        if (v.start_ms - start >= length) break;
        start = Math.max(start, v.start_ms + v.duration_ms);
      }
      if (start + length <= total) break;
    }
    if (track > 3 || length < 100) {
      setAddError(
        zh
          ? "轨道没有足够空位，请缩短已有叠加画面或延长主时间线。"
          : "No free track space; shorten a layer or extend the timeline.",
      );
      return;
    }
    setAddError("");
    const v: VisualClip = {
      id: crypto.randomUUID(),
      generation_id: generation,
      name:
        shots.find((s) => s.id === shot)?.title ||
        (zh ? "叠加画面" : "Overlay"),
      track,
      start_ms: start,
      duration_ms: length,
      in_point_ms: 0,
      x: 0.6,
      y: 0.6,
      width: 0.35,
      height: 0.35,
      opacity: 1,
      fit: "contain",
      hidden: false,
    };
    change([...visuals, v]);
    onSelect(v.id, v.start_ms);
  };
  const number = (
    label: string,
    key:
      | "start_ms"
      | "in_point_ms"
      | "duration_ms"
      | "x"
      | "y"
      | "width"
      | "height"
      | "opacity",
    scale: number,
    min: number,
    max: number,
  ) => (
    <label>
      {label}
      <input
        type="number"
        aria-label={label}
        min={min}
        max={max}
        step={scale === 1000 ? 0.1 : 1}
        value={Number((current[key] / scale).toFixed(3))}
        onChange={(e) =>
          patch({
            [key]: Math.round(Number(e.target.value) * scale * 100000) / 100000,
          })
        }
      />
    </label>
  );
  return (
    <fieldset disabled={disabled} className="timeline-visual-editor">
      <header>
        <div>
          <Layers size={18} />
          <h3>{zh ? "叠加画面" : "Visual layers"}</h3>
          <span>{visuals.length}/12</span>
        </div>
        <p>
          {zh
            ? "三条叠加轨道，轨道 3 位于最上方。视频原声请通过声音轨道单独添加。"
            : "Three overlay tracks; track 3 is on top. Add video sound separately on audio tracks."}
        </p>
      </header>
      <div className="timeline-visual-add">
        <select
          aria-label={zh ? "叠加素材镜头" : "Overlay shot"}
          value={shot}
          onChange={(e) => {
            setShot(e.target.value);
            setGeneration("");
          }}
        >
          <option value="">
            {zh ? "选择素材所在镜头" : "Choose a source shot"}
          </option>
          {shots.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title || s.code}
            </option>
          ))}
        </select>
        <select
          aria-label={zh ? "叠加素材版本" : "Overlay version"}
          value={generation}
          onChange={(e) => setGeneration(e.target.value)}
        >
          <option value="">
            {sources.isFetching
              ? zh
                ? "读取素材…"
                : "Loading…"
              : zh
                ? "选择图片或视频版本"
                : "Choose image or video"}
          </option>
          {sources.data
            ?.filter(
              (g) =>
                g.output_blob_hash &&
                ["image", "video"].includes(g.output_type),
            )
            .map((g, i) => (
              <option key={g.id} value={g.id}>
                {g.output_type === "image"
                  ? zh
                    ? "图片"
                    : "Image"
                  : zh
                    ? "视频"
                    : "Video"}{" "}
                {i + 1} · {g.id.slice(0, 8)}
              </option>
            ))}
        </select>
        <Button
          size="sm"
          disabled={
            !candidate ||
            total < 100 ||
            visuals.length >= 12 ||
            (candidate.output_type === "video" && !info.data)
          }
          onClick={add}
        >
          <Plus size={14} />
          {zh ? "添加叠加画面" : "Add overlay"}
        </Button>
      </div>
      {addError && (
        <p role="alert" className="text-danger">
          {addError}
        </p>
      )}
      {(sources.error || info.error) && (
        <p role="alert">{(sources.error || info.error)?.message}</p>
      )}
      <div className="timeline-visual-workspace">
        <div className="timeline-visual-list">
          {visuals.map((v) => (
            <button
              type="button"
              key={v.id}
              aria-pressed={v.id === current?.id}
              onClick={() => onSelect(v.id, v.start_ms)}
            >
              <span>{v.track.toString().padStart(2, "0")}</span>
              <div>
                <strong>{v.name}</strong>
                <small>
                  {(v.start_ms / 1000).toFixed(1)}–
                  {((v.start_ms + v.duration_ms) / 1000).toFixed(1)} s
                  {v.hidden ? (zh ? " · 隐藏" : " · Hidden") : ""}
                </small>
              </div>
            </button>
          ))}
          {!visuals.length && (
            <p>
              {zh
                ? "先添加主画面，再从镜头素材中选择一段叠加画面。可制作画中画、分屏或图片贴片。"
                : "Add a base clip, then choose a visual layer for picture-in-picture or split screens."}
            </p>
          )}
        </div>
        {current && (
          <div className="timeline-visual-inspector">
            <div className="timeline-visual-fields">
              <label>
                {zh ? "画面名称" : "Layer name"}
                <input
                  aria-label={zh ? "画面名称" : "Layer name"}
                  maxLength={255}
                  value={current.name}
                  onChange={(e) => patch({ name: e.target.value })}
                />
              </label>
              <label>
                {zh ? "叠加轨道" : "Overlay track"}
                <select
                  aria-label={zh ? "叠加轨道" : "Overlay track"}
                  value={current.track}
                  onChange={(e) => patch({ track: Number(e.target.value) })}
                >
                  {[1, 2, 3].map((n) => (
                    <option key={n} value={n}>
                      {n}
                      {n === 3 ? (zh ? " · 最上方" : " · Top") : ""}
                    </option>
                  ))}
                </select>
              </label>
              {number(
                zh ? "出现时间（秒）" : "Start (s)",
                "start_ms",
                1000,
                0,
                total / 1000,
              )}
              {number(
                zh ? "显示时长（秒）" : "Duration (s)",
                "duration_ms",
                1000,
                0.1,
                total / 1000,
              )}
              {number(
                zh ? "源视频起点（秒）" : "Source in (s)",
                "in_point_ms",
                1000,
                0,
                1800,
              )}
              <label>
                {zh ? "画面适配" : "Fit"}
                <select
                  aria-label={zh ? "画面适配" : "Fit"}
                  value={current.fit}
                  onChange={(e) =>
                    patch({ fit: e.target.value as VisualClip["fit"] })
                  }
                >
                  <option value="contain">{zh ? "完整显示" : "Contain"}</option>
                  <option value="cover">{zh ? "填满裁切" : "Cover"}</option>
                </select>
              </label>
              {number(zh ? "横向位置（%）" : "Left (%)", "x", 0.01, 0, 100)}
              {number(zh ? "纵向位置（%）" : "Top (%)", "y", 0.01, 0, 100)}
              {number(zh ? "宽度（%）" : "Width (%)", "width", 0.01, 5, 100)}
              {number(zh ? "高度（%）" : "Height (%)", "height", 0.01, 5, 100)}
              {number(
                zh ? "不透明度（%）" : "Opacity (%)",
                "opacity",
                0.01,
                0,
                100,
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-3">
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  patch({ x: 0.6, y: 0.6, width: 0.35, height: 0.35 })
                }
              >
                {zh ? "右下画中画" : "Corner inset"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  patch({ x: 0, y: 0, width: 0.5, height: 1, fit: "cover" })
                }
              >
                {zh ? "左半屏" : "Left half"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  patch({ x: 0.5, y: 0, width: 0.5, height: 1, fit: "cover" })
                }
              >
                {zh ? "右半屏" : "Right half"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => patch({ x: 0, y: 0, width: 1, height: 1 })}
              >
                {zh ? "全画幅" : "Full frame"}
              </Button>
              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={current.hidden}
                  onChange={(e) => patch({ hidden: e.target.checked })}
                />
                {zh ? "隐藏画面" : "Hide layer"}
              </label>
              <Button
                size="sm"
                variant="ghost"
                onClick={() =>
                  change(visuals.filter((v) => v.id !== current.id))
                }
              >
                <Trash2 size={14} />
                {zh ? "移除画面" : "Remove layer"}
              </Button>
            </div>
          </div>
        )}
      </div>
      {visualIssue(visuals, total) && (
        <p role="alert" className="text-danger">
          {zh
            ? "请检查叠加时间和画框范围。同轨道的片段不能重叠，可切换到其他轨道。"
            : "Check time and frame bounds. Overlapping segments must use different tracks."}
        </p>
      )}
    </fieldset>
  );
}

function FrameMedia({
  projectId,
  generationId,
  time,
  fit,
}: {
  projectId: string;
  generationId: string;
  time: number;
  fit: "contain" | "cover";
}) {
  const zh = useI18n().lang === "zh";
  const video = useRef<HTMLVideoElement>(null);
  const [failed, setFailed] = useState(false);
  const q = useQuery({
    queryKey: ["generation", projectId, generationId],
    enabled: !!generationId,
    queryFn: () =>
      api.get<Generation>(`/projects/${projectId}/generations/${generationId}`),
  });
  const seek = () => {
    if (video.current?.readyState && Number.isFinite(time))
      video.current.currentTime = Math.max(
        0,
        Math.min(time / 1000, Math.max(0, video.current.duration - 0.05)),
      );
  };
  useEffect(seek, [time]);
  useEffect(() => setFailed(false), [generationId]);
  if (!q.data?.output_blob_hash || failed)
    return (
      <span className="timeline-composition-missing">
        {q.isLoading ? "…" : zh ? "素材不可用" : "Media unavailable"}
      </span>
    );
  const src = blobUrl(projectId, q.data.output_blob_hash),
    style = { width: "100%", height: "100%", objectFit: fit };
  return q.data.output_type === "image" ? (
    <img src={src} alt="" style={style} onError={() => setFailed(true)} />
  ) : (
    <video
      ref={video}
      src={src}
      style={style}
      muted
      playsInline
      preload="auto"
      onLoadedMetadata={seek}
      onError={() => setFailed(true)}
    />
  );
}

export function TimelineCompositionPreview({
  projectId,
  shots,
  content,
  aspect,
  time,
  onTime,
  selected,
}: {
  projectId: string;
  shots: Shot[];
  content: TimelineContent;
  aspect: string;
  time: number;
  onTime: (t: number) => void;
  selected: string;
}) {
  const zh = useI18n().lang === "zh",
    root = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 640, height: 360 });
  useEffect(() => {
    const observer = new ResizeObserver(([e]) =>
      setSize({ width: e.contentRect.width, height: e.contentRect.height }),
    );
    if (root.current) observer.observe(root.current);
    return () => observer.disconnect();
  }, []);
  const ratio = aspect === "9:16" ? 9 / 16 : aspect === "1:1" ? 1 : 16 / 9;
  const total = timelineDuration(content.items),
    now = Math.max(0, Math.min(time, Math.max(0, total - 1)));
  let cursor = 0,
    base: { generation: string; time: number } | undefined;
  for (const clip of content.items) {
    if (now >= cursor && now < cursor + clip.duration_ms)
      base = {
        generation:
          clip.generation_id ||
          shots.find((s) => s.id === clip.shot_id)?.selected_generation_id ||
          "",
        time: now - cursor + clip.in_point_ms,
      };
    cursor += clip.duration_ms - Number(clip.transition?.duration_ms || 0);
  }
  const width = Math.min(size.width, Math.max(1, size.height) * ratio);
  return (
    <div className="timeline-composition">
      <div ref={root} className="timeline-composition-stage">
        <div
          className="timeline-composition-frame"
          style={{ width, height: width / ratio }}
        >
          {base && (
            <FrameMedia
              key={base.generation}
              projectId={projectId}
              generationId={base.generation}
              time={base.time}
              fit="contain"
            />
          )}
          {[...content.visuals]
            .sort((a, b) => a.track - b.track)
            .filter(
              (v) =>
                !v.hidden &&
                now >= v.start_ms &&
                now < v.start_ms + v.duration_ms,
            )
            .map((v) => (
              <div
                key={v.id}
                data-layer-id={v.id}
                className={v.id === selected ? "is-selected" : ""}
                style={{
                  position: "absolute",
                  left: `${v.x * 100}%`,
                  top: `${v.y * 100}%`,
                  width: `${v.width * 100}%`,
                  height: `${v.height * 100}%`,
                  opacity: v.opacity,
                }}
              >
                <FrameMedia
                  projectId={projectId}
                  generationId={v.generation_id}
                  time={v.in_point_ms + now - v.start_ms}
                  fit={v.fit}
                />
              </div>
            ))}
        </div>
      </div>
      <label className="timeline-composition-scrub">
        <span>{(now / 1000).toFixed(1)}s</span>
        <input
          aria-label={zh ? "合成预览位置" : "Composition time"}
          type="range"
          min={0}
          max={Math.max(0, total - 1)}
          step={1}
          value={now}
          onChange={(e) => onTime(Number(e.target.value))}
        />
        <span>{(total / 1000).toFixed(1)}s</span>
      </label>
    </div>
  );
}
