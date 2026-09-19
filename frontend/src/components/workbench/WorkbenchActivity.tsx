import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { ACTIVE_JOBS, JOB_LABEL, taskLabel } from "@/lib/task-center";
import { LoadState } from "./LoadState";
import { api, type GenJob, type Quota } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
export function WorkbenchActivity({ projectId }: { projectId: string }) {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const base = `/projects/${projectId}`;
  const quota = useQuery({
    queryKey: ["quota", projectId],
    queryFn: () => api.get<Quota | null>(`${base}/quota`),
  });
  const jobs = useQuery({
    queryKey: ["jobs", projectId, "workbench"],
    queryFn: () => api.get<GenJob[]>(`${base}/jobs?limit=5`),
    refetchInterval: 10000,
  });
  const viewAll = (path: string) => (
    <Link
      to={`${base}/${path}`}
      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary"
    >
      {zh ? "查看全部" : "View all"}
      <ArrowUpRight className="h-3.5 w-3.5" />
    </Link>
  );
  return (
    <section
      className="rounded-xl border bg-card p-5"
      aria-labelledby="jobs-heading"
    >
      <div className="mb-4 flex items-center justify-between">
        <h2 id="jobs-heading" className="text-sm font-semibold">
          {zh ? "最近任务" : "Recent tasks"}
        </h2>
        {viewAll("tasks")}
      </div>
      <LoadState
        loading={jobs.isPending}
        error={jobs.isError}
        retry={() => void jobs.refetch()}
      >
        {jobs.data?.length ? (
          <div className="space-y-1">
            {jobs.data.map((job) => {
              const name = taskLabel(
                job.input_snapshot.operation || "generate",
                job.request_type,
                zh,
              );
              return (
                <Link
                  key={job.id}
                  to={`${base}/tasks?job=${job.id}`}
                  className="flex items-start gap-3 rounded-lg py-3 hover:bg-elevated"
                >
                  <span
                    className={cn(
                      "mt-1 h-2 w-2 shrink-0 rounded-full",
                      job.status === "failed"
                        ? "bg-danger"
                        : ACTIVE_JOBS.has(job.status)
                          ? "bg-warning"
                          : job.status === "succeeded"
                            ? "bg-success"
                            : "bg-faint",
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-medium">{name}</p>
                    <p className="mt-1 text-[10px] text-faint">
                      {new Date(job.created_at).toLocaleString(
                        zh ? "zh-CN" : "en-US",
                        {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        },
                      )}
                    </p>
                  </div>
                  <span className="text-[11px] text-muted-foreground">
                    {zh ? (JOB_LABEL[job.status] ?? job.status) : job.status}
                  </span>
                </Link>
              );
            })}
          </div>
        ) : (
          <div className="py-5 text-center text-xs leading-6 text-muted-foreground">
            <Activity className="mx-auto mb-2 h-5 w-5 text-faint" />
            {zh
              ? "生成和导出任务会显示在这里。"
              : "Generation and export tasks will appear here."}
          </div>
        )}
      </LoadState>
      <div className="mt-3 flex items-center justify-between gap-2 border-t pt-4 text-xs">
        <span className="text-muted-foreground">
          {zh ? "项目用量" : "Project usage"}
        </span>
        <span>
          {quota.isError
            ? zh
              ? "暂不可用"
              : "Unavailable"
            : quota.isPending
              ? "…"
              : quota.data && quota.data.limit_cost > 0
                ? `${Math.round(quota.data.used_cost)} / ${quota.data.limit_cost}`
                : zh
                  ? "未设置限额"
                  : "No limit set"}
        </span>
      </div>
    </section>
  );
}
