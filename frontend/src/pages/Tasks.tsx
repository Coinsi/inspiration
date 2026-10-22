import { StudioSelect } from "@/components/ui/studio-select";
import { SearchField } from "@/components/ui/search-field";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Clock3,
  Download,
  Film,
  Image,
  Music2,
  RefreshCw,
  X,
} from "lucide-react";
import { api, blobUrl, downloadBlob, type GenJob } from "@/lib/api";
import {
  ACTIVE_JOBS,
  JOB_LABEL,
  OPERATIONS,
  mediaExtension,
  taskLabel,
  taskSource,
  taskError,
  type TaskCatalog,
  type TaskWorkspace,
} from "@/lib/task-center";
import { Button } from "@/components/ui/button";
import { MediaVideo } from "@/components/MediaVideo";
import { MediaAudio } from "@/components/MediaAudio";
import { MediaPreview } from "@/components/MediaPreview";
import { useToast } from "@/components/ui/toast";
import { useConfirm } from "@/components/ui/confirm";
import { useI18n } from "@/lib/i18n";
import { ActivityDetail } from "@/components/ActivityDetail";
import "./tasks.css";

export default function Tasks() {
  const { projectId } = useParams();
  return <TaskPage key={projectId} projectId={projectId!} />;
}

function TaskPage({ projectId }: { projectId: string }) {
  const [search, setSearch] = useSearchParams(),
    zh = useI18n().lang === "zh";
  const status = search.get("status") || "",
    operation = search.get("operation") || "",
    term = search.get("search") || "";
  const page = Math.min(
      4166,
      Math.max(0, Math.floor(Number(search.get("page")) || 0)),
    ),
    open = search.get("job") || search.get("task") || "",
    kind = search.get("job")
      ? "generation"
      : search.get("kind") || "generation";
  const [draft, setDraft] = useState(term),
    detailRef = useRef<HTMLElement>(null);
  useEffect(() => setDraft(term), [term]);
  useEffect(() => {
    if (open && window.innerWidth < 1150)
      detailRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [open]);
  const toast = useToast(),
    confirm = useConfirm(),
    qc = useQueryClient(),
    base = `/projects/${projectId}`;
  const returnTo = search.get("return")?.startsWith(`${base}/`)
    ? search.get("return")!
    : "";
  const fromConversation =
    returnTo.split("?")[0] === `${base}/workbench` &&
    new URLSearchParams(returnTo.split("?")[1]).has("conversation");
  const navigate = (patch: Record<string, string>) =>
    setSearch((previous) => {
      const next = new URLSearchParams(previous);
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      if (
        ["status", "operation", "search", "page"].some((key) => key in patch)
      ) {
        next.delete("job");
        next.delete("task");
        next.delete("kind");
      }
      return next;
    });
  const select = (id: string, taskKind = "generation") =>
    navigate({
      job: taskKind === "generation" ? id : "",
      task: taskKind !== "generation" ? id : "",
      kind: taskKind !== "generation" ? taskKind : "",
    });
  const query = useQuery({
    queryKey: ["jobs", projectId, "catalog", status, operation, term, page],
    queryFn: () =>
      api.get<TaskCatalog>(
        `${base}/activity?${new URLSearchParams({ status, operation, search: term, offset: String(page * 24), limit: "24" })}`,
      ),
    refetchInterval: 5000,
  });
  const detail = useQuery({
    queryKey: ["task-workspace", projectId, open],
    enabled: !!open && kind === "generation",
    queryFn: () => api.get<TaskWorkspace>(`${base}/jobs/${open}/workspace`),
    refetchInterval: (q) =>
      q.state.data &&
      (ACTIVE_JOBS.has(q.state.data.job.status) ||
        q.state.data.retries.some((r) => ACTIVE_JOBS.has(r.status)))
        ? 2000
        : false,
  });
  const action = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: string }) =>
      api.post<GenJob>(`${base}/jobs/${id}/${kind}`),
    onSuccess: (j) => {
      select(j.id);
      void qc.invalidateQueries({ queryKey: ["jobs", projectId] });
      void qc.invalidateQueries({ queryKey: ["task-workspace", projectId] });
      void qc.invalidateQueries({ queryKey: ["job", projectId] });
    },
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  const perform = async (kind: "retry" | "cancel") => {
    const j = detail.data?.job;
    if (!j) return;
    if (
      kind === "cancel" &&
      !(await confirm({
        title: zh ? "取消任务" : "Cancel task",
        message: zh
          ? "停止本地执行或接收结果。已发送给云端的请求可能仍会运行和计费。"
          : "Stop local execution or receiving results. Cloud requests may still run and incur charges.",
      }))
    )
      return;
    if (
      kind === "retry" &&
      !["local", "mock"].includes(j.provider) &&
      !(await confirm({
        title: zh ? "重试任务" : "Retry task",
        message: zh
          ? "按原始输入重新请求，可能再次产生供应商费用。"
          : "Retry the original input; provider charges may apply again.",
      }))
    )
      return;
    action.mutate({ id: j.id, kind });
  };
  const counts = query.data?.counts,
    all = counts ? Object.values(counts).reduce((a, b) => a + b, 0) : undefined;
  const filters = [
    { key: "", label: zh ? "全部" : "All", count: all },
    {
      key: "attention",
      label: zh ? "需要处理" : "Needs attention",
      count: counts
        ? (counts.failed || 0) +
          (counts.paused || 0) +
          (counts.needs_review || 0)
        : undefined,
    },
    {
      key: "active",
      label: zh ? "进行中" : "In progress",
      count: counts
        ? Array.from(ACTIVE_JOBS).reduce((n, s) => n + (counts[s] || 0), 0)
        : undefined,
    },
    {
      key: "failed",
      label: zh ? "失败" : "Failed",
      count: counts ? counts.failed || 0 : undefined,
    },
    {
      key: "succeeded",
      label: zh ? "已完成" : "Completed",
      count: counts ? counts.succeeded || 0 : undefined,
    },
    {
      key: "canceled",
      label: zh ? "已取消" : "Canceled",
      count: counts ? counts.canceled || 0 : undefined,
    },
  ];
  return (
    <div className="task-page">
      {returnTo && (
        <div className="task-return-bar">
          <Link to={returnTo}>
            <ArrowLeft size={16} />
            {zh
              ? fromConversation
                ? "返回对话"
                : "返回创作"
              : "Back to creation"}
          </Link>
        </div>
      )}
      <header className="task-page-header">
        <div>
          <h1>
            <Activity size={22} />
            {zh ? "任务中心" : "Task center"}
          </h1>
          <p>
            {zh
              ? "查看创作进度、结果与需要处理的任务。"
              : "Track generation and exports, find results, and continue creating."}
          </p>
        </div>
        <Button
          variant="outline"
          disabled={query.isFetching}
          onClick={() => {
            void query.refetch();
            if (open && kind === "generation") void detail.refetch();
            void qc.invalidateQueries({
              queryKey: ["activity-detail", projectId],
            });
          }}
        >
          <RefreshCw
            size={14}
            className={query.isFetching ? "animate-spin" : ""}
          />
          {zh ? "刷新任务" : "Refresh tasks"}
        </Button>
      </header>
      <div className="task-toolbar">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            navigate({ search: draft.trim(), page: "" });
          }}
        >
          <SearchField
            label={zh ? "搜索任务" : "Search tasks"}
            placeholder={
              zh
                ? "搜索创作、素材或任务编号"
                : "Search source names or task IDs"
            }
            value={draft}
            maxLength={200}
            onValueChange={setDraft}
            onClear={() => navigate({ search: "", page: "" })}
            className="flex-1"
          />
          <Button type="submit" variant="outline">
            {zh ? "搜索" : "Search"}
          </Button>
        </form>
        <StudioSelect
          aria-label={zh ? "任务类型" : "Task type"}
          value={operation}
          onValueChange={(selectedValue) =>
            navigate({ operation: selectedValue, page: "" })
          }
        >
          <option value="">{zh ? "全部任务类型" : "All task types"}</option>
          {Object.entries(OPERATIONS).map(([key, label]) => (
            <option key={key} value={key}>
              {label[zh ? 0 : 1]}
            </option>
          ))}
        </StudioSelect>
        {(term || operation || status) && (
          <button
            className="task-reset"
            onClick={() =>
              navigate({ search: "", operation: "", status: "", page: "" })
            }
          >
            {zh ? "清除筛选" : "Clear filters"}
          </button>
        )}
      </div>
      <div
        className="task-status-filters"
        aria-label={zh ? "任务状态" : "Task status"}
      >
        {filters.map((f) => (
          <button
            key={f.key}
            aria-pressed={status === f.key}
            onClick={() => navigate({ status: f.key, page: "" })}
          >
            {f.label}
            <span>{query.isError ? "—" : (f.count ?? "…")}</span>
          </button>
        ))}
        {status && !filters.some((f) => f.key === status) && (
          <button
            aria-pressed="true"
            onClick={() => navigate({ status: "", page: "" })}
          >
            {JOB_LABEL[status] || status} ×
          </button>
        )}
      </div>
      <p className="task-count-hint">
        {zh
          ? "数量按当前类型和搜索范围统计；每 5 秒更新。"
          : "Counts reflect type/search filters; refreshed every 5 seconds."}
      </p>
      <div className={`task-workspace ${open ? "has-detail" : ""}`}>
        <section
          className="task-list"
          aria-label={zh ? "任务列表" : "Task list"}
        >
          {query.error && (
            <div role="alert" className="task-error">
              {(query.error as Error).message}
              <button onClick={() => void query.refetch()}>
                {zh ? "重新读取" : "Try again"}
              </button>
            </div>
          )}
          {query.isLoading && (
            <div className="task-empty">
              <Clock3 />
              {zh ? "正在读取创作记录…" : "Loading creative activity…"}
            </div>
          )}
          {!query.isError && query.data?.items.length === 0 && (
            <div className="task-empty">
              <Activity />
              <h2>
                {zh
                  ? term || status || operation
                    ? "没有符合条件的任务"
                    : "从一次创作开始"
                  : "No tasks found"}
              </h2>
              <p>
                {zh
                  ? term || status || operation
                    ? "调整搜索或筛选条件后再试。"
                    : "对话、生成、画布、素材处理与导出记录都会保留在这里。"
                  : "Generate media or adjust your filters."}
              </p>
              {page > 0 ? (
                <Button
                  variant="outline"
                  onClick={() => navigate({ page: "" })}
                >
                  {zh ? "返回第一页" : "First page"}
                </Button>
              ) : (
                <Link to={`${base}/shots`}>
                  {zh ? "进入镜头创作" : "Open shots"} →
                </Link>
              )}
            </div>
          )}
          {query.data?.items.map((j) => (
            <button
              key={`${j.kind}:${j.id}`}
              data-task-id={j.id}
              className={`task-row ${open === j.id ? "is-selected" : ""}`}
              aria-pressed={open === j.id}
              onClick={() => select(j.id, j.kind)}
            >
              <span className={`task-type-icon status-${j.status}`}>
                {j.kind !== "generation" ? (
                  <Activity />
                ) : j.operation === "render" || j.request_type === "video" ? (
                  <Film />
                ) : j.request_type === "audio" ? (
                  <Music2 />
                ) : (
                  <Image />
                )}
              </span>
              <div className="task-row-content">
                <div>
                  <strong>{taskLabel(j.operation, j.request_type, zh)}</strong>
                  <span className={`task-status status-${j.status}`}>
                    {zh ? JOB_LABEL[j.status] : j.status}
                  </span>
                </div>
                <h3>
                  {j.target_name || (zh ? "未命名来源" : "Untitled source")}
                  {!j.target_available && (
                    <small>{zh ? " · 来源已移除" : " · Source removed"}</small>
                  )}
                </h3>
                {(j.error || j.has_warnings || j.last_event) && <p className={j.error ? "text-danger" : ""}>
                  {(j.error ? taskError(j.error, zh) : null) ||
                    (j.has_warnings
                      ? zh
                        ? "结果参数有差异，查看详情"
                        : "Result parameters differ; view details"
                      : j.last_event) ||
                    (zh
                      ? "查看结果、影响范围与恢复方式"
                      : "View results, scope and recovery")}
                </p>}
                <footer>
                  <span>
                    {j.provider === "local"
                      ? zh
                        ? "本地处理"
                        : "Local"
                      : j.provider === "mock"
                        ? zh
                          ? "Mock · 测试"
                          : "Mock · Test"
                        : j.model_name || j.provider}
                  </span>
                  <time>
                    {new Date(j.created_at).toLocaleString(
                      zh ? "zh-CN" : "en-US",
                    )}
                  </time>
                  {j.result_count > 0 && (
                    <span>
                      {j.result_count}
                      {zh ? " 个结果" : " results"}
                    </span>
                  )}
                  <span>#{j.id.slice(0, 8)}</span>
                </footer>
              </div>
              <ArrowUpRight size={15} />
            </button>
          ))}
          <footer className="task-pagination">
            <Button
              size="sm"
              variant="ghost"
              disabled={page === 0 || query.isFetching}
              onClick={() =>
                navigate({ page: page === 1 ? "" : String(page - 1) })
              }
            >
              {zh ? "上一页" : "Previous"}
            </Button>
            <span>
              {page + 1} · {query.isError ? "—" : (query.data?.total ?? "…")}{" "}
              {zh ? "条记录" : "tasks"}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={
                !query.data?.has_more || query.isFetching || page >= 4166
              }
              onClick={() => navigate({ page: String(page + 1) })}
            >
              {zh ? "下一页" : "Next"}
            </Button>
          </footer>
        </section>
        <aside
          hidden={!open}
          ref={detailRef}
          className={`task-detail ${open ? "is-open" : ""}`}
          aria-label={zh ? "任务详情" : "Task details"}
        >
          <header>
            <h2>{zh ? "任务详情" : "Task details"}</h2>
            {open && (
              <button
                aria-label={zh ? "关闭任务详情" : "Close task details"}
                onClick={() => select("")}
              >
                <X size={16} />
              </button>
            )}
          </header>
          {!open ? (
            <div className="task-empty">
              <Activity />
              <p>
                {zh
                  ? "选择一条任务，查看结果和执行过程。"
                  : "Choose a task to inspect its results and activity."}
              </p>
            </div>
          ) : kind !== "generation" ? (
            <ActivityDetail
              key={`${kind}:${open}`}
              projectId={projectId}
              kind={kind}
              id={open}
            />
          ) : detail.error ? (
            <div role="alert" className="task-error">
              {(detail.error as Error).message}
              <button onClick={() => void detail.refetch()}>
                {zh ? "重新读取详情" : "Retry details"}
              </button>
            </div>
          ) : detail.isLoading ? (
            <div className="task-empty">
              {zh ? "读取任务详情…" : "Loading task…"}
            </div>
          ) : (
            detail.data && (
              <TaskDetail
                key={open}
                data={detail.data}
                projectId={projectId}
                onSelect={select}
                onAction={perform}
                busy={action.isPending}
              />
            )
          )}
        </aside>
      </div>
    </div>
  );
}

