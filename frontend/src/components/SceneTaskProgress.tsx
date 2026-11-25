import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/lib/i18n";
import { sceneProgressLabel, type SceneProgress } from "@/lib/task-center";

export type SceneProgressRow = {
  title: string;
  state: "pending" | "running" | "succeeded" | "failed";
  shots: number;
  error?: string | null;
  shotTitles?: string[];
};

export function SceneTaskProgress({
  status,
  progress,
}: {
  status: string;
  progress?: SceneProgress | null;
}) {
  const zh = useI18n().lang === "zh";
  const queued = status === "queued" || status === "pending";
  const running = status === "running";
  const retry = (progress?.attempt ?? 1) > 1;
  const shot = progress?.unit === "shot";
  let note = "";
  if (queued) {
    note = retry
      ? zh
        ? "重试已排队"
        : "Retry queued"
      : zh
        ? "已排队，等待开始拆镜"
        : "Queued for breakdown";
  } else if (running && progress?.current) {
    note = zh
      ? `${retry ? "正在重试" : "正在拆解"}第 ${progress.current} ${shot ? "镜" : "场"}`
      : `${retry ? "Retrying" : "Working on"} ${shot ? "shot" : "scene"} ${progress.current}`;
    if (progress.current_title) note += " · " + progress.current_title;
  } else if (running) {
    note = zh ? "正在整理场次" : "Preparing scenes";
  } else if (retry && progress) {
    const added = Math.max(0, progress.completed - (progress.preserved ?? 0));
    note = zh
      ? `本次重试完成了 ${added} 场`
      : `${added} scenes recovered in this retry`;
  }
  return (
    <div
      className="space-y-1.5"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {progress && progress.total > 0 && (
        <>
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
            <strong className="font-medium">
              {sceneProgressLabel(progress, zh)}
            </strong>
            {progress.shots != null && (
              <span className="text-xs text-muted-foreground">
                {zh ? `${progress.shots} 个镜头` : `${progress.shots} shots`}
              </span>
            )}
          </div>
          <progress
            className="studio-progress"
            max={progress.total}
            value={progress.completed}
            aria-label={
              shot
                ? zh
                  ? "已完成镜头"
                  : "Completed shots"
                : zh
                  ? "已完成场次"
                  : "Completed scenes"
            }
          />
        </>
      )}
      {note && (
        <p className="break-words text-sm text-muted-foreground">{note}</p>
      )}
    </div>
  );
}

export function SceneTaskRows({
  rows,
  stopped,
  unit = "scene",
}: {
  rows: SceneProgressRow[];
  stopped: boolean;
  unit?: SceneProgress["unit"];
}) {
  const zh = useI18n().lang === "zh";
  return (
    <ol className="divide-y divide-border">
      {rows.map((row, i) => {
        const unfinished =
          stopped && (row.state === "running" || row.state === "pending");
        const labels = {
          pending: zh ? "等待中" : "Waiting",
          running: zh ? "拆镜中" : "In progress",
          failed: zh ? "失败" : "Failed",
          succeeded:
            unit === "shot"
              ? zh
                ? "已完成"
                : "Completed"
              : zh
                ? `${row.shots} 个镜头`
                : `${row.shots} shots`,
        };
        return (
          <li key={i} className="flex items-start gap-3 py-3">
            <span className="pt-0.5 text-xs tabular-nums text-muted-foreground">
              {String(i + 1).padStart(2, "0")}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
                <span className="break-words text-sm font-medium">
                  {row.title}
                </span>
                <Badge
                  variant={
                    unfinished
                      ? "warning"
                      : row.state === "failed"
                        ? "danger"
                        : row.state === "running"
                          ? "info"
                          : "default"
                  }
                >
                  {unfinished
                    ? zh
                      ? "未完成"
                      : "Unfinished"
                    : labels[row.state]}
                </Badge>
              </div>
              {row.state === "failed" && row.error && (
                <p className="mt-1 break-words text-xs text-danger">
                  {row.error}
                </p>
              )}
              {!!row.shotTitles?.length && (
                <details className="mt-1.5 text-xs text-muted-foreground">
                  <summary className="w-fit cursor-pointer">
                    {zh ? "查看镜头" : "Show shots"}
                  </summary>
                  <p className="mt-1.5 leading-relaxed">
                    {row.shotTitles.join(" · ")}
                  </p>
                </details>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
