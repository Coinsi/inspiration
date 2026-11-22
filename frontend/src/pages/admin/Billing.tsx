import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Plus, RefreshCw, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  billingCurrencies,
  billingDate,
  currencyDecimals,
  decimalAmount,
  money,
  orderLabel,
  paymentOrderLabel,
  parseMinor,
  points,
  providerConfigDefaults,
  refundLabel,
  type BillingSettings,
  type CreditPack,
  type PaymentOrder,
  type PaymentProvider,
  type PaymentProviderType,
  type PaymentRefund,
} from "@/lib/billing";
import { BillingOrderDetail } from "@/components/BillingOrderDetail";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { StudioSelect } from "@/components/ui/studio-select";
import { useConfirm } from "@/components/ui/confirm";
import { Empty, ErrorNotice, Heading, Loading } from "./shared";
import { useAdminUnsaved } from "./navigation";
import { Reservations } from "./BillingReservations";
import { BillingPlans } from "./BillingPlans";
import "../billing.css";

type Tab =
  | "orders"
  | "plans"
  | "packs"
  | "providers"
  | "settings"
  | "reservations";
const tabs: [Tab, string][] = [
  ["orders", "订单与退款"],
  ["plans", "会员套餐"],
  ["packs", "积分包"],
  ["providers", "支付渠道"],
  ["settings", "收款设置"],
  ["reservations", "积分预留"],
];

export default function AdminBilling() {
  const { me } = useAuth();
  const allowed =
    me?.user.is_platform_admin &&
    (me.user.platform_role == null || me.user.platform_role === "super");
  if (!allowed)
    return (
      <div className="admin-denied">
        <ShieldCheck size={32} />
        <h1>支付管理仅供超级管理员使用</h1>
        <Link to="/admin">返回管理概览</Link>
      </div>
    );
  return <BillingManager />;
}

