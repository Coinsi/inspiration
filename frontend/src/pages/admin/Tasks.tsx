import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { StudioSelect } from "@/components/ui/studio-select";
import {
  Badge,
  bytes,
  date,
  Empty,
  ErrorNotice,
  Heading,
  kinds,
  Loading,
  Notice,
  Pager,
  SearchBox,
  useFilters,
} from "./shared";
type Task = {
  title: string;
  project_name: string;
  user_name: string;
  id: string;
  kind: string;
  status: string;
  created_at: string;
  updated_at: string;
  project_id: string;
  user_id: string | null;
};
type Detail = Task & {
  summary: string | null;
  work_name: string | null;
  diagnosis: { message: string; http_status: string | null };
  has_upstream_receipt: boolean;
  transfer?: { uploaded_bytes: number; size_bytes: number };
  progress?: { completed: number; total: number };
  project_url: string | null;
};
export default function AdminTasks() {
  const { params, set, offset } = useFilters();
  const kind = params.get("kind") || "",
    state = params.get("state") || "",
    search = params.get("search") || "",
    user = params.get("user_id") || "",
    project = params.get("project_id") || "",
    detail = params.get("detail") || "";
  const query = new URLSearchParams({ state, search, offset: String(offset) });
  if (kind) query.set("kind", kind);
  if (user) query.set("user_id", user);
  if (project) query.set("project_id", project);
  const q = useQuery({
    queryKey: ["admin", "tasks", query.toString()],
    queryFn: () =>
      api.get<{ items: Task[]; total: number }>(`/admin/tasks?${query}`),
  });
  return (
    <>
      <Heading
        title="任务中心"
        description="集中查看生成、制作、上传和处理任务，定位异常发生在哪一步。"
      >
        <Button
          variant="outline"
          disabled={q.isFetching}
          onClick={() => void q.refetch()}
        >
          刷新
        </Button>
      </Heading>
      <div className="admin-filters">
        <SearchBox
          key={search}
          initial={search}
          placeholder="搜索项目名称、用户昵称或任务编号"
          onSearch={(v) => set("search", v)}
        />
        <StudioSelect
          aria-label="任务类型"
          value={kind}
          onValueChange={(v) => set("kind", v)}
        >
          <option value="">全部任务类型</option>
          {Object.entries(kinds).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </StudioSelect>
        <StudioSelect
          aria-label="任务状态"
          value={state}
          onValueChange={(v) => set("state", v)}
        >
          <option value="">全部状态</option>
          <option value="active">进行中</option>
          <option value="attention">需要关注</option>
          <option value="paused">已暂停</option>
          <option value="succeeded">执行成功</option>
          <option value="ready">处理就绪</option>
        </StudioSelect>
      </div>
      {(user || project) && (
        <div className="admin-filter-note">
          {user && (
            <span>
              已按发起用户筛选{" "}
              <Button variant="ghost" onClick={() => set("user_id", "")}>
                清除用户筛选
              </Button>
            </span>
          )}
          {project && (
            <span>
              已按所属项目筛选{" "}
              <Button variant="ghost" onClick={() => set("project_id", "")}>
                清除项目筛选
              </Button>
            </span>
          )}
        </div>
      )}
      {q.error ? (
        <ErrorNotice error={q.error} retry={() => void q.refetch()} />
      ) : !q.data ? (
        <Loading />
      ) : (
        <section className="admin-panel">
          {q.data.items.length ? (
            <div className="admin-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>任务</th>
                    <th>状态</th>
                    <th>所属项目</th>
                    <th>发起用户</th>
                    <th>最近更新</th>
                    <th>
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.items.map((t) => (
                    <tr key={`${t.kind}:${t.id}`}>
                      <td>
                        <strong>{t.title}</strong>
                        <small>{date(t.created_at)}</small>
                      </td>
                      <td>
                        <Badge value={t.status} />
                      </td>
                      <td>
                        <span title={t.project_id}>{t.project_name}</span>
                      </td>
                      <td>
                        {t.user_id ? (
                          <Link
                            className="admin-text-link"
                            to={`/admin/users?user=${t.user_id}`}
                          >
                            {t.user_name}
                          </Link>
                        ) : (
                          "未记录"
                        )}
                      </td>
                      <td>{date(t.updated_at)}</td>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => set("detail", `${t.kind}:${t.id}`)}
                        >
                          查看详情
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty />
          )}
          <Pager
            offset={offset}
            total={q.data.total}
            onPage={(n) => set("offset", String(n))}
          />
        </section>
      )}
      <Notice>
        这里只展示运行元数据。任务恢复沿用原业务入口的权限与规则，不会重新提交状态不明的付费请求。部分历史上传未记录发起人，按用户筛选时不包含这些记录。
      </Notice>
      <Modal
        open={!!detail}
        onClose={() => set("detail", "")}
        title="任务详情"
        width={720}
      >
        {detail && (
          <TaskDetail
            identity={detail.includes(":") ? detail : `${kind}:${detail}`}
          />
        )}
      </Modal>
    </>
  );
}
function TaskDetail({ identity }: { identity: string }) {
  const [kind, id] = identity.split(":");
  const q = useQuery({
    queryKey: ["admin", "task", identity],
    queryFn: () => api.get<Detail>(`/admin/tasks/${kind}/${id}`),
    enabled: !!kind && !!id,
  });
  if (!kind || !id)
    return <Empty>任务链接不完整，请关闭后从列表重新选择。</Empty>;
  if (q.error)
    return <ErrorNotice error={q.error} retry={() => void q.refetch()} />;
  if (!q.data) return <Loading />;
  const t = q.data;
  return (
    <div className="admin-detail">
      <div className="admin-section-title">
        <h2>{t.title}</h2>
        <Badge value={t.status} />
      </div>
      {t.summary && <p className="admin-diagnosis">{t.summary}</p>}
      <dl className="admin-facts">
        <dt>所属项目</dt>
        <dd>{t.project_name}</dd>
        {t.work_name && (
          <>
            <dt>关联作品</dt>
            <dd>{t.work_name}</dd>
          </>
        )}
        <dt>发起用户</dt>
        <dd>
          {t.user_id ? (
            <Link to={`/admin/users?user=${t.user_id}`}>{t.user_name}</Link>
          ) : (
            "未记录"
          )}
        </dd>
        <dt>创建时间</dt>
        <dd>{date(t.created_at)}</dd>
        <dt>最近更新</dt>
        <dd>{date(t.updated_at)}</dd>
        {t.kind === "generation" && (
          <>
            <dt>上游任务回执</dt>
            <dd>
              {t.has_upstream_receipt ? "已记录" : "未记录；不代表上游没有受理"}
            </dd>
          </>
        )}
        {t.transfer && (
          <>
            <dt>上传进度</dt>
            <dd>
              {bytes(t.transfer.uploaded_bytes)} /{" "}
              {bytes(t.transfer.size_bytes)}
            </dd>
          </>
        )}
        {t.progress && (
          <>
            <dt>处理进度</dt>
            <dd>
              {t.progress.completed} / {t.progress.total}
            </dd>
          </>
        )}
      </dl>
      <details className="admin-note">
        <summary>排查信息与编号</summary>
        <dl className="admin-facts">
          <dt>任务 ID</dt>
          <dd>{t.id}</dd>
          <dt>项目 ID</dt>
          <dd>{t.project_id}</dd>
          <dt>用户 ID</dt>
          <dd>{t.user_id || "未记录"}</dd>
        </dl>
      </details>
      <section className="admin-diagnosis">
        <h3>
          诊断提示
          {t.diagnosis.http_status && ` · HTTP ${t.diagnosis.http_status}`}
        </h3>
        <p>{t.diagnosis.message}</p>
      </section>
      {t.project_url ? (
        <Link className="admin-text-link" to={t.project_url}>
          进入项目处理 →
        </Link>
      ) : (
        <Notice>
          你不是该项目成员，管理身份不会开放私人内容。可使用任务 ID
          继续排查服务日志。
        </Notice>
      )}
    </div>
  );
}
