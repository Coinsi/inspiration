import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Activity, RefreshCw } from "lucide-react";
import { api, type GenJob } from "@/lib/api";
import { ACTIVE_JOBS, JOB_LABEL } from "@/components/JobStatus";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useConfirm } from "@/components/ui/confirm";
import { useI18n } from "@/lib/i18n";

export default function Tasks() {
  const { projectId } = useParams();
  const [search] = useSearchParams();
  const { lang } = useI18n();
  const zh = lang === "zh";
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState(search.get("job") || "");
  const detailRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (open && window.innerWidth < 1280)
      detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [open]);
  const toast = useToast();
  const confirm = useConfirm();
  const qc = useQueryClient();
  const base = `/projects/${projectId}`;
  const query = useQuery({
    queryKey: ["jobs", projectId, status, page],
    queryFn: () =>
      api.get<GenJob[]>(
        `${base}/jobs?status=${status}&limit=30&offset=${page * 30}`,
      ),
    refetchInterval: 3000,
  });
  const detail = useQuery({
    queryKey: ["job", projectId, open],
    queryFn: () => api.get<GenJob>(`${base}/jobs/${open}`),
    enabled: !!open,
    refetchInterval: (q) =>
      q.state.data && ACTIVE_JOBS.has(q.state.data.status) ? 2000 : false,
  });
  const action = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: string }) =>
      api.post<GenJob>(`${base}/jobs/${id}/${kind}`),
    onSuccess: (j) => {
      setOpen(j.id);
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["job"] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const label = (j: GenJob) => {
    const op = j.input_snapshot.operation || "generate";
    return zh
      ? {
          refine: "图片精修",
          video_frames: "视频抽帧",
          video_audio: "提取音轨",
          video_trim: "导出视频片段",
          inpaint: "局部重绘",
          render: "成片导出",
          upload: "素材上传",
          generate: j.request_type === "video" ? "视频生成" : "图片生成",
        }[op] || op
      : op;
  };
  const selected = detail.data;
  return (
    <div className="studio-page space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Activity className="h-5 w-5" />
            {zh ? "任务中心" : "Task center"}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {zh
              ? "图片、视频与成片处理记录，刷新或离开页面后仍可查看。"
              : "Persistent image, video and film processing history."}
          </p>
        </div>
        <Button variant="outline" onClick={() => void query.refetch()}>
          <RefreshCw className="mr-2 h-4 w-4" />
          {zh ? "刷新" : "Refresh"}
        </Button>
      </header>
      <div className="flex flex-wrap gap-1.5">
        {["", ...Object.keys(JOB_LABEL)].map((s) => (
          <Button
            key={s}
            size="sm"
            variant={s === status ? "default" : "outline"}
            onClick={() => {
              setStatus(s);
              setPage(0);
            }}
          >
            {s ? (zh ? JOB_LABEL[s] : s) : zh ? "全部" : "All"}
          </Button>
        ))}
      </div>
      {query.error && (
        <p role="alert" className="text-sm text-danger">
          {(query.error as Error).message}
        </p>
      )}
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section
          className="min-w-0 space-y-2"
          aria-label={zh ? "任务列表" : "Task list"}
        >
          {query.isLoading && <p>{zh ? "加载中…" : "Loading…"}</p>}
          {query.data?.length === 0 && (
            <div className="studio-empty">
              {zh
                ? "暂无任务。从镜头或资产详情开始生成，也可以上传已有素材。"
                : "No tasks. Generate or upload media from a shot or asset."}
            </div>
          )}
          {query.data?.map((j) => (
            <button
              key={j.id}
              onClick={() => setOpen(j.id)}
              className={`w-full rounded-xl border p-4 text-left ${open === j.id ? "border-primary bg-elevated" : "border-border bg-card hover:bg-elevated"}`}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium">{label(j)}</span>
                <span
                  className={`text-xs ${j.status === "failed" ? "text-danger" : "text-muted-foreground"}`}
                >
                  {j.cost_raw?.warnings?.length
                    ? zh
                      ? "参数有差异"
                      : "Parameter mismatch"
                    : zh
                      ? JOB_LABEL[j.status]
                      : j.status}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>
                  {j.provider === "mock"
                    ? "Mock · " + (zh ? "测试素材" : "test media")
                    : j.provider === "local"
                      ? zh
                        ? "本地处理"
                        : "Local"
                      : j.provider}
                </span>
                <time>{new Date(j.created_at).toLocaleString()}</time>
                <span>#{j.id.slice(0, 8)}</span>
              </div>
              {j.error && (
                <p className="mt-2 line-clamp-2 break-words text-xs text-danger">
                  {j.error}
                </p>
              )}
            </button>
          ))}
          <div className="flex items-center justify-between py-2">
            <Button
              size="sm"
              variant="ghost"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              {zh ? "上一页" : "Previous"}
            </Button>
            <span className="text-xs text-muted-foreground">{page + 1}</span>
            <Button
              size="sm"
              variant="ghost"
              disabled={(query.data?.length || 0) < 30}
              onClick={() => setPage((p) => p + 1)}
            >
              {zh ? "下一页" : "Next"}
            </Button>
          </div>
        </section>
        <aside
          ref={detailRef}
          className={`min-w-0 rounded-xl border border-border bg-card p-4 order-first xl:order-last ${open ? "" : "hidden xl:block"}`}
        >
          {selected ? (
            <div className="space-y-4">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <h2 className="font-medium">{label(selected)}</h2>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="xl:hidden"
                    onClick={() => setOpen("")}
                  >
                    {zh ? "收起" : "Close"}
                  </Button>
                </div>
                <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                  {selected.id}
                </p>
              </div>
              <div className="text-xs text-muted-foreground">
                {zh ? "预估 / 记录点数" : "Estimated / recorded points"}:{" "}
                {selected.estimated_cost ?? "—"} / {selected.actual_cost ?? "—"}
              </div>
              <Link
                className="studio-link inline-block text-sm"
                to={`${base}/${selected.target_type === "timeline" ? `cuts?timeline=${selected.target_id}` : selected.target_type === "asset" ? `assets/${selected.target_id}` : `shots?shot=${selected.target_id}`}`}
              >
                {zh ? "打开对应素材 / 时间线" : "Open source / timeline"} →
              </Link>
              <div className="flex flex-wrap gap-2">
                {ACTIVE_JOBS.has(selected.status) && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={action.isPending}
                    onClick={async () => {
                      if (
                        await confirm({
                          title: zh ? "取消任务" : "Cancel task",
                          message: zh
                            ? "停止本地执行或接收结果。已发送给云端供应商的请求可能仍会运行和计费。"
                            : "Stop local processing or receiving results. An already submitted cloud request may still run and incur charges.",
                        })
                      )
                        action.mutate({ id: selected.id, kind: "cancel" });
                    }}
                  >
                    {zh ? "取消任务" : "Cancel task"}
                  </Button>
                )}
                {["failed", "canceled"].includes(selected.status) && (
                  <Button
                    size="sm"
                    disabled={action.isPending}
                    onClick={async () => {
                      if (
                        selected.provider === "local" ||
                        selected.provider === "mock" ||
                        (await confirm({
                          title: zh ? "重试任务" : "Retry task",
                          message: zh
                            ? "按原始输入创建新请求，会重新计费。"
                            : "Create a new request with the original input. Provider charges may apply again.",
                        }))
                      )
                        action.mutate({ id: selected.id, kind: "retry" });
                    }}
                  >
                    {zh ? "重试" : "Retry"}
                  </Button>
                )}
              </div>
              {selected.cost_raw?.warnings?.map((warning, i) => (
                <p
                  key={i}
                  role="alert"
                  className="rounded-md border border-border bg-elevated p-3 text-xs leading-5"
                >
                  {warning}
                </p>
              ))}
              <ol className="space-y-3 border-l border-border pl-3">
                {selected.cost_raw?.events?.map((e, i) => (
                  <li key={i} className="text-xs">
                    <time className="text-faint">
                      {new Date(e.at).toLocaleTimeString()}
                    </time>
                    <p className="mt-1 break-words leading-5">{e.message}</p>
                  </li>
                )) || (
                  <li className="text-xs text-muted-foreground">
                    {zh
                      ? "早期任务未记录执行日志"
                      : "No event log for this older task"}
                  </li>
                )}
              </ol>
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground">
                  {zh ? "输入快照" : "Input snapshot"}
                </summary>
                <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md bg-elevated p-3">
                  {JSON.stringify(selected.input_snapshot, null, 2)}
                </pre>
              </details>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              {detail.error
                ? (detail.error as Error).message
                : zh
                  ? "选择任务查看详情"
                  : "Select a task for details"}
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
