import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Film, Music2, Captions, Minus, Plus } from "lucide-react";
import { timelineDuration, type TimelineContent } from "@/lib/timeline-edit";
import {
  clipSpans,
  dragTimeline,
  type TrackKind,
  type DragMode,
} from "@/lib/timeline-interaction";
import type { Shot } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Gesture = {
  kind: TrackKind;
  index: number;
  mode: DragMode;
  x: number;
  y: number;
  width: number;
  span: number;
  original: TimelineContent;
  result: ReturnType<typeof dragTimeline>;
  moved: boolean;
};
export function TimelineOverview({
  content,
  shots,
  selected,
  onSelect,
  onTracks,
  onVisualSelect,
  onChange,
  disabled,
  time,
  onTime,
}: {
  content: TimelineContent;
  shots: Shot[];
  selected: number;
  onSelect: (index: number) => void;
  onTracks: (tab: "audio" | "subtitles") => void;
  onVisualSelect: (id: string) => void;
  onChange: (c: TimelineContent) => void;
  disabled: boolean;
  time: number;
  onTime: (t: number) => void;
}) {
  const zh = useI18n().lang === "zh",
    [zoom, setZoom] = useState(1),
    [draft, setDraft] = useState<TimelineContent | null>(null),
    [error, setError] = useState("");
  const gesture = useRef<Gesture | null>(null),
    latest = useRef({ content, disabled, onChange, onSelect }),
    suppress = useRef(false);
  latest.current = { content, disabled, onChange, onSelect };
  const displayed = draft ?? content,
    clips = clipSpans(displayed),
    span = Math.max(1000, timelineDuration(content.items));
  useEffect(() => {
    const cancel = () => {
      gesture.current = null;
      setDraft(null);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape" && gesture.current) {
        suppress.current = true;
        cancel();
      }
    };
    window.addEventListener("keydown", key);
    window.addEventListener("blur", cancel);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("blur", cancel);
    };
  }, []);
  useEffect(() => {
    if (disabled || (gesture.current && gesture.current.original !== content)) {
      gesture.current = null;
      setDraft(null);
    }
  }, [disabled, content]);
  const position = (start: number, length: number) => ({
    left: `${(start / span) * 100}%`,
    width: `${Math.max(0.2, (length / span) * 100)}%`,
  });
  const begin = (
    e: ReactPointerEvent<HTMLDivElement>,
    kind: TrackKind,
    index: number,
  ) => {
    if (disabled || e.button !== 0) return;
    const target = (e.target as HTMLElement).closest<HTMLElement>(
      "[data-drag-mode]",
    );
    if (!target) return;
    const lane = e.currentTarget.parentElement!;
    suppress.current = false;
    setError("");
    gesture.current = {
      kind,
      index,
      mode: target.dataset.dragMode as DragMode,
      x: e.clientX,
      y: e.clientY,
      width: lane.getBoundingClientRect().width,
      span,
      original: content,
      result: { content, selected: index, error: "" },
      moved: false,
    };
    target.setPointerCapture(e.pointerId);
  };
  const move = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (!g) return;
    if (Math.hypot(e.clientX - g.x, e.clientY - g.y) < 4 && !g.moved) return;
    g.moved = true;
    suppress.current = true;
    const raw = ((e.clientX - g.x) / g.width) * g.span,
      delta = e.shiftKey ? Math.round(raw) : Math.round(raw / 100) * 100;
    const lane = document
      .elementFromPoint(e.clientX, e.clientY)
      ?.closest<HTMLElement>("[data-visual-track]");
    g.result = dragTimeline(
      g.original,
      g.kind,
      g.index,
      g.mode,
      delta,
      lane ? Number(lane.dataset.visualTrack) : undefined,
    );
    setDraft(g.result.content);
    setError(g.result.error);
  };
  const finish = (cancel = false) => {
    const g = gesture.current;
    gesture.current = null;
    setDraft(null);
    if (
      !g?.moved ||
      cancel ||
      latest.current.disabled ||
      latest.current.content !== g.original
    )
      return;
    if (g.result.error) {
      setError(g.result.error);
      return;
    }
    latest.current.onChange(g.result.content);
    if (g.kind === "items") latest.current.onSelect(g.result.selected);
  };
  const block = (
    kind: TrackKind,
    index: number,
    label: string,
    start: number,
    length: number,
    className: string,
    onClick: () => void,
    extra?: React.ReactNode,
    top?: number,
  ) => (
    <div
      key={`${kind}-${index}`}
      className="timeline-drag-shell"
      style={{ ...position(start, length), top }}
      onPointerDown={(e) => begin(e, kind, index)}
      onPointerMove={move}
      onPointerUp={() => finish()}
      onPointerCancel={() => finish(true)}
      onLostPointerCapture={() => {
        if (gesture.current) finish(true);
      }}
    >
      <button
        className={className}
        style={
          kind === "items" && index > 0
            ? {
                paddingLeft: `calc(${(Number(displayed.items[index - 1].transition?.duration_ms || 0) / length) * 100}% + 12px)`,
              }
            : undefined
        }
        data-drag-mode="move"
        aria-label={label}
        aria-pressed={kind === "items" ? selected === index : undefined}
        onClick={() => {
          if (suppress.current) {
            suppress.current = false;
            return;
          }
          onClick();
        }}
      >
        {extra ?? label}
      </button>
      {(["start", "end"] as const).map((mode) => (
        <button
          key={mode}
          className={`timeline-trim-handle is-${mode}`}
          disabled={disabled}
          data-drag-mode={mode}
          aria-label={`${mode === "start" ? "裁切起点" : "裁切终点"} ${kind} ${index + 1}`}
          title="向内拖动裁切；方向键每次裁切 0.1 秒"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (!["ArrowLeft", "ArrowRight"].includes(e.key) || disabled)
              return;
            e.preventDefault();
            const result = dragTimeline(
              content,
              kind,
              index,
              mode,
              e.key === "ArrowRight" ? 100 : -100,
            );
            setError(result.error);
            if (!result.error) onChange(result.content);
          }}
        />
      ))}
    </div>
  );
  return (
    <section
      className="timeline-overview"
      aria-label={zh ? "时间线概览" : "Timeline overview"}
    >
      <header>
        <div>
          <Film size={16} />
          <strong>{zh ? "时间线" : "Timeline"}</strong>
          <span>
            {content.items.length} 个片段 ·{" "}
            {(timelineDuration(displayed.items) / 1000).toFixed(1)}s
          </span>
        </div>
        <div>
          <button
            aria-label="缩小时间线"
            disabled={zoom === 1}
            onClick={() => setZoom(zoom / 2)}
          >
            <Minus />
          </button>
          <span>{zoom}×</span>
          <button
            aria-label="放大时间线"
            disabled={zoom === 4}
            onClick={() => setZoom(zoom * 2)}
          >
            <Plus />
          </button>
        </div>
      </header>
      <div className="timeline-overview-scroll">
        <div
          className="timeline-overview-content"
          style={{ width: `${zoom * 100}%` }}
        >
          <div className="timeline-ruler">
            <span className="timeline-track-label">秒</span>
            <div
              className="timeline-seek-ruler"
              onPointerDown={(e) => {
                const rect = e.currentTarget.getBoundingClientRect();
                onTime(
                  Math.round(
                    Math.max(
                      0,
                      Math.min(1, (e.clientX - rect.left) / rect.width),
                    ) * Math.max(0, span - 1),
                  ),
                );
              }}
            >
              {Array.from({ length: 11 }, (_, i) => (
                <span key={i} style={{ left: `${i * 10}%` }}>
                  {((span * i) / 10000).toFixed(1)}
                </span>
              ))}
              <i
                className="timeline-playhead"
                style={{
                  left: `${Math.max(0, Math.min(100, (time / span) * 100))}%`,
                }}
              />
            </div>
          </div>
          <div className="timeline-overview-row">
            <span className="timeline-track-label">
              <Film />
              画面
            </span>
            <div className="timeline-overview-lane">
              {clips.map(({ clip, index, start }) =>
                block(
                  "items",
                  index,
                  `选择片段 ${index + 1}`,
                  start,
                  clip.duration_ms,
                  "timeline-picture-block",
                  () => onSelect(index),
                  <>
                    <span>
                      {index + 1} ·{" "}
                      {shots.find((s) => s.id === clip.shot_id)?.title ||
                        "镜头"}
                    </span>
                    <small>
                      {(clip.duration_ms / 1000).toFixed(1)}s
                      {clip.transition ? " · 转场" : ""}
                    </small>
                  </>,
                ),
              )}
              {!clips.length && (
                <span className="timeline-lane-empty">
                  添加镜头后在这里排列画面
                </span>
              )}
            </div>
          </div>
          {[3, 2, 1].map((track) => (
            <div className="timeline-overview-row" key={track}>
              <button
                className="timeline-track-label"
                onClick={() => onVisualSelect("")}
              >
                <Film />
                叠加 {track}
              </button>
              <div
                className="timeline-overview-lane timeline-visual-lane"
                data-visual-track={track}
              >
                {displayed.visuals.map((v, index) =>
                  (gesture.current
                    ? content.visuals[index]?.track
                    : v.track) === track
                    ? block(
                        "visuals",
                        index,
                        `叠加画面 ${v.name}`,
                        v.start_ms,
                        v.duration_ms,
                        `timeline-visual-block ${v.hidden ? "is-muted" : ""}`,
                        () => onVisualSelect(v.id),
                        v.name,
                      )
                    : null,
                )}
                {!displayed.visuals.some((v) => v.track === track) && (
                  <button
                    className="timeline-lane-empty"
                    onClick={() => onVisualSelect("")}
                  >
                    + 添加叠加画面
                  </button>
                )}
              </div>
            </div>
          ))}
          <div className="timeline-overview-row">
            <button
              className="timeline-track-label"
              onClick={() => onTracks("audio")}
            >
              <Music2 />
              声音
            </button>
            <div
              className="timeline-overview-lane timeline-audio-lane"
              style={{ height: Math.max(44, displayed.audio.length * 30 + 4) }}
            >
              {displayed.audio.map((a, i) =>
                block(
                  "audio",
                  i,
                  `声音片段 ${i + 1}`,
                  a.start_ms,
                  a.duration_ms,
                  `timeline-audio-block ${a.muted ? "is-muted" : ""}`,
                  () => onTracks("audio"),
                  a.name,
                  4 + i * 30,
                ),
              )}
              {!displayed.audio.length && (
                <button
                  className="timeline-lane-empty"
                  onClick={() => onTracks("audio")}
                >
                  + 添加配乐、对白或音效
                </button>
              )}
            </div>
          </div>
          <div className="timeline-overview-row">
            <button
              className="timeline-track-label"
              onClick={() => onTracks("subtitles")}
            >
              <Captions />
              字幕
            </button>
            <div className="timeline-overview-lane timeline-caption-lane">
              {displayed.subtitles
                .slice(0, 500)
                .map((c, i) =>
                  block(
                    "subtitles",
                    i,
                    `字幕片段 ${i + 1}`,
                    c.start_ms,
                    c.end_ms - c.start_ms,
                    "timeline-caption-block",
                    () => onTracks("subtitles"),
                    c.text,
                  ),
                )}
              {!displayed.subtitles.length && (
                <button
                  className="timeline-lane-empty"
                  onClick={() => onTracks("subtitles")}
                >
                  + 添加或导入字幕
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      <p>
        拖动画面调整顺序，拖动叠加、声音或字幕调整时间；两侧拖柄向内裁切。默认按
        0.1 秒对齐，按住 Shift 精细移动，Esc 取消。点击刻度预览。
        {content.subtitles.length > 500 ? " 概览显示前 500 条字幕。" : ""}
      </p>
      {error && (
        <p role="alert" className="text-danger">
          {error}，本次拖动未保存。
        </p>
      )}
    </section>
  );
}
