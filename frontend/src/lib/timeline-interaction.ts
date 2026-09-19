import {
  timelineDuration,
  visualIssue,
  type TimelineContent,
} from "./timeline-edit.ts";
export type TrackKind = "items" | "visuals" | "audio" | "subtitles";
export type DragMode = "move" | "start" | "end";
export function clipSpans(content: TimelineContent) {
  let start = 0;
  return content.items.map((clip, index) => {
    const at = start;
    start += clip.duration_ms - Number(clip.transition?.duration_ms || 0);
    return { index, start: at, end: at + clip.duration_ms, clip };
  });
}
export function timelineEditIssue(c: TimelineContent): string {
  const total = timelineDuration(c.items);
  if (!Number.isFinite(total) || total > 1800000 || total < 100)
    return "时间线时长须在 0.1 秒至 30 分钟之间";
  if (
    c.items.some(
      (v, i) =>
        v.duration_ms < 100 ||
        v.in_point_ms < 0 ||
        (v.transition &&
          (i === c.items.length - 1 ||
            Number(v.transition.duration_ms) * 2 >
              Math.min(v.duration_ms, c.items[i + 1].duration_ms))),
    )
  )
    return "裁切或排序后转场过长，请先缩短或移除转场";
  if (visualIssue(c.visuals, total)) return "叠加画面不能越界或在同一轨道重叠";
  if (
    c.audio.some(
      (v) =>
        v.start_ms < 0 ||
        v.duration_ms < 100 ||
        v.start_ms + v.duration_ms > total ||
        v.fade_in_ms + v.fade_out_ms > v.duration_ms,
    )
  )
    return "声音超出时间线，或裁切后不足以保留淡入淡出";
  if (
    c.subtitles.some(
      (v) => v.start_ms < 0 || v.end_ms - v.start_ms < 100 || v.end_ms > total,
    )
  )
    return "字幕不能超出时间线，且至少保留 0.1 秒";
  return "";
}
export function dragTimeline(
  original: TimelineContent,
  kind: TrackKind,
  index: number,
  mode: DragMode,
  delta: number,
  track?: number,
): { content: TimelineContent; selected: number; error: string } {
  const content = structuredClone(original),
    total = timelineDuration(original.items);
  const row = content[kind][index];
  if (!row || !Number.isFinite(delta))
    return { content: original, selected: index, error: "无法读取片段" };
  let selected = index;
  if (kind === "items" && mode === "move") {
    const spans = clipSpans(original),
      target =
        spans[index].start + original.items[index].duration_ms / 2 + delta;
    const others = spans.filter((s) => s.index !== index);
    selected = others.filter((s) => target >= (s.start + s.end) / 2).length;
    const [clip] = content.items.splice(index, 1);
    content.items.splice(selected, 0, clip);
  } else if (kind === "subtitles") {
    const cue = content.subtitles[index],
      length = cue.end_ms - cue.start_ms;
    if (mode === "move") {
      const start = Math.max(0, Math.min(total - length, cue.start_ms + delta));
      cue.start_ms = start;
      cue.end_ms = start + length;
    } else if (mode === "start")
      cue.start_ms += Math.max(0, Math.min(length - 100, delta));
    else cue.end_ms += Math.min(0, Math.max(100 - length, delta));
  } else {
    const clip =
      kind === "items"
        ? content.items[index]
        : kind === "audio"
          ? content.audio[index]
          : content.visuals[index];
    if (mode === "move" && "start_ms" in clip)
      clip.start_ms = Math.max(
        0,
        Math.min(total - clip.duration_ms, clip.start_ms + delta),
      );
    else if (mode === "start") {
      const trim = Math.max(0, Math.min(clip.duration_ms - 100, delta));
      clip.in_point_ms += trim;
      clip.duration_ms -= trim;
      if ("start_ms" in clip) clip.start_ms += trim;
    } else if (mode === "end")
      clip.duration_ms += Math.min(0, Math.max(100 - clip.duration_ms, delta));
    if (kind === "items") content.items[index].out_point_ms = 0;
    if (
      kind === "visuals" &&
      mode === "move" &&
      track &&
      track >= 1 &&
      track <= 3
    )
      content.visuals[index].track = track;
  }
  return { content, selected, error: timelineEditIssue(content) };
}
export function transitionAt(content: TimelineContent, time: number) {
  const spans = clipSpans(content),
    active = spans.filter((s) => time >= s.start && time < s.end);
  if (active.length < 2) return { active, progress: 0, type: "" };
  const outgoing = active[0],
    incoming = active[1],
    duration = Number(outgoing.clip.transition?.duration_ms || 0);
  return {
    active,
    progress: Math.max(
      0,
      Math.min(1, (time - incoming.start) / Math.max(1, duration)),
    ),
    type: String(outgoing.clip.transition?.type || ""),
  };
}
export function audioEnvelope(
  localMs: number,
  durationMs: number,
  fadeIn: number,
  fadeOut: number,
  gainDb = 0,
) {
  if (localMs < 0 || localMs >= durationMs) return 0;
  return (
    Math.pow(10, gainDb / 20) *
    Math.min(
      1,
      fadeIn ? localMs / fadeIn : 1,
      fadeOut ? (durationMs - localMs) / fadeOut : 1,
    )
  );
}
