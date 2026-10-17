import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import {
  JOB_LABEL,
  taskLabel,
  taskError,
  type TaskSummary,
} from "@/lib/task-center";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm";
import { useToast } from "@/components/ui/toast";

type Destination = { label: string; path: string | null };
type Action = {
  name: string;
  label: string;
  path: string;
  payload: object | null;
  note: string;
};
type Change = {
  id: string;
  label: string;
  state: string;
  target: Destination;
  before: unknown;
  after: unknown;
};
type Detail = {
  summary: TaskSummary;
  source: Destination | null;
  result_note: string;
  recovery_note: string;
  scope: Destination[];
  actions: Action[];
  changes: Change[];
  children: { id: string; status: string }[];
  progress: { completed: number; total: number } | null;
  history: { id: string; label: string; at: string }[];
  more_history: boolean;
};

function DestinationLink({ item }: { item: Destination }) {
  return item.path ? (
    <Link className="task-source-link" to={item.path}>
      {item.label}
      <ArrowUpRight size={14} />
    </Link>
  ) : (
    <span>{item.label}</span>
  );
}

function readable(value: unknown): string {
  if (value == null) return "未记录";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(readable).join("\n");
  if (typeof value === "object") {
    const state = value as Record<string, unknown>;
    if ("text" in state) return String(state.text ?? "");
    if ("fields" in state) return readable(state.fields);
    return Object.entries(state)
      .filter(([key]) => !["id", "version_id", "code"].includes(key))
      .map(
        ([key, text]) =>
          `${({ title: "标题", blocks: "正文", description: "描述", visual_description: "画面", dialogue: "对白", name: "名称" } as Record<string, string>)[key] || key}：${readable(text)}`,
      )
      .join("\n");
  }
  return String(value);
}

export function ActivityDetail({
  projectId,
  kind,
  id,
}: {
  projectId: string;
  kind: string;
  id: string;
}) {
  const qc = useQueryClient(),
    confirm = useConfirm(),
    toast = useToast();
  const query = useQuery({
    queryKey: ["activity-detail", projectId, kind, id],
    queryFn: () =>
      api.get<Detail>(`/projects/${projectId}/activity/${kind}/${id}`),
    refetchInterval: (q) =>
      q.state.data &&
      !["succeeded", "failed", "canceled"].includes(q.state.data.summary.status)
        ? 3000
        : false,
  });
  const command = useMutation({
    mutationFn: (action: Action) =>
      api.post(action.path, action.payload ?? undefined),
    onSuccess: async () => {
      toast.push("操作已受理，状态将自动更新");
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["activity-detail", projectId] }),
        qc.invalidateQueries({ queryKey: ["jobs", projectId] }),
        qc.invalidateQueries({ queryKey: ["conversations", projectId] }),
        qc.invalidateQueries({ queryKey: ["conversation", projectId] }),
      ]);
    },
    onError: (error) => {
      toast.push((error as Error).message, "error");
      void query.refetch();
    },
  });
  async function perform(action: Action) {
    if (action.note || action.name === "cancel") {
      if (
        !(await confirm({
          title: action.label,
          message: action.note || "停止后保留已完成的内容，等待当前步骤结束。",
        }))
      )
        return;
    }
    command.mutate(action);
  }
  if (query.isError)
    return (
      <div className="task-error" role="alert">
        {(query.error as Error).message}
        <button onClick={() => void query.refetch()}>重新读取详情</button>
      </div>
    );
  if (!query.data) return <div className="task-empty">正在读取任务…</div>;
  const data = query.data,
    s = data.summary;
  return (
    <div className="task-detail-body">
      <div className="task-detail-title">
        <span className={`task-status status-${s.status}`}>
          {JOB_LABEL[s.status] || s.status}
        </span>
        <h3>{taskLabel(s.operation, s.request_type, true)}</h3>
        <p>{s.target_name}</p>
      </div>
      {data.source && <DestinationLink item={data.source} />}
      {data.progress && data.progress.total > 0 && (
        <div className="activity-progress">
          <label>
            已完成 {data.progress.completed} / {data.progress.total}
          </label>
          <progress value={data.progress.completed} max={data.progress.total} />
        </div>
      )}
      <section className="activity-section">
        <h4>结果保存在哪里</h4>
        <p className="whitespace-pre-wrap">{data.result_note}</p>
      </section>
      <section className="activity-section">
        <h4>影响范围</h4>
        {data.scope.length ? (
          data.scope.map((item, i) => <DestinationLink key={i} item={item} />)
        ) : (
          <p>未指定可修改对象，当前任务不会直接修改作品正文。</p>
        )}
      </section>
      {s.error && (
        <p className="task-error" role="alert">
          {taskError(s.error)}
        </p>
      )}
      <section className="activity-section">
        <h4>如何继续</h4>
        <p>{data.recovery_note}</p>
        <div className="task-actions">
          {data.actions.map((action) => (
            <Button
              key={action.name}
              variant={action.name === "cancel" ? "outline" : "default"}
              disabled={command.isPending}
              onClick={() => void perform(action)}
            >
              {action.name === "retry" && <RefreshCw size={14} />}
              {action.label}
            </Button>
          ))}
        </div>
      </section>
      {!!data.changes.length && (
        <section className="activity-section">
          <h4>内容修改</h4>
          {data.changes.map((change) => (
            <article className="activity-change" key={change.id}>
              <span
                className={`task-status status-${change.state === "applied" ? "succeeded" : "paused"}`}
              >
                {{ applied: "已应用", rejected: "已拒绝", proposed: "未应用" }[
                  change.state
                ] || change.state}
              </span>
              <p>{change.label}</p>
              <DestinationLink item={change.target} />
              <details>
                <summary>查看修改前后</summary>
                <div className="activity-diff">
                  <section>
                    <h5>修改前</h5>
                    <pre>{readable(change.before)}</pre>
                  </section>
                  <section>
                    <h5>
                      {change.state === "applied" ? "应用内容" : "提议内容"}
                    </h5>
                    <pre>{readable(change.after)}</pre>
                  </section>
                </div>
              </details>
            </article>
          ))}
        </section>
      )}
      {!!data.children.length && (
        <section className="activity-section">
          <h4>关联的生成任务与结果</h4>
          {data.children.map((child) => (
            <Link
              key={child.id}
              className="task-source-link"
              to={`/projects/${projectId}/tasks?job=${child.id}`}
            >
              #{child.id.slice(0, 8)} ·{" "}
              {JOB_LABEL[child.status] || child.status}
              <ArrowUpRight size={14} />
            </Link>
          ))}
        </section>
      )}
      <section className="activity-section">
        <h4>操作记录</h4>
        {data.history.length ? (
          <ol className="activity-history">
            {data.history.map((item) => (
              <li key={item.id}>
                <span>{item.label}</span>
                <time>{new Date(item.at).toLocaleString()}</time>
              </li>
            ))}
          </ol>
        ) : (
          <p>暂无已记录的操作。</p>
        )}
        {data.more_history && <p>显示最近 30 条操作。</p>}
      </section>
      <details className="task-metadata">
        <summary>任务信息</summary>
        <dl>
          <dt>任务编号</dt>
          <dd>{s.id}</dd>
          <dt>最后更新</dt>
          <dd>{new Date(s.updated_at).toLocaleString()}</dd>
        </dl>
      </details>
    </div>
  );
}