function TaskDetail({
  data,
  projectId,
  onSelect,
  onAction,
  busy,
}: {
  data: TaskWorkspace;
  projectId: string;
  onSelect: (id: string) => void;
  onAction: (kind: "retry" | "cancel") => void;
  busy: boolean;
}) {
  const zh = useI18n().lang === "zh",
    { summary: s, job: j } = data,
    [index, setIndex] = useState(0),
    toast = useToast();
  const [originParams] = useSearchParams();
  const conversationOrigin = originParams.get("return");
  const materialReturn = conversationOrigin?.startsWith(
    `/projects/${projectId}/workbench?`,
  )
    ? conversationOrigin
    : `/projects/${projectId}/tasks?${originParams}`;
  const result =
      data.outputs[Math.min(index, Math.max(0, data.outputs.length - 1))],
    source = s.target_available
      ? taskSource(projectId, s.target_type, s.target_id)
      : null;
  const download = useMutation({
    mutationFn: () =>
      downloadBlob(
        projectId,
        result.output_blob_hash!,
        `inspiration-${result.id.slice(0, 8)}.${mediaExtension(result.output_mime)}`,
      ),
    onError: (e) => toast.push((e as Error).message, "error"),
  });
  return (
    <div className="task-detail-body">
      <div className="task-detail-title">
        <span className={`task-status status-${j.status}`}>
          {zh ? JOB_LABEL[j.status] : j.status}
        </span>
        <h3>{taskLabel(s.operation, j.request_type, zh)}</h3>
        <p>{s.target_name}</p>
      </div>
      {source ? (
        <Link className="task-source-link" to={source}>
          {zh ? "回到创作位置" : "Return to creation"}
          <ArrowUpRight size={14} />
        </Link>
      ) : (
        <p className="text-xs text-muted-foreground">
          {zh
            ? "来源已移除，保留任务记录与已有结果。"
            : "Source removed; task history and existing results remain."}
        </p>
      )}
      {result ? (
        <section
          className="task-output"
          aria-label={zh ? "任务结果" : "Task results"}
        >
          <div className="task-output-screen">
            {result.provider === "mock" ? (
              <p className="task-result-empty">
                离线测试占位内容，不是真实模型生成结果。
              </p>
            ) : result.output_type === "video" ? (
              <MediaVideo
                presentation="studio"
                src={blobUrl(projectId, result.output_blob_hash!)}
                label={zh ? "任务视频结果" : "Task video result"}
              />
            ) : result.output_type === "audio" ? (
              <MediaAudio
                key={result.id}
                src={blobUrl(projectId, result.output_blob_hash!)}
              />
            ) : (
              <MediaPreview
                key={result.id}
                filename={`result-${result.id.slice(0, 8)}`}
                label={zh ? "任务图片结果" : "Task image result"}
                src={blobUrl(projectId, result.output_blob_hash!)}
              />
            )}
          </div>
          <div className="task-output-bar">
            <span>
              {index + 1} / {data.outputs.length}
              {s.result_count > data.outputs.length
                ? zh
                  ? "（前100个）"
                  : " (first 100)"
                : ""}
            </span>
            <div>
              <button
                disabled={index === 0}
                aria-label={zh ? "上一个结果" : "Previous result"}
                onClick={() => setIndex((i) => i - 1)}
              >
                ←
              </button>
              <button
                disabled={index >= data.outputs.length - 1}
                aria-label={zh ? "下一个结果" : "Next result"}
                onClick={() => setIndex((i) => i + 1)}
              >
                →
              </button>
              <Button
                variant="ghost"
                size="sm"
                disabled={download.isPending}
                onClick={() => download.mutate()}
              >
                <Download size={13} />
                {zh ? "下载结果" : "Download"}
              </Button>
            </div>
          </div>
        </section>
      ) : (
        <div className="task-result-empty">
          <Clock3 />
          <span>
            {zh
              ? ACTIVE_JOBS.has(j.status)
                ? "任务仍在运行，完成后在这里查看结果。"
                : "这个任务还没有可预览的结果。"
              : "Results will appear here when available."}
          </span>
        </div>
      )}
      {result &&
      j.status === "succeeded" &&
      result.output_type !== "audio" &&
      !["upload", "local", "library"].includes(result.provider) ? (
        <section className="activity-section">
          <h4>结果与采用</h4>
          <p>结果已保存到素材库。生成与重试不会自动替换作品中已采用的画面。</p>
          <Link
            className="task-source-link"
            to={`/projects/${projectId}/materials?generation=${result.id}&return=${encodeURIComponent(materialReturn)}`}
          >
            查看素材与采用位置
            <ArrowUpRight size={14} />
          </Link>
        </section>
      ) : result ? (
        <p className="text-sm text-muted-foreground">
          结果保留在当前任务，可下载或回到创作位置继续使用。
        </p>
      ) : null}
      {!!data.changes?.length && (
        <section className="activity-section">
          <h4>采用修改记录</h4>
          <ol className="activity-history">
            {data.changes.map((change) => (
              <li key={change.id}>
                <span>
                  {change.replaced ? "替换采用画面" : "采用画面"} ·{" "}
                  {change.target.path ? (
                    <Link to={change.target.path}>{change.target.label} ↗</Link>
                  ) : (
                    change.target.label
                  )}
                </span>
                <time>{new Date(change.at).toLocaleString()}</time>
              </li>
            ))}
          </ol>
          {data.more_changes && <p>显示最近 30 条修改。</p>}
        </section>
      )}
      {!!j.routing_state?.attempts?.length && (
        <section className="rounded-xl border border-border p-4 space-y-3">
          <h4 className="text-sm font-medium">模型调用记录</h4>
          {j.routing_state.attempts.map((a, i) => (
            <div key={i} className="text-xs space-y-1">
              <strong>{a.channel_name}</strong>
              <p className="text-muted-foreground">{a.message}</p>
              {a.at && (
                <time className="text-muted-foreground">
                  {new Date(a.at).toLocaleString()}
                </time>
              )}
            </div>
          ))}
          {["dispatching", "submission_unknown"].includes(
            j.routing_state.phase || "",
          ) && (
            <p className="text-xs text-danger">
              上游是否已接单尚不明确，已阻止自动换渠道和重发，请先核对上游任务。
            </p>
          )}
        </section>
      )}
      {!!j.input_snapshot.skills?.length && (
        <section className="rounded-xl border border-border p-4 space-y-3">
          <h4 className="text-sm font-medium">本次使用的技能</h4>
          {j.input_snapshot.skills.map((s) => (
            <details key={s.id} className="text-xs">
              <summary className="cursor-pointer">
                {s.name} · 版本 {s.revision}
              </summary>
              <div className="mt-2 space-y-1 text-muted-foreground">
                {s.files.map((f) => (
                  <p key={f.path} className="break-all">
                    {f.path} · SHA256 {f.sha256}
                  </p>
                ))}
              </div>
            </details>
          ))}
        </section>
      )}
      {j.error && (
        <p role="alert" className="task-error">
          {taskError(j.error, zh)}
        </p>
      )}
      {j.cost_raw?.warnings?.map((w, i) => (
        <p key={i} className="task-warning">
          {w}
        </p>
      ))}
      {(data.can_retry || data.can_cancel) && (
        <div className="task-actions">
          {data.can_cancel && (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => onAction("cancel")}
            >
              {zh ? "取消任务" : "Cancel task"}
            </Button>
          )}
          {data.can_retry && (
            <Button disabled={busy} onClick={() => onAction("retry")}>
              <RefreshCw size={14} />
              {j.routing_state &&
              ["accepted", "generated"].includes(j.routing_state.phase || "")
                ? zh
                  ? "恢复原任务结果"
                  : "Recover original result"
                : zh
                  ? "按原始输入重试"
                  : "Retry original input"}
            </Button>
          )}
        </div>
      )}
      {(data.retry_parent || data.retries.length > 0) && (
        <section className="task-retries">
          <h4>{zh ? "重试记录" : "Retry history"}</h4>
          {data.retry_parent && (
            <button onClick={() => onSelect(data.retry_parent!.id)}>
              {zh ? "查看原任务" : "Original task"} · #
              {data.retry_parent.id.slice(0, 8)} →
            </button>
          )}
          {data.retries.map((r) => (
            <button key={r.id} onClick={() => onSelect(r.id)}>
              {zh ? "重试任务" : "Retry"} · #{r.id.slice(0, 8)}
              <span>{zh ? JOB_LABEL[r.status] : r.status} →</span>
            </button>
          ))}
          {data.more_retries && (
            <p>
              {zh
                ? "显示最近20次重试。其余记录可通过任务列表查找。"
                : "Showing the latest 20 retries."}
            </p>
          )}
        </section>
      )}
      <section className="task-events">
        <h4>{zh ? "执行过程" : "Activity"}</h4>
        {j.cost_raw?.events?.length ? (
          <ol>
            {j.cost_raw.events.map((e, i) => (
              <li key={i}>
                <i className={`status-${e.status}`} />
                <div>
                  <p>{e.message}</p>
                  <time>
                    {new Date(e.at).toLocaleString(zh ? "zh-CN" : "en-US")}
                  </time>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-xs text-muted-foreground">
            {zh
              ? "早期任务未记录执行日志。"
              : "No event log for this older task."}
          </p>
        )}
      </section>
      <details className="task-metadata">
        <summary>
          {zh ? "任务信息与原始输入" : "Task information & original input"}
        </summary>
        <dl>
          {j.error && (
            <>
              <dt>{zh ? "原始错误" : "Original error"}</dt>
              <dd>{j.error}</dd>
            </>
          )}
          <dt>{zh ? "任务编号" : "Task ID"}</dt>
          <dd>{j.id}</dd>
          <dt>{zh ? "预估 / 记录点数" : "Estimated / recorded points"}</dt>
          <dd>
            {j.estimated_cost ?? "—"} / {j.actual_cost ?? "—"}
          </dd>
          <dt>{zh ? "最后更新" : "Last updated"}</dt>
          <dd>{new Date(j.updated_at).toLocaleString()}</dd>
        </dl>
        <pre>{JSON.stringify(j.input_snapshot, null, 2)}</pre>
      </details>
    </div>
  );
}
