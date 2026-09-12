import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type GenJob } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export const ACTIVE_JOBS = new Set(["pending", "submitted", "running"]);
export const JOB_LABEL: Record<string, string> = {
  pending: "排队中",
  submitted: "准备中",
  running: "处理中",
  succeeded: "已完成",
  failed: "失败",
  canceled: "已取消",
};
export function JobStatus({
  projectId,
  jobId,
}: {
  projectId: string;
  jobId: string;
}) {
  const { lang } = useI18n();
  const qc = useQueryClient();
  const refreshed = useRef("");
  const { data: job, error } = useQuery({
    queryKey: ["job", projectId, jobId],
    queryFn: () => api.get<GenJob>(`/projects/${projectId}/jobs/${jobId}`),
    refetchInterval: (q) =>
      !q.state.data || ACTIVE_JOBS.has(q.state.data.status) ? 2000 : false,
  });
  useEffect(() => {
    if (job && !ACTIVE_JOBS.has(job.status) && refreshed.current !== job.id) {
      refreshed.current = job.id;
      for (const key of ["gens", "quota", "jobs", "all-shots", "assets"])
        void qc.invalidateQueries({ queryKey: [key] });
    }
  }, [job, qc]);
  return (
    <div
      className="rounded-lg border border-border bg-elevated px-3 py-2 text-xs"
      role="status"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>
          {error
            ? (error as Error).message
            : job
              ? lang === "zh"
                ? job.cost_raw?.warnings?.length
                  ? "已返回素材，参数有差异"
                  : JOB_LABEL[job.status]
                : job.cost_raw?.warnings?.length
                  ? "Media returned with parameter mismatch"
                  : job.status
              : lang === "zh"
                ? "读取任务…"
                : "Loading task…"}
        </span>
        <Link
          className="studio-link"
          to={`/projects/${projectId}/tasks?job=${jobId}`}
        >
          {lang === "zh" ? "查看任务" : "View task"} →
        </Link>
      </div>
      {job?.error && (
        <p className="mt-2 break-words text-danger">{job.error}</p>
      )}
      {job?.cost_raw?.warnings?.map((warning, i) => (
        <p key={i} className="mt-2 break-words text-muted-foreground">
          {warning}
        </p>
      ))}
    </div>
  );
}
