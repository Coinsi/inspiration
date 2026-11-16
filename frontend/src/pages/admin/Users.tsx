import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { StudioSelect } from "@/components/ui/studio-select";
import { useConfirm } from "@/components/ui/confirm";
import {
  amount,
  Badge,
  date,
  Empty,
  ErrorNotice,
  Heading,
  Loading,
  Notice,
  Pager,
  roles,
  SearchBox,
  Stats,
  useFilters,
} from "./shared";

type UserRow = {
  id: string;
  username: string;
  display_name: string;
  email: string;
  is_active: boolean;
  role: string;
  revision: number;
  created_at: string;
  last_login_at: string | null;
};
type UserDetail = UserRow & {
  login_methods: string[];
  owned_projects: number;
  memberships: number;
  works_created: number;
  generation_usage: {
    jobs: number;
    estimated: number;
    recorded_actual: number;
    unknown_jobs: number;
  };
};

export default function AdminUsers() {
  const { params, set, offset } = useFilters();
  const search = params.get("search") || "",
    status = params.get("status") || "all",
    selected = params.get("user") || "",
    accountRole = params.get("role") || "all";
  const q = useQuery({
    queryKey: ["admin", "users", search, status, accountRole, offset],
    queryFn: () =>
      api.get<{ items: UserRow[]; total: number }>(
        `/admin/users?${new URLSearchParams({ search, status, account_role: accountRole, offset: String(offset) })}`,
      ),
  });
  return (
    <>
      <Heading
        title="用户管理"
        description="查看注册账号、使用情况和相关任务。账号权限由超级管理员调整。"
      />
      <div className="admin-filters">
        <SearchBox
          key={search}
          initial={search}
          placeholder="搜索昵称、用户名或邮箱"
          onSearch={(v) => set("search", v)}
        />
        <StudioSelect
          aria-label="账号状态"
          value={status}
          onValueChange={(v) => set("status", v)}
        >
          <option value="all">全部状态</option>
          <option value="active">正常</option>
          <option value="disabled">已禁用</option>
        </StudioSelect>
        <StudioSelect
          aria-label="账号角色"
          value={accountRole}
          onValueChange={(v) => set("role", v)}
        >
          <option value="all">全部角色</option>
          {Object.entries(roles).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </StudioSelect>
      </div>
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
                    <th>用户</th>
                    <th>角色</th>
                    <th>状态</th>
                    <th>注册时间</th>
                    <th>最近登录</th>
                    <th>
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.items.map((u) => (
                    <tr key={u.id}>
                      <td>
                        <button
                          className="admin-text-link"
                          onClick={() => set("user", u.id)}
                        >
                          {u.display_name}
                        </button>
                        <small>{u.email}</small>
                      </td>
                      <td>{roles[u.role]}</td>
                      <td>
                        <Badge value={u.is_active ? "active" : "disabled"} />
                      </td>
                      <td>{date(u.created_at)}</td>
                      <td>{date(u.last_login_at)}</td>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => set("user", u.id)}
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
      <Modal
        open={!!selected}
        onClose={() => set("user", "")}
        title="用户详情"
        width={820}
      >
        {selected && <UserInfo id={selected} />}
      </Modal>
    </>
  );
}

function UserInfo({ id }: { id: string }) {
  const [notice, setNotice] = useState("");
  const q = useQuery({
    queryKey: ["admin", "user", id],
    queryFn: () => api.get<UserDetail>(`/admin/users/${id}`),
  });
  if (q.error)
    return <ErrorNotice error={q.error} retry={() => void q.refetch()} />;
  if (!q.data) return <Loading />;
  const u = q.data;
  return (
    <div className="admin-detail admin-user-detail">
      {notice && (
        <p role="status" className="admin-success">
          {notice}
        </p>
      )}
      <div className="admin-section-title">
        <div>
          <h2>{u.display_name}</h2>
          <p>{u.email}</p>
        </div>
        <Badge value={u.is_active ? "active" : "disabled"} />
      </div>
      <dl className="admin-facts">
        <dt>账号 ID</dt>
        <dd>{u.id}</dd>
        <dt>用户名</dt>
        <dd>{u.username}</dd>
        <dt>当前角色</dt>
        <dd>{roles[u.role]}</dd>
        <dt>登录方式</dt>
        <dd>
          {u.login_methods
            .map((v) => (v === "password" ? "邮箱密码" : v))
            .join("、") || "未记录"}
        </dd>
        <dt>注册时间</dt>
        <dd>{date(u.created_at)}</dd>
        <dt>最近登录</dt>
        <dd>{date(u.last_login_at)}</dd>
      </dl>
      <Stats
        items={[
          { label: "拥有的项目", value: u.owned_projects },
          { label: "参与的项目", value: u.memberships },
          { label: "创建的作品", value: u.works_created },
        ]}
      />
      <p>
        生成任务 {u.generation_usage.jobs} 次 · 已记录实际用量{" "}
        {amount(u.generation_usage.recorded_actual)} · 尚未记录{" "}
        {u.generation_usage.unknown_jobs} 次
      </p>
      <div className="admin-inline-actions">
        <Link to={`/admin/tasks?user_id=${u.id}`}>查看相关任务 →</Link>
        <Link to={`/admin/quotas?user_id=${u.id}`}>查看个人项目额度 →</Link>
      </div>
      <Notice>
        此处展示账号与使用信息，不开放私人对话、素材及作品内容。历史登录时间不会推测补填。
      </Notice>
      <AccountActions
        key={`${id}:${u.revision}`}
        user={u}
        onApplied={() => setNotice("账号操作已完成，相关登录会话已失效。")}
      />
    </div>
  );
}

function AccountActions({
  user,
  onApplied,
}: {
  user: UserRow;
  onApplied: () => void;
}) {
  const { me, refresh } = useAuth();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [action, setAction] = useState("revoke_sessions"),
    [nextRole, setNextRole] = useState(user.role),
    [reason, setReason] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      api.post(`/admin/users/${user.id}/actions`, {
        expected_revision: user.revision,
        action,
        role: action === "set_role" ? nextRole : null,
        reason,
      }),
    onSuccess: async () => {
      onApplied();
      await qc.invalidateQueries({ queryKey: ["admin"] });
      if (user.id === me?.user.id) await refresh();
    },
  });
  if (me?.user.platform_role === "admin")
    return (
      <Notice>
        你可以查看用户和任务。账号启停、授权及会话撤销需要超级管理员。
      </Notice>
    );
  const submit = async () => {
    if (
      !(await confirm({
        title: "确认修改账号？",
        message: `${user.display_name} 的现有登录会话将失效。${user.id === me?.user.id ? "这也会退出你当前的登录。" : ""}`,
        confirmText: "确认执行",
        danger: action === "disable",
      }))
    )
      return;
    mutation.mutate();
  };
  return (
    <section className="admin-account-actions">
      <h3>账号操作</h3>
      <div className="admin-form-grid">
        <label>
          操作
          <StudioSelect
            aria-label="账号操作"
            value={action}
            onValueChange={setAction}
          >
            <option value="revoke_sessions">退出所有登录会话</option>
            <option value={user.is_active ? "disable" : "enable"}>
              {user.is_active ? "禁用账号" : "恢复账号"}
            </option>
            <option value="set_role">调整角色</option>
          </StudioSelect>
        </label>
        {action === "set_role" && (
          <label>
            新角色
            <StudioSelect
              aria-label="目标角色"
              value={nextRole}
              onValueChange={setNextRole}
            >
              {Object.entries(roles).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </StudioSelect>
          </label>
        )}
        <label className="admin-wide">
          操作原因
          <Input
            value={reason}
            maxLength={300}
            onChange={(e) => setReason(e.target.value)}
            placeholder="简要说明原因，记录到操作日志"
          />
        </label>
      </div>
      {mutation.error && (
        <ErrorNotice
          error={mutation.error}
          retry={() => {
            mutation.reset();
            void qc.invalidateQueries({ queryKey: ["admin", "user", user.id] });
          }}
        />
      )}
      <Button
        disabled={
          mutation.isPending ||
          !reason.trim() ||
          (action === "set_role" && nextRole === user.role)
        }
        onClick={() => void submit()}
      >
        {mutation.isPending ? "正在保存…" : "执行操作"}
      </Button>
      <Notice>
        必须保留至少一位有效超级管理员。禁用不会删除该用户的历史作品或已提交任务。
      </Notice>
    </section>
  );
}
