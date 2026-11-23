import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { parseMinor, points } from "@/lib/billing";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { date, Empty, ErrorNotice, Loading, Notice } from "./shared";

type Operation = "grant" | "revoke";
type Balance = {
  user_id: string;
  username: string;
  email: string | null;
  display_name: string;
  is_active: boolean;
  available: number;
  reserved: number;
  grant_available: number;
};
type Adjustment = {
  id: string;
  operation: Operation;
  units: number;
  reason: string;
  created_at: string;
};
type CreditSettings = { welcome_points: string };
const pageSize = 25;
const actions = { grant: "发放积分", revoke: "收回积分" };

export function AdminCredits() {
  const [input, setInput] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Balance>();
  const users = useQuery({
    queryKey: ["admin", "credits", "list", search, offset],
    queryFn: ({ signal }) => {
      const filter = new URLSearchParams({ search, offset: String(offset), limit: String(pageSize) });
      return api.get<Balance[]>(`/admin/billing/credits?${filter}`, { signal });
    },
  });
  const settings = useQuery({
    queryKey: ["admin", "credit-settings"],
    queryFn: ({ signal }) => api.get<CreditSettings>("/admin/billing/credit-settings", { signal }),
  });
  return (
    <div className="grid gap-4">
      {settings.isPending && <Loading />}
      {settings.error && <ErrorNotice error={settings.error} retry={() => void settings.refetch()} />}
      {settings.data && <WelcomeCredits initial={settings.data.welcome_points} />}
      <section className="admin-panel">
        <form className="billing-toolbar" onSubmit={(e) => {
          e.preventDefault(); setOffset(0); setSearch(input.trim());
        }}>
          <Input aria-label="搜索积分账号" placeholder="输入完整邮箱或用户名" value={input} maxLength={254} onChange={(e) => setInput(e.target.value)} />
          <Button variant="outline">查找用户</Button>
        </form>
        {users.isPending && <Loading />}
        {users.error && <ErrorNotice error={users.error} retry={() => void users.refetch()} />}
        {users.data && (users.data.length ? (
          <div className="admin-table-scroll">
            <table className="admin-resource-table" role="table" aria-label="个人积分余额">
              <thead><tr><th>用户</th><th>可用积分</th><th>已预留</th><th>可收回赠送积分</th><th><span className="sr-only">操作</span></th></tr></thead>
              <tbody>{users.data.map((user) => (
                <tr key={user.user_id}>
                  <td><div>{user.display_name || user.username}</div><small>{user.email || user.username}</small></td>
                  <td><span className="admin-resource-label">可用积分</span>{points(user.available)}</td>
                  <td><span className="admin-resource-label">已预留</span>{points(user.reserved)}</td>
                  <td><span className="admin-resource-label">可收回赠送积分</span>{points(user.grant_available)}</td>
                  <td className="admin-resource-actions"><Button variant="outline" onClick={() => setSelected(user)}>管理积分</Button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <Empty>没有找到匹配的账号。</Empty>)}
        {users.data && (offset > 0 || users.data.length === pageSize) && (
          <div className="billing-pagination">
            <Button variant="outline" disabled={offset === 0} onClick={() => setOffset(offset - pageSize)}>上一页</Button>
            <Button variant="outline" disabled={users.data.length < pageSize} onClick={() => setOffset(offset + pageSize)}>下一页</Button>
          </div>
        )}
      </section>
      <Modal open={!!selected} onClose={() => setSelected(undefined)} title="管理个人积分" width={580}>
        {selected && <AccountCredits account={selected} />}
      </Modal>
    </div>
  );
}

function WelcomeCredits({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial);
  const [baseline, setBaseline] = useState(initial);
  const [saved, setSaved] = useState(false);
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (welcome_points: string) => api.patch<CreditSettings>("/admin/billing/credit-settings", { welcome_points }),
    onSuccess: (settings) => {
      setValue(settings.welcome_points);
      setBaseline(settings.welcome_points);
      setSaved(true);
      qc.setQueryData(["admin", "credit-settings"], settings);
    },
  });
  const units = parseMinor(value);
  const canSave = units != null && units >= 0 && value !== baseline && !save.isPending;
  return (
    <section className="admin-panel p-5">
      <form className="admin-detail" onSubmit={(e) => { e.preventDefault(); if (canSave) save.mutate(value); }}>
        <h2 className="font-medium">新用户试用积分</h2>
        <Notice>新账号注册时自动领取一次。填 0 暂停发放，已有用户可在下方单独发放。</Notice>
        <label className="admin-field">每个新账号发放的积分
          <Input className="max-w-48" disabled={save.isPending} type="number" min={0} step="0.01" value={value} required onChange={(e) => { setValue(e.target.value); setSaved(false); }} />
        </label>
        {save.error && <ErrorNotice error={save.error} />}
        <div className="admin-inline-actions">
          {saved && <span role="status">试用积分已保存</span>}
          <Button disabled={!canSave}>{save.isPending ? "保存中…" : "保存试用积分"}</Button>
        </div>
      </form>
    </section>
  );
}

function AccountCredits({ account }: { account: Balance }) {
  const [operation, setOperation] = useState<Operation>(account.is_active ? "grant" : "revoke");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const [saved, setSaved] = useState("");
  const qc = useQueryClient();
  const path = `/admin/billing/credits/${encodeURIComponent(account.user_id)}`;
  const detailKey = ["admin", "credits", "account", account.user_id];
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: ({ signal }) => api.get<Balance & { adjustments: Adjustment[] }>(path, { signal }),
  });
  const balance = detail.data ?? account;
  const save = useMutation({
    mutationFn: () => api.post<Balance & { adjustment: Adjustment }>(`${path}/adjustments`, {
      idempotency_key: requestKey, operation, points: value, reason: reason.trim(),
    }),
    onSuccess: async ({ adjustment, ...updated }) => {
      qc.setQueryData(detailKey, (previous?: typeof detail.data) => ({ ...updated, adjustments: [
        adjustment, ...(previous?.adjustments ?? []).filter((row) => row.id !== adjustment.id),
      ] }));
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["admin", "credits", "list"] }),
        qc.invalidateQueries({ queryKey: ["billing", account.user_id, "wallet"] }),
      ]);
      setSaved(`${actions[adjustment.operation]} ${points(adjustment.units)}，当前可用 ${points(updated.available)}`);
      setValue(""); setReason(""); setRequestKey(crypto.randomUUID());
    },
  });
  const units = parseMinor(value);
  const canSave = !save.isPending && !!reason.trim() && units != null && units > 0 && (
    operation === "grant" ? balance.is_active : units <= balance.grant_available
  );
  return (
    <div className="admin-detail">
      <p>{balance.display_name || balance.username} · {balance.email || balance.username}</p>
      <Notice>可用 {points(balance.available)} 积分，可收回 {points(balance.grant_available)} 赠送积分。</Notice>
      <form className="admin-detail" onSubmit={(e) => { e.preventDefault(); if (canSave) save.mutate(); }}>
        <fieldset className="flex gap-4">
          <legend className="sr-only">操作方式</legend>
          {(["grant", "revoke"] as const).map((kind) => (
            <label key={kind} className="flex items-center gap-2">
              <input type="radio" name="credit-operation" checked={kind === operation}
                disabled={save.isPending || (kind === "grant" && !balance.is_active)}
                onChange={() => { setOperation(kind); setSaved(""); }} />{actions[kind]}
            </label>
          ))}
        </fieldset>
        {operation === "revoke" && <Notice>可收回范围是仍然可用的赠送积分。</Notice>}
        <label className="admin-field">积分数量 <Input required type="number" min="0.01" step="0.01" value={value} disabled={save.isPending}
          onChange={(e) => { setValue(e.target.value); setSaved(""); }} /></label>
        <label className="admin-field">原因 <Input required maxLength={500} value={reason} disabled={save.isPending}
          onChange={(e) => { setReason(e.target.value); setSaved(""); }} /></label>
        {save.error && <ErrorNotice error={save.error} />}
        {saved && <p role="status">{saved}</p>}
        <div className="admin-inline-actions"><Button disabled={!canSave}>{save.isPending ? "处理中…" : actions[operation]}</Button></div>
      </form>
      <h3 className="admin-section-title">最近调整</h3>
      {detail.error && <ErrorNotice error={detail.error} retry={() => void detail.refetch()} />}
      {detail.isPending && <Loading />}
      {detail.data?.adjustments.length === 0 && <Empty>还没有积分调整记录。</Empty>}
      {detail.data?.adjustments.map((row) => (
        <div key={row.id} className="border-t py-2 text-sm [overflow-wrap:anywhere]">
          <strong>{actions[row.operation]} {points(row.units)}</strong> · {date(row.created_at)}
          <p className="text-muted-foreground">{row.reason}</p>
        </div>
      ))}
    </div>
  );
}
