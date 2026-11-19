import {
  timelineDuration,
  visualIssue,
  type TimelineContent,
} from "./timeline-edit.ts";
export type TrackKind = "items" | "visuals" | "audio" | "subtitles";
export type SourceBounds = { image: boolean; duration_ms?: number };
export type DragMode = "move" | "start" | "end";
export type TrimMode = "ripple" | "lift";
export const frameMs = 1000 / 24;
export function timecode(ms: number) {
  const frames = Math.max(0, Math.floor((ms + 0.5) / frameMs));
  return [Math.floor(frames / 1440), Math.floor(frames / 24) % 60, frames % 24]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}
export function clipSpans(content: TimelineContent) {
  let start = 0;
  return content.items.map((clip, index) => {
    const at = start + (clip.gap_before_ms || 0);
    start =
      at +
      clip.duration_ms +
      (clip.gap_after_ms || 0) -
      Number(clip.transition?.duration_ms || 0);
    return { index, start: at, end: at + clip.duration_ms, clip };
  });
}
export function cutAt(
  content: TimelineContent,
  time: number,
  preferred: number,
) {
  const spans = clipSpans(content);
  const candidates = spans.filter(({ index, start, end, clip }) => {
    const left = Number(content.items[index - 1]?.transition?.duration_ms || 0);
    const right = Number(clip.transition?.duration_ms || 0);
    return (
      time - start >= Math.max(100, left * 2) &&
      end - time >= Math.max(100, right * 2)
    );
  });
  return (
    candidates.find((s) => s.index === preferred) ??
    candidates[candidates.length - 1]
  );
}

function edges(content: TimelineContent, kind: TrackKind, index: number) {
  if (kind === "items") {
    const { start, end } = clipSpans(content)[index];
    return [start, end];
  }
  const item = content[kind][index];
  return [
    item.start_ms,
    "end_ms" in item ? item.end_ms : item.start_ms + item.duration_ms,
  ];
}

export function snapEdit(
  content: TimelineContent,
  kind: TrackKind,
  index: number,
  mode: DragMode,
  delta: number,
  playhead: number,
  tolerance: number,
) {
  const [start, end] = edges(content, kind, index);
  const moving =
    mode === "start" ? [start] : mode === "end" ? [end] : [start, end];
  const anchors = new Set([0, Math.round(playhead)]);
  for (const track of ["items", "audio", "subtitles", "visuals"] as const) {
    const spans = track === "items" ? clipSpans(content) : null;
    content[track].forEach((_, i) => {
      if (track === kind && i === index) return;
      const pair = spans
        ? [spans[i].start, spans[i].end]
        : edges(content, track, i);
      pair.forEach((n) => anchors.add(n));
    });
  }
  let distance = tolerance,
    guide: number | null = null,
    adjusted = Math.round(delta / frameMs) * frameMs;
  for (const edge of moving)
    for (const anchor of anchors) {
      const diff = anchor - edge - delta;
      if (Math.abs(diff) < distance) {
        distance = Math.abs(diff);
        guide = anchor;
        adjusted = anchor - edge;
      }
    }
  return { delta: Math.round(adjusted), guide };
}

function lastTrackEnd(content: TimelineContent) {
  return Math.max(
    0,
    ...content.subtitles.map((c) => c.end_ms),
    ...content.audio.map((a) => a.start_ms + a.duration_ms),
    ...content.visuals.map((v) => v.start_ms + v.duration_ms),
  );
}

function preserveTrackEnds(content: TimelineContent) {
  const tail = content.items[content.items.length - 1];
  const shortage = lastTrackEnd(content) - timelineDuration(content.items);
  if (tail && shortage > 0)
    tail.gap_after_ms = (tail.gap_after_ms || 0) + shortage;
}

export function removePicture(
  content: TimelineContent,
  index: number,
  mode: TrimMode,
) {
  const next = structuredClone(content);
  const [removed] = next.items.splice(index, 1);
  if (!removed) return { content, error: "" };
  if (!next.items.length && lastTrackEnd(content) > 0)
    return {
      content,
      error:
        "时间线上还有声音、字幕或叠加画面。请先添加替代画面，或移除这些轨道内容。",
    };
  const previous = next.items[index - 1];
  if (previous) previous.transition = null;
  if (mode === "lift" && next.items.length) {
    const removedTime =
      timelineDuration(content.items) - timelineDuration(next.items);
    const following = next.items[index];
    if (following)
      following.gap_before_ms = (following.gap_before_ms || 0) + removedTime;
    else previous.gap_after_ms = (previous.gap_after_ms || 0) + removedTime;
  }
  preserveTrackEnds(next);
  return { content: next, error: "" };
}

export function canClosePictureGap(content: TimelineContent, time: number) {
  const spans = clipSpans(content);
  if (!spans.length || spans.some((s) => time >= s.start && time < s.end))
    return false;
  const tailEnd = Math.max(spans[spans.length - 1].end, lastTrackEnd(content));
  return (
    spans.some((s) => s.start > time) ||
    timelineDuration(content.items) > tailEnd
  );
}

export function closePictureGap(
  content: TimelineContent,
  time: number,
): TimelineContent {
  const next = structuredClone(content);
  const spans = clipSpans(content);
  const following = spans.find((s) => s.start > time);
  const previous = [...spans].reverse().find((s) => s.end <= time);
  if (!canClosePictureGap(content, time)) return content;
  if (previous) next.items[previous.index].gap_after_ms = 0;
  if (following) next.items[following.index].gap_before_ms = 0;
  preserveTrackEnds(next);
  return next;
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
    if (v.transition && (v.gap_after_ms || c.items[i + 1]?.gap_before_ms))
      return "请先合拢两个画面之间的空隙，再添加转场";
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
  trimMode: TrimMode = "ripple",
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
    if (kind === "items") {
      const item = content.items[index];
      item.out_point_ms = 0;
      const shrink = original.items[index].duration_ms - item.duration_ms;
      if (trimMode === "lift" && mode !== "move") {
        const before = mode === "start";
        const key = before ? "gap_before_ms" : "gap_after_ms";
        const neighbor = content.items[index + (before ? -1 : 1)];
        const otherKey = before ? "gap_after_ms" : "gap_before_ms";
        if (shrink !== 0 && (before ? neighbor?.transition : item.transition))
          return {
            content: original,
            selected,
            error: "这个剪点有转场，请切换到联动修剪或移除转场",
          };
        const room = (item[key] || 0) + (neighbor?.[otherKey] || 0);
        if (shrink < -room && (before || neighbor)) {
          const excess = -shrink - room;
          item.duration_ms -= excess;
          if (before && !source?.image) item.in_point_ms += excess;
        }
        const remaining =
          (item[key] || 0) +
          original.items[index].duration_ms -
          item.duration_ms;
        item[key] = Math.max(0, remaining);
        if (remaining < 0 && neighbor)
          neighbor[otherKey] = Math.max(
            0,
            (neighbor[otherKey] || 0) + remaining,
          );
      }
      preserveTrackEnds(content);
    }
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