function BillingManager() {
  const [tab, setTab] = useState<Tab>("orders");
  const [editPack, setEditPack] = useState<CreditPack | "new" | null>(null);
  const [editProvider, setEditProvider] = useState<
    PaymentProvider | "new" | null
  >(null);
  const [selected, select] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [offset, setOffset] = useState(0);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const qc = useQueryClient();
  const confirm = useConfirm();
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearchTerm(search.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  const orderParams = new URLSearchParams({
    limit: "50",
    offset: String(offset),
  });
  if (searchTerm) orderParams.set("search", searchTerm);
  if (filter && filter !== "refund") orderParams.set("status", filter);
  if (filter === "refund") orderParams.set("refund_attention", "true");
  const orders = useQuery({
    queryKey: ["admin", "billing", "orders", orderParams.toString()],
    queryFn: ({ signal }) =>
      api.get<PaymentOrder[]>(`/admin/billing/orders?${orderParams}`, {
        signal,
      }),
    enabled: tab === "orders",
    placeholderData: (previous) => previous,
  });
  const packs = useQuery({
    queryKey: ["admin", "billing", "packs"],
    queryFn: ({ signal }) =>
      api.get<CreditPack[]>("/admin/billing/packs", { signal }),
    enabled: tab === "packs",
  });
  const providers = useQuery({
    queryKey: ["admin", "billing", "providers"],
    queryFn: ({ signal }) =>
      api.get<PaymentProvider[]>("/admin/billing/providers", { signal }),
    enabled: tab === "providers",
  });
  const types = useQuery({
    queryKey: ["admin", "billing", "types"],
    queryFn: ({ signal }) =>
      api.get<PaymentProviderType[]>("/admin/billing/provider-types", {
        signal,
      }),
    select: (catalog) =>
      catalog.map((type) =>
        type.kind === "easypay" ? { ...type, name: "易支付" } : type,
      ),
    enabled: tab === "providers",
  });
  const settings = useQuery({
    queryKey: ["admin", "billing", "settings"],
    queryFn: ({ signal }) =>
      api.get<BillingSettings>("/admin/billing/settings", { signal }),
    enabled: tab === "settings" || tab === "providers",
  });
  const order = orders.data?.find((item) => item.id === selected);
  const filtered = orders.data ?? [];

  async function refresh() {
    await qc.invalidateQueries({ queryKey: ["admin", "billing"] });
    await qc.invalidateQueries({ queryKey: ["billing"] });
  }
  async function action(
    path: string,
    confirmation?: string,
    body: unknown = {},
    message?: string,
  ) {
    if (busy) return false;
    if (
      confirmation &&
      !(await confirm({
        title: confirmation,
        message:
          message ??
          (confirmation.startsWith("拒绝")
            ? "拒绝后不会向支付渠道发起退款，申请人可在订单中查看结果。"
            : order?.type === "membership"
              ? "批准后将向支付渠道提交全额退款，并收回该订单的会员权益。"
              : "批准后将向支付渠道提交退款并扣回对应积分。"),
        confirmText: "确认执行",
        danger: true,
      }))
    )
      return false;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.post<PaymentOrder>(path, body);
      if (updated?.id) {
        qc.setQueriesData<PaymentOrder[]>(
          { queryKey: ["admin", "billing", "orders"] },
          (current) =>
            current?.map((order) =>
              order.id === updated.id ? updated : order,
            ),
        );
      }
      await refresh();
      return true;
    } catch (e) {
      setError(e);
      return false;
    } finally {
      setBusy(false);
    }
  }
  const currentQuery =
    tab === "orders"
      ? orders
      : tab === "packs"
        ? packs
        : tab === "providers"
          ? providers
          : settings;
  async function changeTab(next: Tab) {
    if (next === tab) return;
    if (
      settingsDirty &&
      !(await confirm({
        title: "放弃未保存的设置？",
        message: "当前收款设置尚未保存。",
        confirmText: "放弃修改",
        danger: true,
      }))
    )
      return;
    setSettingsDirty(false);
    setTab(next);
    setError(null);
  }
  return (
    <div className="billing-admin">
      <Heading
        title="支付与积分"
        description="管理会员套餐、积分包、收款渠道，以及付款和退款记录。"
      >
        <Button variant="outline" onClick={() => void refresh()}>
          <RefreshCw size={15} />
          刷新
        </Button>
      </Heading>
      <nav className="billing-tabs" aria-label="支付管理分类">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            aria-current={tab === key ? "page" : undefined}
            onClick={() => void changeTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      {error != null && !selected && <ErrorNotice error={error} />}
      {tab !== "reservations" && tab !== "plans" && currentQuery.error && (
        <ErrorNotice
          error={currentQuery.error}
          retry={() => void currentQuery.refetch()}
        />
      )}
      {tab !== "reservations" && tab !== "plans" && currentQuery.isLoading && (
        <Loading />
      )}
      {tab === "reservations" && <Reservations />}
      {tab === "plans" && <BillingPlans />}
      {tab === "orders" && orders.data && (
        <section className="admin-panel">
          <div className="billing-toolbar">
            <Input
              aria-label="搜索订单号或用户"
              placeholder="订单号、用户邮箱或用户 ID"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <StudioSelect
              aria-label="订单状态"
              value={filter}
              onValueChange={(value) => {
                setFilter(value);
                setOffset(0);
              }}
            >
              <option value="">全部状态</option>
              <option value="refund">退款需处理</option>
              {(
                [
                  "pending",
                  "paid",
                  "completed",
                  "failed",
                  "expired",
                  "cancelled",
                ] as const
              ).map((status) => (
                <option key={status} value={status}>
                  {status === "completed"
                    ? "已完成"
                    : status === "paid"
                      ? "已付款 · 处理中"
                      : orderLabel(status)}
                </option>
              ))}
            </StudioSelect>
          </div>
          {filtered.length ? (
            <div className="admin-table-scroll">
              <table className="billing-orders-table">
                <thead>
                  <tr>
                    <th>订单 / 用户</th>
                    <th>套餐</th>
                    <th>付款金额</th>
                    <th>状态</th>
                    <th>创建时间</th>
                    <th>
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <span className="billing-table-id" title={item.id}>
                          {item.id.slice(0, 12)}
                        </span>
                        <small className="billing-table-note">
                          {item.user_email || item.user_id}
                        </small>
                      </td>
                      <td>
                        {item.pack_name}
                        <small className="billing-table-note">
                          {item.type === "membership"
                            ? "会员 · 期内 "
                            : "永久 "}
                          {points(item.credits)} 积分
                        </small>
                      </td>
                      <td>
                        {money(item.amount_minor, item.currency)}
                        <small className="billing-table-note">
                          {item.provider_name}
                        </small>
                      </td>
                      <td>
                        <span className={`billing-status is-${item.status}`}>
                          {paymentOrderLabel(item)}
                        </span>
                        {item.refunds.length > 0 && (
                          <small className="billing-table-note">
                            {refundLabel(item.refunds[0].status)}
                          </small>
                        )}
                      </td>
                      <td>{billingDate(item.created_at)}</td>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => {
                            select(item.id);
                            setError(null);
                          }}
                        >
                          查看订单
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>没有符合条件的付款订单</Empty>
          )}
          <div className="billing-pagination">
            <Button
              variant="outline"
              disabled={offset === 0 || orders.isFetching}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              上一页
            </Button>
            <span>第 {Math.floor(offset / 50) + 1} 页</span>
            <Button
              variant="outline"
              disabled={filtered.length < 50 || orders.isFetching}
              onClick={() => setOffset(offset + 50)}
            >
              下一页
            </Button>
          </div>
        </section>
      )}
      {tab === "packs" && packs.data && (
        <section className="admin-panel">
          <div className="billing-toolbar">
            <p>积分包的价格、积分和币种会在下单时固定。</p>
            <Button onClick={() => setEditPack("new")}>
              <Plus size={16} />
              新建积分包
            </Button>
          </div>
          {packs.data.length ? (
            <div className="admin-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>积分包</th>
                    <th>积分</th>
                    <th>售价</th>
                    <th>状态</th>
                    <th>
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {packs.data.map((item) => (
                    <tr key={item.id}>
                      <td>
                        {item.name}
                        <small className="billing-table-note">
                          {item.description}
                        </small>
                      </td>
                      <td>{points(item.credits)}</td>
                      <td>{money(item.amount_minor, item.currency)}</td>
                      <td>{item.active ? "在售" : "已下架"}</td>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => setEditPack(item)}
                        >
                          编辑
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>新建积分包后，用户才能选择购买。</Empty>
          )}
        </section>
      )}
      {tab === "providers" && providers.data && (
        <section className="admin-panel">
          <div className="billing-toolbar">
            <p>密钥仅在创建时填写，不会显示在渠道列表中。</p>
            <Button
              disabled={!types.data?.length || !settings.data?.public_origin}
              onClick={() => setEditProvider("new")}
            >
              <Plus size={16} />
              添加支付渠道
            </Button>
          </div>
          {types.error && (
            <ErrorNotice
              error={types.error}
              retry={() => void types.refetch()}
            />
          )}
          {settings.error && (
            <ErrorNotice
              error={settings.error}
              retry={() => void settings.refetch()}
            />
          )}
          {settings.data && !settings.data.public_origin && (
            <div className="billing-notice flex flex-wrap items-center gap-x-4 gap-y-2">
              <p>请先保存网站公开地址。「开放套餐购买」关闭时也可以添加渠道。</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void changeTab("settings")}
              >
                去收款设置
              </Button>
            </div>
          )}
          {providers.data.length ? (
            <div className="admin-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>渠道</th>
                    <th>服务商</th>
                    <th>币种</th>
                    <th>状态</th>
                    <th>退款</th>
                    <th>
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {providers.data.map((item) => (
                    <tr key={item.id}>
                      <td>{item.name}</td>
                      <td>
                        {types.data?.find((t) => t.kind === item.kind)?.name ??
                          item.kind}
                      </td>
                      <td>{item.currency.toUpperCase()}</td>
                      <td>{item.active ? "已启用" : "已停用"}</td>
                      <td>
                        {item.allow_refunds === false ? "已关闭" : "允许申请"}
                      </td>
                      <td>
                        <Button
                          variant="ghost"
                          onClick={() => setEditProvider(item)}
                        >
                          管理
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>还没有支付渠道。</Empty>
          )}
        </section>
      )}
      {tab === "settings" && settings.data && (
        <SettingsForm
          initial={settings.data}
          onSaved={refresh}
          onDirty={setSettingsDirty}
        />
      )}
      <Modal
        open={!!editPack}
        onClose={() => setEditPack(null)}
        title={editPack === "new" ? "新建积分包" : "编辑积分包"}
        width={560}
      >
        {editPack && (
          <PackForm
            initial={editPack === "new" ? null : editPack}
            onSaved={async () => {
              await refresh();
              setEditPack(null);
            }}
          />
        )}
      </Modal>
      <Modal
        open={!!editProvider}
        onClose={() => setEditProvider(null)}
        title={editProvider === "new" ? "添加支付渠道" : "管理支付渠道"}
        width={600}
      >
        {editProvider && (
          <ProviderForm
            initial={editProvider === "new" ? null : editProvider}
            types={types.data ?? []}
            publicOrigin={settings.data?.public_origin ?? ""}
            onSaved={async () => {
              await refresh();
              setEditProvider(null);
            }}
          />
        )}
      </Modal>
      <Modal
        open={!!selected}
        onClose={() => select(null)}
        title="付款订单"
        width={680}
      >
        {error != null && <ErrorNotice error={error} />}
        {order && (
          <>
            <p className="billing-muted billing-order-user">
              用户：{order.user_email || order.user_id}
            </p>
            <BillingOrderDetail
              order={order}
              busy={busy}
              onCheck={() =>
                void action(`/admin/billing/orders/${order.id}/check`)
              }
            />
            {order.refunds
              .filter(
                (r) => r.status !== "succeeded" && r.status !== "rejected",
              )
              .map((refund) => (
                <div className="billing-refund-operation" key={refund.id}>
                  <p>
                    <strong>{refundLabel(refund.status)}</strong> ·{" "}
                    {money(refund.amount_minor, order.currency)}
                  </p>
                  <div className="billing-actions">
                    {refund.status === "requested" && (
                      <>
                        <Button
                          disabled={busy}
                          onClick={() =>
                            void action(
                              `/admin/billing/refunds/${refund.id}/approve`,
                              "批准这笔退款？",
                            )
                          }
                        >
                          批准退款
                        </Button>
                        <Button
                          variant="outline"
                          disabled={busy}
                          onClick={() =>
                            void action(
                              `/admin/billing/refunds/${refund.id}/reject`,
                              "拒绝这笔退款申请？",
                            )
                          }
                        >
                          拒绝申请
                        </Button>
                      </>
                    )}
                    {(refund.status === "pending" ||
                      refund.status === "failed") && (
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() =>
                          void action(
                            `/admin/billing/refunds/${refund.id}/check`,
                          )
                        }
                      >
                        查询退款结果
                      </Button>
                    )}
                  </div>
                  {refund.status === "pending" &&
                    refund.manual_resolution_allowed && (
                      <ManualRefundResolution
                        refund={refund}
                        currency={order.currency}
                        membership={order.type === "membership"}
                        busy={busy}
                        error={error}
                        onResolve={(outcome, reason) =>
                          action(
                            `/admin/billing/refunds/${refund.id}/resolve`,
                            outcome === "succeeded"
                              ? "确认商户已退款成功？"
                              : "确认商户未发生退款？",
                            { outcome, reason },
                            outcome === "succeeded"
                              ? order.type === "membership"
                                ? "将收回这笔订单的会员权益，并记录退款成功，不会再次向渠道发起退款。请确认已有商户后台或退款凭证支持。"
                                : "将结算这笔已实际退款的积分，并记录退款成功，不会再次向渠道发起退款。请确认已有商户后台或退款凭证支持。"
                              : order.type === "membership"
                                ? "将标记退款失败并解除该会员权益的退款冻结。只有商户已明确确认未发生退款时才能操作。无法确认时请保留处理中。"
                                : "将标记退款失败并释放退款预留积分。只有商户已明确确认未发生退款时才能操作。无法确认时请保留处理中。",
                          )
                        }
                      />
                    )}
                </div>
              ))}
            {order.status === "completed" &&
              order.refund_allowed &&
              order.refunded_minor < order.amount_minor &&
              !order.refunds.some(
                (r) => r.status === "pending" || r.status === "requested",
              ) && <AdminRefundForm order={order} onSaved={refresh} />}
          </>
        )}
      </Modal>
    </div>
  );
}

function ManualRefundResolution({
  refund,
  currency,
  membership,
  busy,
  error,
  onResolve,
}: {
  refund: PaymentRefund;
  currency: string;
  membership: boolean;
  busy: boolean;
  error: unknown;
  onResolve: (
    outcome: "succeeded" | "failed",
    reason: string,
  ) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [outcome, setOutcome] = useState<"" | "succeeded" | "failed">("");
  const [reason, setReason] = useState("");
  if (!open)
    return (
      <div className="billing-refund-operation">
        <p>
          渠道尚未确认退款最终结果。可先核实商户后台或退款凭证；无法确认时保留处理中。
        </p>
        <Button variant="outline" disabled={busy} onClick={() => setOpen(true)}>
          核实商户退款结果
        </Button>
      </div>
    );
  return (
    <form
      className="billing-refund-form billing-form"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!outcome || !reason.trim() || busy) return;
        if (await onResolve(outcome, reason.trim())) setOpen(false);
      }}
    >
      <h3>记录已核实的退款结果</h3>
      <p>
        核对商户退款 {money(refund.amount_minor, currency)} 的实际结果。
        {membership
          ? "此操作只更新退款记录和会员权益状态，不会重新发起退款。"
          : "此操作只更新退款记录和预留积分，不会重新发起退款。"}
      </p>
      <label>
        商户实际退款结果
        <StudioSelect
          aria-label="商户实际退款结果"
          value={outcome}
          disabled={busy}
          onValueChange={(value) =>
            setOutcome(value as "" | "succeeded" | "failed")
          }
        >
          <option value="" disabled>
            请选择已核实的结果
          </option>
          <option value="succeeded">已核实退款成功</option>
          <option value="failed">已核实未发生退款</option>
        </StudioSelect>
      </label>
      <label>
        核实依据
        <textarea
          aria-label="核实依据"
          value={reason}
          required
          maxLength={500}
          disabled={busy}
          placeholder="填写商户退款记录、退款凭证或确认结果的依据"
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      {error != null && (
        <div className="billing-error">
          {error instanceof Error ? error.message : "核实结果未保存，请重试。"}
        </div>
      )}
      <div className="billing-actions">
        <Button
          type="button"
          variant="ghost"
          disabled={busy}
          onClick={() => setOpen(false)}
        >
          暂不处理
        </Button>
        <Button disabled={busy || !outcome || !reason.trim()}>
          {busy ? "记录中…" : "确认核实结果"}
        </Button>
      </div>
    </form>
  );
}

function SettingsForm({
  initial,
  onSaved,
  onDirty,
}: {
  initial: BillingSettings;
  onSaved: () => Promise<void>;
  onDirty: (value: boolean) => void;
}) {
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);
  useAdminUnsaved(dirty);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  async function save() {
    if (busy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await api.patch<BillingSettings>(
        "/admin/billing/settings",
        form,
      );
      await onSaved();
      setForm((draft) => (draft === form ? updated : draft));
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="admin-panel billing-form billing-settings"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <h2>收款设置</h2>
      <label>
        网站公开地址
        <Input
          type="url"
          required={form.enabled}
          value={form.public_origin}
          placeholder="https://example.com"
          onChange={(e) => {
            setForm({ ...form, public_origin: e.target.value });
            setSaved(false);
          }}
        />
        <small>
          用于付款返回和接收通知。首次配置时，填写 HTTPS 地址，保持「开放套餐购买」关闭，点击「保存设置」。
        </small>
      </label>
      <label>
        订阅美元兑人民币汇率
        <Input
          type="text"
          inputMode="decimal"
          pattern="[0-9]{1,6}([.][0-9]{1,6})?"
          value={form.subscription_usd_to_cny_rate ?? ""}
          placeholder="例如 7.20"
          onChange={(e) => {
            setForm({ ...form, subscription_usd_to_cny_rate: e.target.value || null });
            setSaved(false);
          }}
        />
        <small>
          填写 1 美元折合多少元人民币。美元会员套餐使用人民币渠道付款时按此换算，留空或填 0 关闭换算。
        </small>
      </label>
      <label className="billing-toggle">
        <input
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => {
            setForm({ ...form, enabled: e.target.checked });
            setSaved(false);
          }}
        />
        <span>
          <strong>开放套餐购买</strong>
          <small>
            启用支付渠道并上架可用该渠道付款的会员套餐或积分包后，再开启购买。关闭后不接受新订单。
          </small>
        </span>
      </label>
      <label className="billing-toggle">
        <input
          type="checkbox"
          checked={form.payment_required}
          onChange={(e) => {
            setForm({ ...form, payment_required: e.target.checked });
            setSaved(false);
          }}
        />
        <span>
          <strong>启用积分付费检查</strong>
          <small>
            开启后，适用任务需要满足积分要求。请确认积分规则与账户余额已准备好。
          </small>
        </span>
      </label>
      <label>
        付款有效期（分钟）
        <Input
          type="number"
          min={35}
          max={120}
          required
          value={form.checkout_minutes}
          onChange={(e) =>
            setForm({ ...form, checkout_minutes: Number(e.target.value) })
          }
        />
      </label>
      {error != null && <ErrorNotice error={error} />}
      {saved && !dirty && (
        <p role="status" className="billing-notice">
          收款设置已保存
        </p>
      )}
      <div className="billing-actions">
        <Button disabled={busy || !dirty}>
          {busy ? "保存中…" : "保存设置"}
        </Button>
      </div>
    </form>
  );
}

