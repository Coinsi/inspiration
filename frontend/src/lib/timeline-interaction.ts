import {
  timelineDuration,
  visualIssue,
  type TimelineContent,
} from "./timeline-edit.ts";
export type TrackKind = "items" | "visuals" | "audio" | "subtitles";
export type SourceBounds = { image: boolean; duration_ms?: number };
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
  for (const [i, v] of c.items.entries()) {
    if (
      ![v.duration_ms, v.in_point_ms].every(Number.isFinite) ||
      v.duration_ms < 100 ||
      v.in_point_ms < 0
    )
      return `第 ${i + 1} 个画面：起点不能为负，时长至少 0.1 秒`;
    if (
      v.transition &&
      (i === c.items.length - 1 ||
        Number(v.transition.duration_ms) * 2 >
          Math.min(v.duration_ms, c.items[i + 1].duration_ms))
    )
      return `第 ${i + 1} 个画面的转场过长或已位于末尾，请缩短或移除转场`;
  }
  if (visualIssue(c.visuals, total))
    return "叠加画面不能越界或在同一轨道重叠，请在叠加画面面板调整";
  for (const [i, v] of c.audio.entries()) {
    if (
      ![
        v.start_ms,
        v.in_point_ms,
        v.duration_ms,
        v.fade_in_ms,
        v.fade_out_ms,
        v.gain_db,
      ].every(Number.isFinite) ||
      v.start_ms < 0 ||
      v.in_point_ms < 0 ||
      v.duration_ms < 100 ||
      v.start_ms + v.duration_ms > total
    )
      return `第 ${i + 1} 段声音「${v.name}」超出时间线或时间无效，请调整开始位置与时长`;
    if (
      v.fade_in_ms < 0 ||
      v.fade_out_ms < 0 ||
      v.fade_in_ms + v.fade_out_ms > v.duration_ms
    )
      return `第 ${i + 1} 段声音「${v.name}」不足以保留淡入淡出，请缩短淡入淡出`;
    if (v.gain_db < -60 || v.gain_db > 12)
      return `第 ${i + 1} 段声音音量超出范围`;
  }
  for (const [i, v] of c.subtitles.entries()) {
    if (
      ![v.start_ms, v.end_ms].every(Number.isFinite) ||
      v.start_ms < 0 ||
      v.end_ms - v.start_ms < 100 ||
      v.end_ms > total
    )
      return `第 ${i + 1} 条字幕超出时间线或不足 0.1 秒，请调整开始与结束时间`;
    if (!v.text.trim())
      return `第 ${i + 1} 条字幕尚未填写文字，请填写或删除空字幕`;
  }
  return "";
}
export function dragTimeline(
  original: TimelineContent,
  kind: TrackKind,
  index: number,
  mode: DragMode,
  delta: number,
  track?: number,
  source?: SourceBounds,
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
    content.items[content.items.length - 1].transition = null;
  } else if (kind === "subtitles") {
    const cue = content.subtitles[index],
      length = cue.end_ms - cue.start_ms;
    if (mode === "move") {
      const start = Math.max(0, Math.min(total - length, cue.start_ms + delta));
      cue.start_ms = start;
      cue.end_ms = start + length;
    } else if (mode === "start")
      cue.start_ms += Math.max(-cue.start_ms, Math.min(length - 100, delta));
    else
      cue.end_ms += Math.min(total - cue.end_ms, Math.max(100 - length, delta));
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
      const available = source?.image ? 1800000 : clip.in_point_ms;
      const before =
        "start_ms" in clip ? Math.min(available, clip.start_ms) : available;
      const trim = Math.max(-before, Math.min(clip.duration_ms - 100, delta));
      clip.in_point_ms = source?.image ? 0 : clip.in_point_ms + trim;
      clip.duration_ms -= trim;
      if ("start_ms" in clip) clip.start_ms += trim;
    } else if (mode === "end") {
      const sourceEnd = source?.image ? 1800000 : source?.duration_ms;
      if (delta > 0 && sourceEnd === undefined)
        return {
          content: original,
          selected,
          error: "素材时长尚未读取成功，请稍后重试",
        };
      const room =
        "start_ms" in clip
          ? total - clip.start_ms
          : 1800000 - total + clip.duration_ms;
      const maximum = Math.min(
        room,
        sourceEnd === undefined
          ? clip.duration_ms
          : sourceEnd - (source?.image ? 0 : clip.in_point_ms),
      );
      clip.duration_ms = Math.max(
        100,
        Math.min(maximum, clip.duration_ms + delta),
      );
    }
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
