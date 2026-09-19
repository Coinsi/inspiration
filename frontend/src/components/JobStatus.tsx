import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type GenJob } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { ACTIVE_JOBS, JOB_LABEL, taskLabel } from "@/lib/task-center";
export { ACTIVE_JOBS, JOB_LABEL } from "@/lib/task-center";

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
  const {
    data: job,
    error,
    refetch,
  } = useQuery({
    queryKey: ["job", projectId, jobId],
    queryFn: () => api.get<GenJob>(`/projects/${projectId}/jobs/${jobId}`),
    refetchInterval: (q) =>
      q.state.status === "error"
        ? false
        : !q.state.data || ACTIVE_JOBS.has(q.state.data.status)
          ? 2000
          : false,
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
          {job && (
            <span className="mr-2 font-medium">
              {taskLabel(
                job.input_snapshot?.operation || "generate",
                job.request_type,
                lang === "zh",
              )}
            </span>
          )}
          <span>
            {error
              ? (error as Error).message
              : job
                ? lang === "zh"
                  ? job.status === "succeeded" && job.cost_raw?.warnings?.length
                    ? "已返回素材，参数有差异"
                    : JOB_LABEL[job.status]
                  : job.status === "succeeded" && job.cost_raw?.warnings?.length
                    ? "Media returned with parameter mismatch"
                    : job.status
                : lang === "zh"
                  ? "读取任务…"
                  : "Loading task…"}
          </span>
        </span>
        <Link
          className="studio-link"
          to={`/projects/${projectId}/tasks?job=${jobId}`}
        >
          {lang === "zh" ? "查看任务" : "View task"} →
        </Link>
      </div>
      {error && (
        <button className="mt-2 underline" onClick={() => void refetch()}>
          {lang === "zh" ? "重新读取状态" : "Retry status"}
        </button>
      )}
      {job && ACTIVE_JOBS.has(job.status) && job.cost_raw?.events?.length ? (
        <p className="mt-2 break-words text-muted-foreground">
          {job.cost_raw.events[job.cost_raw.events.length - 1]?.message}
        </p>
      ) : null}
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
