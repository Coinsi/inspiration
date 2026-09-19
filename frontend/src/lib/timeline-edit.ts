export interface Clip {
  shot_id: string;
  generation_id: string | null;
  in_point_ms: number;
  out_point_ms: number;
  duration_ms: number;
  transition?: Record<string, unknown> | null;
  note?: string | null;
}
export interface SubtitleCue {
  start_ms: number;
  end_ms: number;
  text: string;
  source?: {
    kind: "reviewed_transcription";
    version_id: string;
    annotation_id: string;
    generation_id: string;
    start_ms: number;
    end_ms: number;
  };
}
export interface AudioClip {
  generation_id: string;
  name: string;
  kind: "dialogue" | "music" | "effect";
  start_ms: number;
  in_point_ms: number;
  duration_ms: number;
  gain_db: number;
  fade_in_ms: number;
  fade_out_ms: number;
  muted: boolean;
}
export interface VisualClip {
  id: string;
  generation_id: string;
  name: string;
  track: number;
  start_ms: number;
  in_point_ms: number;
  duration_ms: number;
  x: number;
  y: number;
  width: number;
  height: number;
  opacity: number;
  fit: "contain" | "cover";
  hidden: boolean;
}
export function visualIssue(visuals: VisualClip[], total: number): boolean {
  return (
    visuals.length > 12 ||
    visuals.some(
      (v) =>
        !v.generation_id ||
        v.start_ms < 0 ||
        v.in_point_ms < 0 ||
        v.duration_ms < 100 ||
        v.start_ms + v.duration_ms > total ||
        v.x < 0 ||
        v.y < 0 ||
        v.width < 0.05 ||
        v.height < 0.05 ||
        v.x + v.width > 1.000001 ||
        v.y + v.height > 1.000001 ||
        [
          v.x,
          v.y,
          v.width,
          v.height,
          v.opacity,
          v.start_ms,
          v.in_point_ms,
          v.duration_ms,
        ].some((n) => !Number.isFinite(n)) ||
        visuals.some(
          (o) =>
            o.id !== v.id &&
            o.track === v.track &&
            Math.max(o.start_ms, v.start_ms) <
              Math.min(o.start_ms + o.duration_ms, v.start_ms + v.duration_ms),
        ),
    )
  );
}
export interface TimelineContent {
  items: Clip[];
  audio: AudioClip[];
  subtitles: SubtitleCue[];
  visuals: VisualClip[];
}
export interface TimelineDocument extends TimelineContent {
  revision: number;
}
export function timelineDuration(clips: Clip[]): number {
  return clips.reduce(
    (total, c) =>
      total + c.duration_ms - Number(c.transition?.duration_ms || 0),
    0,
  );
}
export type History<T> = { past: T[]; present: T; future: T[] };
export function edit<T>(history: History<T>, next: T): History<T> {
  if (JSON.stringify(next) === JSON.stringify(history.present)) return history;
  return {
    past: [...history.past, history.present].slice(-100),
    present: next,
    future: [],
  };
}
export function undo<T>(h: History<T>): History<T> {
  if (!h.past.length) return h;
  return {
    past: h.past.slice(0, -1),
    present: h.past[h.past.length - 1],
    future: [h.present, ...h.future],
  };
}
export function redo<T>(h: History<T>): History<T> {
  if (!h.future.length) return h;
  return {
    past: [...h.past, h.present].slice(-100),
    present: h.future[0],
    future: h.future.slice(1),
  };
}
export function splitClip(
  clips: Clip[],
  index: number,
  offset: number,
): Clip[] {
  const c = clips[index];
  if (
    !c ||
    clips.length >= 100 ||
    !Number.isInteger(offset) ||
    offset < 100 ||
    offset > c.duration_ms - 100
  ) {
    throw new Error(
      "Split must leave at least 0.1 seconds on each side (100 clips maximum)",
    );
  }
  return [
    ...clips.slice(0, index),
    { ...c, duration_ms: offset, out_point_ms: 0, transition: null },
    {
      ...c,
      in_point_ms: c.in_point_ms + offset,
      duration_ms: c.duration_ms - offset,
      out_point_ms: 0,
    },
    ...clips.slice(index + 1),
  ];
}
