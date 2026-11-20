import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { Film, Music2, Captions, Minus, Plus, Magnet, Layers, ScanLine } from "lucide-react";
import { TimelineMediaStrip } from "./TimelineMediaStrip";
import { timelineDuration, type TimelineContent } from "@/lib/timeline-edit";
import {
  clipSpans, frameMs, snapEdit, timecode, type TrimMode,
  dragTimeline,
  type TrackKind,
  type DragMode,
  type SourceBounds,
} from "@/lib/timeline-interaction";
import type { Shot } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const trackNames: Record<TrackKind, string> = { items: "画面", audio: "声音", visuals: "叠加画面", subtitles: "字幕" };

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
  projectId, trimMode, focusKind, focusId, range, onRange, onTrimPreview,
  bounds,
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
  projectId: string;
  trimMode: TrimMode;
  focusKind: TrackKind;
  focusId: string;
  range: [number, number] | null;
  onRange: (range: [number, number] | null) => void;
  onTrimPreview: (content: TimelineContent | null) => void;
  bounds: Record<string, SourceBounds | undefined>;
  content: TimelineContent;
  shots: Shot[];
  selected: number;
  onSelect: (index: number) => void;
  onTracks: (tab: "audio" | "subtitles", id?: string) => void;
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
  const [showEmptyLayers, setShowEmptyLayers] = useState(false);
  const [magnet, setMagnet] = useState(true);
  const [rangeTool, setRangeTool] = useState(false);
  const [guide, setGuide] = useState<number | null>(null);
  const ruler = useRef<HTMLDivElement>(null);
  const [rulerWidth, setRulerWidth] = useState(800);
  const scrub = useRef<{ pointer: number; anchor: number | null } | null>(null);
  const beforeDrag = useRef(0);
  const gesture = useRef<Gesture | null>(null),
    latest = useRef({ content, disabled, onChange, onSelect, onTrimPreview, onTime }),
    suppress = useRef(false);
  latest.current = { content, disabled, onChange, onSelect, onTrimPreview, onTime };
  const displayed = draft ?? content,
    clips = clipSpans(displayed),
    span = Math.max(1000, timelineDuration(content.items) * 1.2);
  const fullLength = timelineDuration(content.items);
  const pixelsPerLabel = Math.max(1, rulerWidth / 76);
  const targetStep = span / pixelsPerLabel;
  const magnitude = 10 ** Math.floor(Math.log10(targetStep));
  const tick = ([1, 2, 5, 10].find(step => step * magnitude >= targetStep) || 10) * magnitude;
  useEffect(() => {
    if (!ruler.current) return;
    const observer = new ResizeObserver(([entry]) => setRulerWidth(Math.max(1,entry.contentRect.width)));
    observer.observe(ruler.current);
    return () => observer.disconnect();
  }, []);
  function rulerTime(e: ReactPointerEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    return Math.max(0, Math.min(fullLength, Math.round((e.clientX - rect.left) / Math.max(1, rect.width) * span)));
  }
  function scrubTo(e: ReactPointerEvent<HTMLDivElement>) {
    const drag = scrub.current;
    if (!drag || drag.pointer !== e.pointerId) return;
    const at = rulerTime(e);
    onTime(Math.min(at, Math.max(0, fullLength - 1)));
    if (drag.anchor !== null && Math.abs(at - drag.anchor) >= 1)
      onRange([Math.min(at, drag.anchor), Math.max(at, drag.anchor)]);
  }
  useEffect(() => {
    const cancel = () => {
      if (gesture.current?.moved) latest.current.onTime(beforeDrag.current);
      latest.current.onTrimPreview(null);
      scrub.current = null;
      setGuide(null);
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
      latest.current.onTrimPreview(null);
      setDraft(null);
      setGuide(null);
    }
  }, [disabled, content]);
  const sourceFor = (kind: TrackKind, index: number) => {
    if (kind === "subtitles") return undefined;
    const clip = content[kind][index];
    const id =
      clip.generation_id ||
      ("shot_id" in clip
        ? shots.find((s) => s.id === clip.shot_id)?.selected_generation_id
        : undefined);
    return id ? bounds[id] : undefined;
  };
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
    beforeDrag.current = time;
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
    const raw = ((e.clientX - g.x) / g.width) * g.span;
    const snapping = magnet && !e.shiftKey && !(g.kind === "items" && g.mode === "move");
    const snap = snapping ? snapEdit(g.original, g.kind, g.index, g.mode, raw, beforeDrag.current, g.span / g.width * 7)
      : {delta: Math.round(raw), guide: null};
    setGuide(snap.guide);
    const lane = document
      .elementFromPoint(e.clientX, e.clientY)
      ?.closest<HTMLElement>("[data-visual-track]");
    g.result = dragTimeline(
      g.original,
      g.kind,
      g.index,
      g.mode,
      snap.delta,
      lane ? Number(lane.dataset.visualTrack) : undefined,
      sourceFor(g.kind, g.index), trimMode,
    );
    if (g.kind === "items" && g.mode !== "move" && !g.result.error) {
      const edge = clipSpans(g.result.content)[g.index];
      onTrimPreview(g.result.content);
      onTime(g.mode === "start" ? edge.start : Math.max(edge.start, edge.end - 1));
    }
    setDraft(g.result.content);
    setError(g.result.error);
  };
  const finish = (cancel = false) => {
    const g = gesture.current;
    gesture.current = null;
    setDraft(null);
    setGuide(null);
    onTrimPreview(null);
    if (g?.moved && (cancel || g.result.error)) onTime(beforeDrag.current);
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
    top?: number | string,
  ) => (
    <div
      key={content[kind][index]?.id || `${kind}-${index}`}
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
        aria-pressed={focusKind === kind && (kind === "items" ? selected === index : displayed[kind][index].id === focusId)}
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
          aria-label={`${trackNames[kind]} ${index + 1} · ${mode === "start" ? "裁切起点" : "裁切终点"}`}
          title="拖动修剪，方向键逐帧调整"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (!["ArrowLeft", "ArrowRight"].includes(e.key) || disabled)
              return;
            e.preventDefault();
            e.stopPropagation();
            const result = dragTimeline(
              content,
              kind,
              index,
              mode,
              e.key === "ArrowRight" ? Math.round(frameMs) : -Math.round(frameMs),
              undefined,
              sourceFor(kind, index), trimMode,
            );
            setError(result.error);
            if (!result.error) {
              onChange(result.content);
              onClick();
              if (kind === "items") {
                const edge = clipSpans(result.content)[index];
                onTime(mode === "start" ? edge.start : Math.max(edge.start, edge.end - 1));
              }
            }
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
          <button aria-label="吸附剪点" title="吸附剪点和声音边缘（按住 Shift 临时关闭）" aria-pressed={magnet} onClick={() => setMagnet(!magnet)}><Magnet/></button>
          <button aria-label="选取时间范围" title="拖动时间尺选区，也可以按住 Shift 拖动" aria-pressed={rangeTool} onClick={() => setRangeTool(!rangeTool)}><ScanLine/></button>
          <button aria-label="显示叠加轨" title="显示空叠加轨" aria-expanded={showEmptyLayers} onClick={() => setShowEmptyLayers(!showEmptyLayers)}><Layers/></button>
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
          style={{ width: `${zoom * 100}%`, "--tick-width": `${tick / span * 100}%` } as React.CSSProperties}
        >
          <div className="timeline-ruler">
            <span className="timeline-track-label">秒</span>
            <div className="timeline-seek-ruler" ref={ruler} role="slider" tabIndex={0}
              aria-label="时间尺" aria-valuemin={0} aria-valuemax={fullLength} aria-valuenow={Math.round(time)} aria-valuetext={timecode(time)}
              onPointerDown={e => {
                if (e.button !== 0 || scrub.current) return;
                e.preventDefault(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId);
                const at = rulerTime(e);
                scrub.current = { pointer: e.pointerId, anchor: rangeTool || e.shiftKey ? at : null };
                onRange(null);
                scrubTo(e);
              }}
              onPointerMove={scrubTo}
              onPointerUp={e => { scrubTo(e); scrub.current = null; }}
              onLostPointerCapture={() => { scrub.current = null; }}
              onKeyDown={e => {
                if (!["ArrowLeft","ArrowRight","Home","End"].includes(e.key)) return;
                e.preventDefault(); e.stopPropagation();
                const at = e.key === "Home" ? 0 : e.key === "End" ? fullLength - 1 :
                  time + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1000 : frameMs);
                onTime(Math.max(0, Math.min(fullLength - 1, Math.round(at))));
              }}>
              {Array.from({length: Math.ceil(span / tick)}, (_, i) => (
                <span key={i} style={{left: `${i * tick / span * 100}%`}}>{Number((i * tick / 1000).toFixed(2))}s</span>
              ))}
            </div>
          </div>
          <div className="cut-cursor-layer" aria-hidden="true">
            {range && <i className="cut-range-wash" style={position(range[0], range[1] - range[0])}/>}
            {guide !== null && <i className="cut-snap-guide" style={{left: `${guide / span * 100}%`}}/>}
            <i className="cut-playhead" style={{left: `${time / span * 100}%`}}/>
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
                    <TimelineMediaStrip project={projectId} id={clip.generation_id || shots.find(s => s.id === clip.shot_id)?.selected_generation_id || ""} from={clip.in_point_ms} length={clip.duration_ms}/>
                    <span className="cut-clip-label">
                      {index + 1} ·{" "}
                      {shots.find((s) => s.id === clip.shot_id)?.title ||
                        clip.note ||
                        "素材片段"}
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
                  添加素材后在这里排列画面
                </span>
              )}
            </div>
          </div>
          {[3, 2, 1]
            .filter(
              (track) =>
                showEmptyLayers ||
                displayed.visuals.some((v) => v.track === track),
            )
            .map((track) => (
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
              style={{ height: `max(44px, calc(${displayed.audio.length} * var(--timeline-lane-step, 30px) + 4px))` }}
            >
              {displayed.audio.map((a, i) =>
                block(
                  "audio",
                  i,
                  `声音片段 ${i + 1}`,
                  a.start_ms,
                  a.duration_ms,
                  `timeline-audio-block ${a.muted ? "is-muted" : ""}`,
                  () => onTracks("audio", a.id),
                  <><TimelineMediaStrip project={projectId} id={a.generation_id} from={a.in_point_ms} length={a.duration_ms} audio/><span>{a.name}</span></>,
                  `calc(4px + ${i} * var(--timeline-lane-step, 30px))`,
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
                    () => onTracks("subtitles", c.id),
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
      {range && <div className="cut-range-info">选区 {timecode(range[0])} – {timecode(range[1])}<button onClick={() => onRange(null)}>清除选区</button></div>}
      {content.subtitles.length > 500 && <p>显示前 500 条字幕，其余可在字幕属性中查看。</p>}
      {error && (
        <p role="alert" className="text-danger">
          {error}，本次拖动未保存。
        </p>
      )}
    </section>
  );
}
