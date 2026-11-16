import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
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
};
export default function AdminQuotas() {
  const { me } = useAuth();
  const { params, set, offset } = useFilters();
  const [selected, select] = useState<Quota | null>(null);
  const user = params.get("user_id") || "";
  const q = useQuery({
    queryKey: ["admin", "quotas", user, offset],
    queryFn: () =>
      api.get<{ items: Quota[]; total: number }>(
        `/admin/quotas?offset=${offset}${user ? `&user_id=${encodeURIComponent(user)}` : ""}`,
      ),
  });
  return (
    <>
      <Heading
        title="用量与额度"
        description="集中查看现有项目内部额度。平台实际用量摘要见概览，用户用量见用户详情。"
      />
      <Notice>
        这里沿用项目内部计量单位，额度调整不代表充值或供应商余额。项目管理员仍可管理自己项目的内部额度；此处尚不是独立的商业计费系统。
      </Notice>
      {user && (
        <div className="admin-filter-note">
          已按用户筛选
          <Button variant="ghost" onClick={() => set("user_id", "")}>
            查看全部额度
          </Button>
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
                    <th>所属项目</th>
                    <th>范围</th>
                    <th>额度</th>
                    <th>已使用</th>
                    <th>周期</th>
                    <th>
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.items.map((v) => (
                    <tr key={v.id}>
                      <td title={v.project_id}>{v.project_name}</td>
                      <td>{v.user_id ? v.user_name : "整个项目"}</td>
                      <td>{amount(v.limit)}</td>
                      <td>{amount(v.used)}</td>
                      <td>{v.period === "total" ? "累计" : v.period}</td>
                      <td>
                        {me?.user.platform_role !== "admin" && (
                          <Button variant="ghost" onClick={() => select(v)}>
                            调整额度
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>没有已建立的额度记录。可在项目设置中建立项目额度。</Empty>
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
        title="调整内部额度"
        width={520}
      >
        {selected && <EditQuota quota={selected} close={() => select(null)} />}
      </Modal>
    </>
  );
}
function EditQuota({ quota, close }: { quota: Quota; close: () => void }) {
  const [limit, setLimit] = useState(quota.limit),
    [reason, setReason] = useState("");
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () =>
      api.put(`/admin/quotas/${quota.id}`, {
        expected_version: quota.version,
        limit,
        reason,
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["admin"] });
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
        项目 {quota.project_name} · 已使用 {amount(quota.used)}
      </p>
      <label className="admin-field">
        新额度
        <Input
          required
          type="number"
          min={Number(quota.used)}
          step="0.01"
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
        />
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
      <Notice>如提示额度已被修改，请关闭并刷新列表后再调整。</Notice>
      <div className="admin-inline-actions">
        <Button type="button" variant="outline" onClick={close}>
          取消
        </Button>
        <Button disabled={m.isPending || !reason.trim() || !limit}>
          {m.isPending ? "正在保存…" : "保存额度"}
        </Button>
      </div>
    </form>
  );
}
