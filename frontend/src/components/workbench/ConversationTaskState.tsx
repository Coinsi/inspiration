import { useEffect, useState } from "react";
import { AlertCircle, Loader2, RotateCcw, Pencil } from "lucide-react";

export function ConversationTaskState({
  status,
  kind,
  createdAt,
  progress,
  error,
  disabled,
  retrying,
  retry,
  edit,
}: {
  status: string;
  kind: string;
  createdAt: string;
  progress?: string;
  error?: string;
  disabled: boolean;
  retrying: boolean;
  retry: () => void;
  edit: () => void;
}) {
  const [now, setNow] = useState(Date.now());
  const waiting = status === "queued" || status === "running";
  useEffect(() => {
    if (!waiting) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [waiting, createdAt]);
  if (waiting) {
    const seconds =
      Math.max(0, Math.floor((now - new Date(createdAt).getTime()) / 1000)) ||
      0;
    return (
      <div className="conversation-task-state" role="status">
        <div className="conversation-task-title">
          <Loader2 className="conversation-spinner" size={19} />
          <strong>
            {status === "queued"
              ? "任务已提交，正在准备"
              : `正在${kind === "图片" ? "生成图片" : kind === "视频" ? "生成视频" : "处理你的请求"}`}
          </strong>
          <span>
            已等待 {Math.floor(seconds / 60)}:
            {String(seconds % 60).padStart(2, "0")}
          </span>
        </div>
        <details className="conversation-task-progress">
          <summary>查看进度</summary>
          <p>{progress || (kind === "视频" || kind === "图片"
            ? "正在等待生成服务返回结果，完成后会显示在这里。"
            : "正在整理上下文并执行任务，有进展会在这里更新。")}</p>
          <small>可以离开此页，任务会继续执行。</small>
        </details>
      </div>
    );
  }
  if (status !== "failed" && status !== "cancelled") return null;
  return (
    <div className="conversation-task-state is-failed">
      <div className="conversation-task-title">
        <AlertCircle size={19} />
        <strong>
          {status === "failed" ? `${kind}任务未完成` : "任务已停止"}
        </strong>
      </div>
      <p role={status === "failed" ? "alert" : undefined}>
        {error || "原来的创作要求已保留，可以调整后再次尝试。"}
      </p>
      {error?.includes("404") && (
        <small>
          服务返回 404，请检查模型服务地址及接入方式；直接重试可能仍会失败。
        </small>
      )}
      <div className="conversation-task-actions">
        <button disabled={disabled} onClick={retry}>
          <RotateCcw size={15} />
          {retrying ? "正在提交…" : "重试"}
        </button>
        <button disabled={disabled} onClick={edit}>
          <Pencil size={15} />
          修改后重试
        </button>
      </div>
      <small>重试会按原描述和参数创建新任务，按模型服务规则计费。</small>
    </div>
  );
}
