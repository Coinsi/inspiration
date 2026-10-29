import { AudioLines, Clock3, Film, Image, Loader2 } from "lucide-react";
import "./conversation-generation-progress.css";

/** A projection of persisted generation state, never a simulated progress estimate. */
export type MediaProgress = {
  kind: "image" | "video" | "audio";
  phase: "queued" | "generating" | "materializing" | "completed" | "failed" | "stopped";
  title: string;
  message: string;
  started_at: string | null;
  updated_at: string | null;
  completed_outputs: number;
  job_id?: string;
  task_id?: string;
};

export function ConversationGenerationProgress({ progress }: { progress: MediaProgress }) {
  if (!["queued", "generating", "materializing"].includes(progress.phase)) return null;
  const kind = { image: "图片", video: "视频", audio: "音频" }[progress.kind];
  const Icon = progress.kind === "image" ? Image : progress.kind === "video" ? Film : AudioLines;
  const stage = progress.phase === "queued" ? "已提交，等待开始"
    : progress.phase === "materializing" ? "正在读取生成结果" : `正在生成${kind}`;
  return <figure className="conversation-generation" data-phase={progress.phase} aria-label={`${kind}生成进展`}>
    <div className="conversation-generation-viewport" aria-hidden="true">
      <Icon size={30} strokeWidth={1.3} />
      <span>{kind}会显示在这里</span>
    </div>
    <figcaption role="status" aria-live="polite" aria-atomic="true">
      <div className="conversation-generation-heading">
        {progress.phase === "queued" ? <Clock3 size={15} /> : <Loader2 size={15} className="conversation-generation-spin" />}
        <strong>{progress.title || stage}</strong>
      </div>
      <p>{progress.message || stage}</p>
      {progress.completed_outputs > 0 && <small>已有 {progress.completed_outputs} 项结果可查看</small>}
    </figcaption>
  </figure>;
}
