import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { StudioSelect } from "@/components/ui/studio-select";
import { AdminCredits } from "./CreditBalances";
import "../billing.css";
import {
  amount,
  Empty,
  ErrorNotice,
  Heading,
  Loading,
  Notice,
  Pager,
  useFilters,
} from "./shared";
type Quota = {
  project_name: string;
  user_name: string;
  id: string;
  project_id: string;
  user_id: string | null;
  scope: string;
  limit: string;
  used: string;
  period: string;
  version: string;
  blocked: boolean;
};
export default function AdminQuotas() {
  const { me } = useAuth();
  const { params, set } = useFilters();
  const isSuper = !!me?.user.is_platform_admin && me.user.platform_role !== "admin";
  const credits = isSuper && params.get("section") === "credits";
  return (
    <>
      <Heading
        title="用量与积分"
        description="在这里设置项目和成员的用量上限，发放或收回试用与赠送积分。"
      />
      {isSuper && (
        <nav className="billing-tabs" aria-label="用量与积分分区">
          <button aria-current={credits ? undefined : "page"} onClick={() => set("section", "")}>
            项目与成员用量
          </button>
          <button aria-current={credits ? "page" : undefined} onClick={() => set("section", "credits")}>
            个人积分与试用
          </button>
        </nav>
      )}
      {credits ? <AdminCredits /> : <QuotasPanel isSuper={isSuper} />}
    </>
  );
}

function QuotasPanel({ isSuper }: { isSuper: boolean }) {
  const { params, set, offset } = useFilters();
  const [selected, select] = useState<Quota | null>(null);
  const [creating, setCreating] = useState(false);
  const user = params.get("user_id") || "";
  const project = params.get("project_id") || "";
  const q = useQuery({
    queryKey: ["admin", "quotas", user, project, offset],
    queryFn: () => {
      const filters = new URLSearchParams({ offset: String(offset) });
      if (user) filters.set("user_id", user);
      if (project) filters.set("project_id", project);
      return api.get<{ items: Quota[]; total: number }>(
        `/admin/quotas?${filters}`,
      );
    },
  });
  return (
    <>
      <div className="admin-filter-note justify-between">
        <span>上限用于控制新任务的用量。设为 0 表示不限，暂停后可停止新的生成任务。</span>
        {isSuper && <Button onClick={() => setCreating(true)}>分配用量上限</Button>}
      </div>
      {(user || project) && (
        <div className="admin-filter-note">
          {user && <Button variant="ghost" onClick={() => set("user_id", "")}>清除用户筛选</Button>}
          {project && <Button variant="ghost" onClick={() => set("project_id", "")}>清除项目筛选</Button>}
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
              <table className="admin-resource-table" role="table" aria-label="项目与成员用量上限">
                <thead>
                  <tr>
                    <th>所属项目</th>
                    <th>范围</th>
                    <th>用量上限</th>
                    <th>已使用</th>
                    <th>周期</th>
                    {isSuper && <th><span className="sr-only">操作</span></th>}
                  </tr>
                </thead>
                <tbody>
                  {q.data.items.map((v) => (
                    <tr key={v.id}>
                      <td title={v.project_id}>{v.project_name}</td>
                      <td><span className="admin-resource-label">范围</span>{v.user_id ? v.user_name : "整个项目"}</td>
                      <td><span className="admin-resource-label">用量上限</span>{v.blocked ? "已暂停" : Number(v.limit) ? amount(v.limit) : "不限"}</td>
                      <td><span className="admin-resource-label">已使用</span>{amount(v.used)}</td>
                      <td><span className="admin-resource-label">周期</span>{v.period === "total" ? "累计" : v.period}</td>
                      {isSuper && (
                        <td className="admin-resource-actions">
                          <Button variant="outline" onClick={() => select(v)}>
                            调整上限
                          </Button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>当前没有匹配的用量上限。</Empty>
          )}
          <Pager
            total={q.data.total}
            offset={offset}
            onPage={(n) => set("offset", String(n))}
          />
        </section>
      )}
      <Modal
        open={!!selected}
        onClose={() => select(null)}
        title="调整用量上限"
        width={520}
      >
        {selected && <EditQuota quota={selected} close={() => select(null)} />}
      </Modal>
      <Modal open={creating} onClose={() => setCreating(false)} title="分配用量上限" width={540}>
        {creating && <CreateQuota close={() => setCreating(false)} />}
      </Modal>
    </>
  );
}
function EditQuota({ quota, close }: { quota: Quota; close: () => void }) {
  const [limit, setLimit] = useState(quota.limit),
    [reason, setReason] = useState(""),
    [blocked, setBlocked] = useState(quota.blocked);
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () =>
      api.put(`/admin/quotas/${quota.id}`, {
        expected_version: quota.version,
        limit,
        reason,
        blocked,
      }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["admin", "quotas"] }),
        qc.invalidateQueries({ queryKey: ["quota", quota.project_id] }),
      ]);
      close();
    },
  });
  return (
    <form
      className="admin-detail"
      onSubmit={(e) => {
        e.preventDefault();
        m.mutate();
      }}
    >
      <p>
        {quota.project_name} · {quota.user_id ? quota.user_name : "整个项目"} · 已使用 {amount(quota.used)}
      </p>
      <label className="admin-field">
        用量上限
        <Input
          required
          type="number"
          min={0}
          step="0.01"
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
        />
      </label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={blocked} onChange={(e) => setBlocked(e.target.checked)} />
        暂停发起新的生成任务
      </label>
      <label className="admin-field">
        调整原因
        <Input
          required
          maxLength={300}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {m.error && <ErrorNotice error={m.error} />}
      <Notice>0 表示不限。降低上限或暂停会影响之后提交的任务。</Notice>
      <div className="admin-inline-actions">
        <Button type="button" variant="outline" onClick={close}>
          取消
        </Button>
        <Button disabled={m.isPending || !reason.trim() || !limit}>
          {m.isPending ? "正在保存…" : "保存上限"}
        </Button>
      </div>
    </form>
  );
}

