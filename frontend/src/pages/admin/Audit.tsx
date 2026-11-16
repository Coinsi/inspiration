import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { StudioSelect } from "@/components/ui/studio-select";
import { api } from "@/lib/api";
import {
  date,
  Empty,
  ErrorNotice,
  Heading,
  Loading,
  Notice,
  Pager,
  useFilters,
} from "./shared";
type Record = {
  user_name: string;
  id: string;
  user_id: string | null;
  action: string;
  target_name: string | null;
  target_type: string | null;
  target_id: string | null;
  created_at: string;
  reason?: string;
};
const actions: { [key: string]: string } = {
  "admin.user.disable": "禁用账号",
  "admin.user.enable": "恢复账号",
  "admin.user.revoke_sessions": "撤销登录会话",
  "admin.user.set_role": "调整账号角色",
  "admin.quota.update": "调整内部额度",
  "admin.system.check": "检查服务连接",
  "admin.website.save": "保存官网草稿",
  "admin.website.publish": "发布官网",
  "admin.website.restore": "恢复官网草稿",
  "admin.blog.create": "创建博客文章",
  "admin.blog.save": "保存博客文章",
  "admin.blog.publish": "发布博客文章",
  "admin.blog.unpublish": "撤下博客文章",
  "admin.blog.archive": "归档博客文章",
};
export default function AdminAudit() {
  const { params, set, offset } = useFilters(),
    action = params.get("action") || "";
  const q = useQuery({
    queryKey: ["admin", "audit", action, offset],
    queryFn: () =>
      api.get<{ items: Record[]; total: number }>(
        `/admin/audit?${new URLSearchParams({ action, offset: String(offset) })}`,
      ),
  });
  return (
    <>
      <Heading
        title="操作记录"
        description="追溯平台管理操作的执行者、时间和对象。"
      />
      <div className="admin-filters">
        <StudioSelect
          aria-label="操作类型"
          value={action}
          onValueChange={(v) => set("action", v)}
        >
          <option value="">全部操作</option>
          {Object.entries(actions).map(([value, label]) => (
            <option key={value} value={value}>
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
                    <th>操作</th>
                    <th>操作者</th>
                    <th>对象</th>
                    <th>时间</th>
                    <th>原因</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.items.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <strong>{actions[r.action] || r.action}</strong>
                      </td>
                      <td>
                        {r.user_id ? (
                          <Link to={`/admin/users?user=${r.user_id}`}>
                            {r.user_name}
                          </Link>
                        ) : (
                          "服务端命令"
                        )}
                      </td>
                      <td>
                        {r.target_name ||
                          {
                            user: "用户账号",
                            quota: "项目额度",
                            blog_post: "博客文章",
                          }[r.target_type || ""] ||
                          "平台配置"}
                        <details>
                          <summary className="text-muted-foreground text-xs">
                            排查信息
                          </summary>
                          <small>{r.action}</small>
                          <small>{r.target_id || r.id}</small>
                        </details>
                      </td>
                      <td>{date(r.created_at)}</td>
                      <td>{r.reason || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>暂无符合条件的操作记录</Empty>
          )}
          <Pager
            offset={offset}
            total={q.data.total}
            onPage={(n) => set("offset", String(n))}
          />
        </section>
      )}
      <Notice>
        仅展示平台级操作摘要。项目内的创作日志仍按项目权限访问；历史未记录的操作不会补造。
      </Notice>
    </>
  );
}
