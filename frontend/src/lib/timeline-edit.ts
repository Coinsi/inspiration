export interface Clip {
  shot_id: string;
  generation_id: string | null;
  in_point_ms: number;
  out_point_ms: number;
  duration_ms: number;
  transition?: Record<string, unknown> | null;
  note?: string | null;
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