function CreateQuota({ close }: { close: () => void }) {
  const qc = useQueryClient();
  const [projectSearch, setProjectSearch] = useState("");
  const [projectId, setProjectId] = useState("");
  const [memberSearch, setMemberSearch] = useState("");
  const [userId, setUserId] = useState("");
  const [limit, setLimit] = useState("");
  const [reason, setReason] = useState("");
  const projects = useQuery({
    queryKey: ["admin", "quota-projects", projectSearch],
    queryFn: ({ signal }) => api.get<{ id: string; name: string; code: string }[]>(
      `/admin/quotas/projects?search=${encodeURIComponent(projectSearch)}`, { signal },
    ),
  });
  const members = useQuery({
    queryKey: ["admin", "quota-members", projectId, memberSearch],
    queryFn: ({ signal }) => api.get<{ id: string; display_name: string; email: string | null }[]>(
      `/admin/quotas/projects/${projectId}/members?search=${encodeURIComponent(memberSearch)}`, { signal },
    ),
    enabled: !!projectId,
  });
  const save = useMutation({
    mutationFn: () => api.post("/admin/quotas", { project_id: projectId, user_id: userId || null, limit, reason }),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["admin", "quotas"] }),
        qc.invalidateQueries({ queryKey: ["quota", projectId] }),
      ]);
      close();
    },
  });
  return (
    <form className="admin-detail" onSubmit={(e) => {
      e.preventDefault();
      if (projectId && limit && reason.trim() && !save.isPending) save.mutate();
    }}>
      <label className="admin-field">搜索项目 <Input value={projectSearch} maxLength={120} onChange={(e) => {
        setProjectSearch(e.target.value); setProjectId(""); setUserId(""); setMemberSearch("");
      }} placeholder="项目名称或编号" /></label>
      <label className="admin-field">项目
        <StudioSelect aria-label="项目" value={projectId} onValueChange={(value) => {
          setProjectId(value); setUserId(""); setMemberSearch("");
        }}>
          <option value="">{projects.isPending ? "正在加载项目…" : "选择项目"}</option>
          {projects.data?.map((project) => <option key={project.id} value={project.id}>{project.name} · {project.code}</option>)}
        </StudioSelect>
      </label>
      {projects.error && <ErrorNotice error={projects.error} retry={() => void projects.refetch()} />}
      {projectId && (
        <>
          <label className="admin-field">搜索成员 <Input value={memberSearch} maxLength={254} onChange={(e) => {
            setMemberSearch(e.target.value); setUserId("");
          }} placeholder="邮箱或姓名" /></label>
          <label className="admin-field">分配范围
            <StudioSelect aria-label="分配范围" value={userId} onValueChange={setUserId}>
              <option value="">整个项目</option>
              {members.data?.map((user) => <option key={user.id} value={user.id}>{user.display_name}{user.email && ` · ${user.email}`}</option>)}
            </StudioSelect>
          </label>
          {members.error && <ErrorNotice error={members.error} retry={() => void members.refetch()} />}
        </>
      )}
      <label className="admin-field">用量上限 <Input required type="number" min={0} step="0.01" value={limit} onChange={(e) => setLimit(e.target.value)} /></label>
      <label className="admin-field">分配原因 <Input required maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      {save.error && <ErrorNotice error={save.error} />}
      <div className="admin-inline-actions">
        <Button type="button" variant="outline" onClick={close}>取消</Button>
        <Button disabled={save.isPending || !projectId || !limit || !reason.trim()}>{save.isPending ? "正在保存…" : "分配上限"}</Button>
      </div>
    </form>
  );
}