function CurrencyInput({
  value,
  change,
  disabled,
}: {
  value: string;
  change: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <StudioSelect
      aria-label="币种"
      value={value}
      disabled={disabled}
      onValueChange={change}
    >
      {billingCurrencies.map((c) => (
        <option value={c} key={c}>
          {c.toUpperCase()}
        </option>
      ))}
    </StudioSelect>
  );
}

function PackForm({
  initial,
  onSaved,
}: {
  initial: CreditPack | null;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [currency, setCurrency] = useState(initial?.currency ?? "cny");
  const [amount, setAmount] = useState(
    initial
      ? decimalAmount(initial.amount_minor, currencyDecimals(initial.currency))
      : "",
  );
  const [credits, setCredits] = useState(
    initial ? decimalAmount(initial.credits) : "",
  );
  const [active, setActive] = useState(initial?.active ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function save() {
    const amountMinor = parseMinor(amount, currencyDecimals(currency));
    const creditsMinor = parseMinor(credits);
    if (!amountMinor || !creditsMinor) {
      setError(
        new Error(
          "请输入有效的正数；日元仅支持整数，积分及其他币种最多两位小数。",
        ),
      );
      return;
    }
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        name: name.trim(),
        description: description.trim(),
        currency,
        amount_minor: amountMinor,
        credits: creditsMinor,
        active,
      };
      if (initial) await api.patch(`/admin/billing/packs/${initial.id}`, body);
      else await api.post("/admin/billing/packs", body);
      await onSaved();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="billing-form"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label>
        积分包名称
        <Input
          value={name}
          required
          maxLength={100}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        说明
        <textarea
          value={description}
          maxLength={500}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <div className="billing-form-grid">
        <label>
          币种
          <CurrencyInput value={currency} change={setCurrency} />
        </label>
        <label>
          售价
          <Input
            inputMode="decimal"
            required
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
      </div>
      <label>
        到账积分
        <Input
          inputMode="decimal"
          required
          value={credits}
          onChange={(e) => setCredits(e.target.value)}
        />
      </label>
      <label className="billing-toggle">
        <input
          type="checkbox"
          checked={active}
          onChange={(e) => setActive(e.target.checked)}
        />
        <span>上架出售</span>
      </label>
      {error != null && <ErrorNotice error={error} />}
      <div className="billing-actions">
        <Button disabled={busy || !name.trim()}>
          {busy ? "保存中…" : "保存积分包"}
        </Button>
      </div>
    </form>
  );
}

function ProviderForm({
  initial,
  types,
  onSaved,
  publicOrigin,
}: {
  initial: PaymentProvider | null;
  types: PaymentProviderType[];
  onSaved: () => Promise<void>;
  publicOrigin: string;
}) {
  const [providerId] = useState(() => initial?.id ?? crypto.randomUUID());
  const [copyNotice, setCopyNotice] = useState("");
  const callbackUrl = publicOrigin
    ? `${publicOrigin.replace(/\/$/, "")}/api/v1/billing/webhook/${providerId}`
    : "";
  const [name, setName] = useState(initial?.name ?? "");
  const [kind, setKind] = useState(initial?.kind ?? types[0]?.kind ?? "");
  const [currency, setCurrency] = useState(initial?.currency ?? "cny");
  const [active, setActive] = useState(initial?.active ?? false);
  const [allowRefunds, setAllowRefunds] = useState(
    initial?.allow_refunds ?? true,
  );
  const [config, setConfig] = useState<Record<string, string>>(() =>
    initial
      ? {}
      : providerConfigDefaults(types.find((type) => type.kind === kind)),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const providerType = types.find((t) => t.kind === kind);
  const cnyOnly =
    kind === "wechatpay" || kind === "alipay" || kind === "easypay";
  async function save() {
    if (busy) return;
    if (!initial) {
      const missing = providerType?.fields.find(
        (field) => field.required && !config[field.key]?.trim(),
      );
      if (missing) {
        setError(new Error(`请填写${missing.label}`));
        return;
      }
      const invalid = providerType?.fields.find(
        (field) =>
          field.options?.length &&
          config[field.key] &&
          !field.options.some((option) => option.value === config[field.key]),
      );
      if (invalid) {
        setError(new Error(`请选择有效的${invalid.label}`));
        return;
      }
    }
    setBusy(true);
    setError(null);
    try {
      if (initial)
        await api.patch(`/admin/billing/providers/${initial.id}`, {
          name: name.trim(),
          active,
          allow_refunds: allowRefunds,
        });
      else
        await api.post("/admin/billing/providers", {
          id: providerId,
          name: name.trim(),
          kind,
          currency: cnyOnly ? "cny" : currency,
          active,
          allow_refunds: allowRefunds,
          config,
        });
      setConfig({});
      await onSaved();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="billing-form"
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label>
        渠道名称
        <Input
          required
          value={name}
          maxLength={100}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      {!initial && (
        <>
          <label>
            支付服务商
            <StudioSelect
              aria-label="支付服务商"
              value={kind}
              onValueChange={(v) => {
                setKind(v);
                setConfig(
                  providerConfigDefaults(types.find((type) => type.kind === v)),
                );
                setCurrency("cny");
                setError(null);
              }}
            >
              {types.map((t) => (
                <option key={t.kind} value={t.kind}>
                  {t.name}
                </option>
              ))}
            </StudioSelect>
          </label>
          {kind === "easypay" && (
            <p className="billing-muted">
              一个渠道选择一种支付方式。如需同时提供微信和支付宝，请使用同一商户配置分别创建两个渠道，并用渠道名称区分。
            </p>
          )}
          <label>
            收款币种
            <CurrencyInput
              value={cnyOnly ? "cny" : currency}
              change={setCurrency}
              disabled={cnyOnly}
            />
          </label>
          <div className="billing-callback">
            <label>
              支付通知地址
              <Input
                readOnly
                value={callbackUrl}
                onFocus={(e) => e.currentTarget.select()}
              />
            </label>
            <p className="billing-muted">
              {kind === "stripe"
                ? "先在 Stripe 后台使用此地址创建 Webhook，订阅 checkout.session.completed、checkout.session.async_payment_succeeded、checkout.session.async_payment_failed 和 checkout.session.expired 事件，再将签名密钥填入下方。"
                : "此地址用于接收支付平台的付款通知。"}
            </p>
            <Button
              type="button"
              variant="outline"
              disabled={!callbackUrl}
              onClick={() =>
                void navigator.clipboard.writeText(callbackUrl).then(
                  () => setCopyNotice("通知地址已复制"),
                  () => setCopyNotice("请选中上方地址后手动复制"),
                )
              }
            >
              复制通知地址
            </Button>
            {copyNotice && (
              <p role="status" className="billing-muted">
                {copyNotice}
              </p>
            )}
          </div>
          {providerType?.fields.map((field) => (
            <label key={field.key}>
              {field.label}
              {field.required ? " *" : ""}
              {field.options?.length ? (
                <StudioSelect
                  aria-label={field.label}
                  value={config[field.key] ?? ""}
                  disabled={busy}
                  onValueChange={(value) =>
                    setConfig({ ...config, [field.key]: value })
                  }
                >
                  {!field.required && field.default == null && (
                    <option value="">未指定</option>
                  )}
                  {field.options.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </StudioSelect>
              ) : field.key.includes("private_key") ||
                field.key.endsWith("public_key") ? (
                <textarea
                  className={field.secret ? "billing-secret" : undefined}
                  required={field.required}
                  value={config[field.key] ?? ""}
                  spellCheck={false}
                  autoComplete="off"
                  onChange={(e) =>
                    setConfig({ ...config, [field.key]: e.target.value })
                  }
                />
              ) : (
                <Input
                  type={field.secret ? "password" : "text"}
                  autoComplete={field.secret ? "new-password" : "off"}
                  required={field.required}
                  value={config[field.key] ?? ""}
                  spellCheck={false}
                  onChange={(e) =>
                    setConfig({ ...config, [field.key]: e.target.value })
                  }
                />
              )}
            </label>
          ))}
        </>
      )}
      {initial && (
        <>
          <p className="billing-muted">
            {providerType?.name ?? initial.kind} ·{" "}
            {initial.currency.toUpperCase()}
            。已有密钥不会返回；更换配置请创建新渠道并停用旧渠道。
          </p>
          <label>
            支付通知地址
            <Input
              readOnly
              value={callbackUrl}
              onFocus={(e) => e.currentTarget.select()}
            />
          </label>
        </>
      )}
      <label className="billing-toggle">
        <input
          type="checkbox"
          checked={active}
          onChange={(e) => setActive(e.target.checked)}
        />
        <span>启用此渠道</span>
      </label>
      <label className="billing-toggle">
        <input
          type="checkbox"
          checked={allowRefunds}
          onChange={(e) => setAllowRefunds(e.target.checked)}
        />
        <span>允许用户申请退款</span>
      </label>
      {error != null && <ErrorNotice error={error} />}
      <div className="billing-actions">
        <Button disabled={busy || !name.trim() || !kind}>
          {busy ? "保存中…" : "保存支付渠道"}
        </Button>
      </div>
    </form>
  );
}

function AdminRefundForm({
  order,
  onSaved,
}: {
  order: PaymentOrder;
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const attempt = useRef<{ content: string; key: string } | null>(null);
  const confirm = useConfirm();
  const remaining = order.amount_minor - order.refunded_minor;
  const membership = order.type === "membership";
  async function refund() {
    const minor = membership
      ? order.amount_minor
      : parseMinor(amount, currencyDecimals(order.currency));
    if (!minor || minor > remaining) {
      setError(new Error("退款金额必须大于零，且不能超过未退款金额。"));
      return;
    }
    if (busy || !reason.trim()) return;
    if (
      !(await confirm({
        title: "确认发起退款？",
        message: membership
          ? `将全额退款 ${money(minor, order.currency)}，并收回该订单的会员权益。请核对原因。`
          : `将退款 ${money(minor, order.currency)}，并扣回对应积分。请核对金额与原因。`,
        confirmText: "确认退款",
        danger: true,
      }))
    )
      return;
    setBusy(true);
    setError(null);
    const content = `${minor}:${reason.trim()}`;
    if (attempt.current?.content !== content)
      attempt.current = { content, key: crypto.randomUUID() };
    try {
      await api.post(`/admin/billing/orders/${order.id}/refunds`, {
        amount_minor: minor,
        reason: reason.trim(),
        idempotency_key: attempt.current.key,
      });
      await onSaved();
      attempt.current = null;
      setOpen(false);
      setAmount("");
      setReason("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (!open)
    return (
      <div className="billing-refund-operation">
        <Button
          variant="outline"
          onClick={() => {
            setOpen(true);
            setAmount(
              decimalAmount(remaining, currencyDecimals(order.currency)),
            );
          }}
        >
          发起退款
        </Button>
      </div>
    );
  return (
    <form
      className="billing-refund-form billing-form"
      onSubmit={(e) => {
        e.preventDefault();
        void refund();
      }}
    >
      <h3>发起退款</h3>
      <p>
        {membership
          ? `会员仅支持未使用时全额退款 ${money(order.amount_minor, order.currency)}，退款将收回该笔订单的会员权益。`
          : `最多可退 ${money(remaining, order.currency)}。退款将扣回对应积分。`}
      </p>
      {!membership && (
        <label>
          退款金额（{order.currency.toUpperCase()}）
          <Input
            inputMode="decimal"
            required
            disabled={busy}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
      )}
      <label>
        退款原因
        <textarea
          required
          disabled={busy}
          maxLength={500}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      {error != null && <ErrorNotice error={error} />}
      <div className="billing-actions">
        <Button
          type="button"
          variant="ghost"
          disabled={busy}
          onClick={() => setOpen(false)}
        >
          取消
        </Button>
        <Button disabled={busy || !reason.trim()}>
          {busy ? "提交中…" : "核对并退款"}
        </Button>
      </div>
    </form>
  );
}
