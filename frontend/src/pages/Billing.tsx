import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Check, Coins, RefreshCw } from "lucide-react";
import { AccountMenu } from "@/components/AccountMenu";
import { BillingOrderDetail } from "@/components/BillingOrderDetail";
import { BillingMemberships } from "@/components/BillingMemberships";
import { BillingPlanCards } from "@/components/BillingPlanCards";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useConfirm } from "@/components/ui/confirm";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { loadPlanPreview } from "@/lib/billingPlanPreview";
import {
  billingDate,
  money,
  membershipPurchaseDuration,
  paymentOrderLabel,
  points,
  refundLabel,
  type BillingCatalog,
  type CreditWallet,
  type PaymentOrder,
  type MembershipTerm,
} from "@/lib/billing";
import "./billing.css";

const entryNames: Record<string, [string, string]> = {
  purchase: ["购买积分", "Points purchase"],
  recharge: ["积分到账", "Points credited"],
  credit: ["积分到账", "Points credited"],
  reserve: ["预留积分", "Points reserved"],
  release: ["释放预留", "Reservation released"],
  consume: ["使用积分", "Points used"],
  capture: ["使用积分", "Points used"],
  refund: ["退款扣回", "Refund adjustment"],
  refund_reserve: ["退款积分预留", "Points held for refund"],
  refund_settle: ["退款积分结算", "Refund points settled"],
  refund_release: ["退款预留释放", "Refund hold released"],
  adjustment: ["积分调整", "Points adjustment"],
};

export default function Billing() {
  const { lang } = useI18n();
  const zh = lang === "zh";
  const { me } = useAuth();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const scope = ["billing", me?.user.id];
  const [params, setParams] = useSearchParams();
  const orderId = params.get("order");
  const preview = params.get("preview") === "1";
  const previewAllowed =
    !!me?.user.is_platform_admin &&
    (me.user.platform_role == null || me.user.platform_role === "super");
  const requestedSection = params.get("section");
  const section =
    !preview &&
    (requestedSection === "memberships" || requestedSection === "orders")
      ? requestedSection
      : "purchase";
  const [packId, setPack] = useState("");
  const [planId, setPlan] = useState("");
  const [purchaseKind, setPurchaseKind] = useState<"credits" | "membership">(
    "membership",
  );
  const [checkoutOpen, setCheckoutOpen] = useState(false);
  const [providerId, setProvider] = useState("");
  const [offset, setOffset] = useState(0);
  const [membershipOffset, setMembershipOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [refundOpen, setRefundOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [refundError, setRefundError] = useState("");
  const [refundBusy, setRefundBusy] = useState(false);
  const inflight = useRef(false);
  const attempt = useRef<{ selection: string; key: string } | null>(null);
  const catalog = useQuery({
    queryKey: [...scope, "catalog"],
    queryFn: ({ signal }) =>
      api.get<BillingCatalog>("/billing/catalog", { signal }),
    enabled: !preview,
  });
  const planPreview = useQuery({
    queryKey: [...scope, "plan-preview"],
    queryFn: ({ signal }) => loadPlanPreview(signal),
    enabled: preview && previewAllowed,
    retry: false,
  });
  const orders = useQuery({
    queryKey: [...scope, "orders", offset],
    queryFn: ({ signal }) =>
      api.get<PaymentOrder[]>(`/billing/orders?limit=50&offset=${offset}`, {
        signal,
      }),
    placeholderData: (previous) => previous,
    enabled: !preview && section === "orders",
    refetchInterval: (query) =>
      query.state.data?.some(
        (o) => o.status === "pending" || o.status === "paid",
      )
        ? 5000
        : false,
  });
  const order = useQuery({
    queryKey: [...scope, "order", orderId],
    queryFn: ({ signal }) =>
      api.get<PaymentOrder>(`/billing/orders/${encodeURIComponent(orderId!)}`, {
        signal,
      }),
    enabled: !!orderId && !preview,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "pending" || status === "paid" ? 3000 : false;
    },
  });
  const wallet = useQuery({
    queryKey: [...scope, "wallet"],
    queryFn: ({ signal }) =>
      api.get<CreditWallet>("/billing/wallet", { signal }),
    enabled: !preview && section !== "purchase",
  });
  const memberships = useQuery({
    queryKey: [...scope, "memberships", membershipOffset],
    queryFn: ({ signal }) =>
      api.get<MembershipTerm[]>(
        `/billing/memberships?limit=100&offset=${membershipOffset}`,
        { signal },
      ),
    placeholderData: (previous) => previous,
    enabled: !preview && section === "memberships",
    refetchInterval: (query) =>
      query.state.data?.some(
        (term) => term.status === "active" || term.status === "scheduled",
      )
        ? 60000
        : false,
  });
  useEffect(() => {
    if (preview) return;
    void qc.invalidateQueries({ queryKey: ["billing", me?.user.id, "wallet"] });
    void qc.invalidateQueries({
      queryKey: ["billing", me?.user.id, "memberships"],
    });
  }, [qc, me?.user.id, orders.dataUpdatedAt, order.data?.status, preview]);
  const packs = catalog.data?.packs.filter((p) => p.active) ?? [];
  const plans = (
    preview ? (planPreview.data?.plans ?? []) : (catalog.data?.plans ?? [])
  )
    .filter((p) => preview || p.active)
    .sort((a, b) => a.sort_order - b.sort_order);
  const kind = preview ? "membership" : purchaseKind;
  const pack = packs.find((p) => p.id === packId) ?? packs[0];
  const plan = plans.find((p) => p.id === planId);
  const product = kind === "membership" ? plan : pack;
  const providers =
    catalog.data?.providers.filter(
      (p) => p.active && p.currency === product?.currency,
    ) ?? [];
  const provider = providers.find((p) => p.id === providerId) ?? providers[0];
  const selectedOrder = order.data;
  const canRefund =
    selectedOrder?.status === "completed" &&
    selectedOrder.refund_allowed &&
    selectedOrder.refunded_minor < selectedOrder.amount_minor &&
    !selectedOrder.refunds.some(
      (r) => r.status === "requested" || r.status === "pending",
    );

  function selectOrder(id: string | null) {
    if (preview) return;
    const next = new URLSearchParams(params);
    if (id) next.set("order", id);
    else next.delete("order");
    setParams(next);
    setError("");
    setRefundOpen(false);
  }

  async function refresh() {
    if (preview) {
      if (previewAllowed) await planPreview.refetch();
      return;
    }
    await qc.invalidateQueries({ queryKey: scope });
  }

  function sectionUrl(value: "purchase" | "memberships" | "orders") {
    const next = new URLSearchParams(params);
    if (value === "purchase") next.delete("section");
    else next.set("section", value);
    return { pathname: "/billing", search: next.toString() };
  }

  async function createOrder() {
    if (
      preview ||
      !product ||
      !provider ||
      inflight.current ||
      !catalog.data?.enabled ||
      (kind === "membership" && !checkoutOpen)
    )
      return;
    inflight.current = true;
    setBusy(true);
    setError("");
    const selection = `${kind}:${product.id}:${provider.id}`;
    if (attempt.current?.selection !== selection) {
      attempt.current = { selection, key: crypto.randomUUID() };
    }
    try {
      const created = await api.post<PaymentOrder>("/billing/orders", {
        ...(kind === "membership"
          ? { plan_id: product.id }
          : { pack_id: product.id }),
        provider_id: provider.id,
        idempotency_key: attempt.current.key,
      });
      qc.setQueryData([...scope, "order", created.id], created);
      attempt.current = null;
      setCheckoutOpen(false);
      setOffset(0);
      selectOrder(created.id);
      await refresh();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : zh
            ? "创建订单失败，请重试"
            : "Could not create the order. Try again.",
      );
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }

  async function checkOrder() {
    if (preview || !selectedOrder || checking) return;
    setChecking(true);
    setError("");
    try {
      const updated = await api.post<PaymentOrder>(
        `/billing/orders/${selectedOrder.id}/check`,
        {},
      );
      qc.setQueryData([...scope, "order", selectedOrder.id], updated);
      await refresh();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : zh
            ? "查询失败，请重试"
            : "Could not check payment. Try again.",
      );
    } finally {
      setChecking(false);
    }
  }

  async function requestRefund() {
    if (preview || !selectedOrder || !reason.trim() || refundBusy) return;
    setRefundBusy(true);
    setRefundError("");
    try {
      await api.post(`/billing/orders/${selectedOrder.id}/refund-request`, {
        reason: reason.trim(),
      });
      await refresh();
      setRefundOpen(false);
      setReason("");
    } catch (e) {
      setRefundError(
        e instanceof Error
          ? e.message
          : zh
            ? "申请失败，请重试"
            : "Could not request a refund. Try again.",
      );
    } finally {
      setRefundBusy(false);
    }
  }

  async function cancelOrder() {
    if (
      preview ||
      !selectedOrder ||
      checking ||
      selectedOrder.status !== "pending"
    )
      return;
    if (
      !(await confirm({
        title: zh ? "取消这笔付款订单？" : "Cancel this payment order?",
        message: zh
          ? "将向支付渠道确认关闭订单。如已付款，会先核对付款状态。"
          : "The payment provider will confirm cancellation. If paid, its payment status will be checked first.",
        confirmText: zh ? "确认取消订单" : "Confirm cancellation",
      }))
    )
      return;
    setChecking(true);
    setError("");
    try {
      const updated = await api.post<PaymentOrder>(
        `/billing/orders/${selectedOrder.id}/cancel`,
        {},
      );
      qc.setQueryData([...scope, "order", selectedOrder.id], updated);
      await refresh();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : zh
            ? "暂时无法确认取消，请查询付款结果。"
            : "Cancellation could not be confirmed. Check payment status.",
      );
    } finally {
      setChecking(false);
    }
  }

  const paymentControls = (
    <div className="billing-purchase-bar">
      <div
        className="billing-methods"
        role="group"
        aria-label={zh ? "选择支付方式" : "Choose a payment method"}
      >
        {providers.map((item) => (
          <button
            key={item.id}
            disabled={busy}
            aria-pressed={provider?.id === item.id}
            className={provider?.id === item.id ? "is-selected" : ""}
            onClick={() => {
              setProvider(item.id);
              setError("");
            }}
          >
            {item.name}
          </button>
        ))}
        {!providers.length && (
          <p className="billing-muted">
            {zh
              ? "该币种暂无可用支付方式，请选择其他套餐。"
              : "No payment method is available for this currency. Choose another plan."}
          </p>
        )}
      </div>
      <div className="billing-purchase-total">
        <div>
          <span>{zh ? "需支付" : "Total"}</span>
          <strong>
            {product && money(product.amount_minor, product.currency, zh)}
          </strong>
        </div>
        <Button
          disabled={
            busy || !provider || !product || !catalog.data?.enabled || preview
          }
          onClick={() => void createOrder()}
        >
          {busy
            ? zh
              ? "正在创建…"
              : "Creating…"
            : zh
              ? "创建付款订单"
              : "Create payment order"}
          <ArrowRight size={16} />
        </Button>
      </div>
    </div>
  );

  return (
    <div className="billing-shell">
      <header className="billing-topbar">
        <Link to="/projects">
          <ArrowLeft size={17} />
          {zh ? "创作空间" : "Workspace"}
        </Link>
        <AccountMenu />
      </header>
      <main
        className="billing-page billing-consumer-page"
        data-section={section}
      >
        <header
          className={`billing-heading ${section === "purchase" ? "billing-purchase-hero" : ""}`}
        >
          <div>
            <h1>
              {section === "purchase"
                ? zh
                  ? "选择你的创作套餐"
                  : "Choose your creative plan"
                : section === "memberships"
                  ? zh
                    ? "我的会员"
                    : "My memberships"
                  : zh
                    ? "订单记录"
                    : "Order history"}
            </h1>
            <p>
              {section === "purchase"
                ? zh
                  ? "按需选择，每期单独付款。"
                  : "Choose what you need. Pay for each term separately."
                : section === "memberships"
                  ? zh
                    ? "查看会员有效期、期内额度与永久积分。"
                    : "View membership terms, allowances, and permanent points."
                  : zh
                    ? "查看付款结果、退款进度和积分变动。"
                    : "Track payments, refunds, and points activity."}
            </p>
          </div>
          <Button variant="outline" onClick={() => void refresh()}>
            <RefreshCw size={15} />
            {zh ? "刷新" : "Refresh"}
          </Button>
        </header>
        {preview ? (
          <div className="billing-notice billing-preview-notice" role="status">
            <strong>{zh ? "后台预览" : "Admin preview"}</strong>
            <p>
              {planPreview.data?.sample
                ? zh
                  ? "当前展示示例套餐，仅供预览，不可购买。"
                  : "Sample plans for preview only. Purchases are unavailable."
                : zh
                  ? "查看套餐展示效果，不会创建付款订单。"
                  : "Preview plan presentation without creating payment orders."}
            </p>
            <Link to="/admin/billing">
              {zh ? "返回支付管理" : "Back to payment management"}
            </Link>
          </div>
        ) : (
          <nav
            className="billing-section-nav"
            aria-label={zh ? "账单分区" : "Billing sections"}
          >
            {(["purchase", "memberships", "orders"] as const).map((value) => (
              <Link
                key={value}
                to={sectionUrl(value)}
                aria-current={section === value ? "page" : undefined}
                onClick={() => {
                  setCheckoutOpen(false);
                  setError("");
                }}
              >
                {value === "purchase"
                  ? zh
                    ? "购买套餐"
                    : "Buy plans"
                  : value === "memberships"
                    ? zh
                      ? "我的会员"
                      : "My memberships"
                    : zh
                      ? "订单记录"
                      : "Order history"}
              </Link>
            ))}
          </nav>
        )}
        {preview && !previewAllowed && (
          <BillingError
            error={
              zh
                ? "套餐预览仅供超级管理员使用。"
                : "Plan preview is available to super administrators only."
            }
            zh={zh}
          />
        )}
        {preview && planPreview.error && (
          <BillingError
            error={planPreview.error}
            retry={() => void planPreview.refetch()}
            zh={zh}
          />
        )}
        {!preview && section === "memberships" && (
          <>
            <section
              className="billing-wallet"
              aria-label={zh ? "个人积分" : "Your points"}
            >
              <div>
                <span>
                  <Coins size={18} />
                  {zh ? "可用积分" : "Available points"}
                </span>
                <strong>
                  {wallet.data ? points(wallet.data.available, zh) : "—"}
                </strong>
                <small>
                  {zh
                    ? "永久积分 · 与会员额度分开"
                    : "Permanent points · separate from membership"}
                </small>
              </div>
              <div>
                <span>{zh ? "预留积分" : "Reserved points"}</span>
                <strong>
                  {wallet.data ? points(wallet.data.reserved, zh) : "—"}
                </strong>
                <small>
                  {zh
                    ? "待结算任务或退款暂时占用"
                    : "Held for pending tasks or refunds"}
                </small>
              </div>
            </section>
            {wallet.error && (
              <BillingError
                error={wallet.error}
                retry={() => void wallet.refetch()}
                zh={zh}
              />
            )}

            <section
              className="billing-section"
              aria-label={zh ? "我的会员" : "My memberships"}
            >
              <div className="billing-section-heading">
                <h2>{zh ? "会员权益" : "Membership benefits"}</h2>
              </div>
              <p className="billing-muted billing-membership-note">
                {zh
                  ? "会员额度在有效期内使用。续购同一套餐时，下一期从当前到期日开始。每期单独付款。"
                  : "Membership points apply only during their term and remain separate from permanent points. Renewing the same plan extends its term; different plans are independent. No automatic billing."}
              </p>
              {memberships.isLoading && (
                <p className="billing-empty" role="status">
                  {zh ? "正在加载会员…" : "Loading memberships…"}
                </p>
              )}
              {memberships.error && (
                <BillingError
                  error={memberships.error}
                  retry={() => void memberships.refetch()}
                  zh={zh}
                />
              )}
              {memberships.data?.length === 0 && (
                <p className="billing-empty">
                  {zh ? "还没有会员套餐" : "No memberships yet"}
                </p>
              )}
              {memberships.data && (
                <BillingMemberships terms={memberships.data} zh={zh} />
              )}
              {memberships.data &&
                (membershipOffset > 0 || memberships.data.length >= 100) && (
                  <div className="billing-pagination">
                    <Button
                      variant="outline"
                      disabled={
                        membershipOffset === 0 || memberships.isFetching
                      }
                      onClick={() =>
                        setMembershipOffset(Math.max(0, membershipOffset - 100))
                      }
                    >
                      {zh ? "上一页" : "Previous"}
                    </Button>
                    <span>
                      {zh
                        ? `第 ${Math.floor(membershipOffset / 100) + 1} 页`
                        : `Page ${Math.floor(membershipOffset / 100) + 1}`}
                    </span>
                    <Button
                      variant="outline"
                      disabled={
                        memberships.data.length < 100 || memberships.isFetching
                      }
                      onClick={() =>
                        setMembershipOffset(membershipOffset + 100)
                      }
                    >
                      {zh ? "下一页" : "Next"}
                    </Button>
                  </div>
                )}
            </section>
          </>
        )}
        {section === "purchase" && (
          <>
            {!preview && catalog.error && (
              <BillingError
                error={!preview && catalog.error}
                retry={() => void catalog.refetch()}
                zh={zh}
              />
            )}
            {(preview ? planPreview.isLoading : catalog.isLoading) && (
              <p className="billing-empty" role="status">
                {zh ? "正在加载套餐…" : "Loading plans…"}
              </p>
            )}
            {!preview && catalog.data && !catalog.data.enabled && (
              <p className="billing-notice">
                {zh
                  ? "暂未开放购买。你的积分和历史订单仍可查看。"
                  : "Purchases are currently unavailable. Your points and order history remain accessible."}
              </p>
            )}
            {(preview
              ? previewAllowed && !!planPreview.data
              : catalog.data?.enabled) && (
              <section className="billing-section billing-purchase-section">
                {!preview && (
                  <nav
                    className="billing-tabs"
                    aria-label={zh ? "购买类型" : "Purchase type"}
                  >
                    {(["membership", "credits"] as const).map((value) => (
                      <button
                        key={value}
                        disabled={busy}
                        aria-current={kind === value ? "page" : undefined}
                        onClick={() => {
                          setPurchaseKind(value);
                          setProvider("");
                          setError("");
                        }}
                      >
                        {value === "membership"
                          ? zh
                            ? "会员套餐"
                            : "Memberships"
                          : zh
                            ? "积分包"
                            : "Points packs"}
                      </button>
                    ))}
                  </nav>
                )}
                {kind === "membership" && (
                  <p className="billing-muted billing-membership-note">
                    {zh
                      ? "会员积分用于平台图片与视频生成，到期失效。"
                      : "Use membership points for platform image and video generation before the term ends."}
                  </p>
                )}
                {(kind === "membership" ? plans.length : packs.length) ? (
                  <>
                    {kind === "membership" ? (
                      <BillingPlanCards
                        plans={plans}
                        preview={preview}
                        selected={planId || undefined}
                        busy={busy}
                        zh={zh}
                        onSelect={(id) => {
                          setPlan(id);
                          setProvider("");
                          setError("");
                          setCheckoutOpen(true);
                        }}
                      />
                    ) : (
                      <div
                        className="billing-packs"
                        role="group"
                        aria-label={zh ? "选择积分包" : "Choose a points pack"}
                      >
                        {packs.map((item) => (
                          <button
                            key={item.id}
                            className={`billing-pack ${pack?.id === item.id ? "is-selected" : ""}`}
                            aria-pressed={pack?.id === item.id}
                            disabled={busy}
                            onClick={() => {
                              setPack(item.id);
                              setProvider("");
                              setError("");
                            }}
                          >
                            <div className="billing-row">
                              <span>{item.name}</span>
                              {pack?.id === item.id && <Check size={17} />}
                            </div>
                            <strong>
                              {points(item.credits, zh)}{" "}
                              <small>{zh ? "积分" : "points"}</small>
                            </strong>
                            {item.description && <p>{item.description}</p>}
                            <span className="billing-pack-price">
                              {money(item.amount_minor, item.currency, zh)}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                    {kind === "credits" && paymentControls}
                  </>
                ) : (
                  <p className="billing-empty">
                    {kind === "membership"
                      ? zh
                        ? "暂时没有在售会员套餐"
                        : "No membership plans are available yet"
                      : zh
                        ? "暂时没有在售积分包"
                        : "No points packs are available yet"}
                  </p>
                )}
              </section>
            )}
            {error && !orderId && !checkoutOpen && (
              <BillingError error={error} zh={zh} />
            )}
          </>
        )}
        {!preview && section === "orders" && (
          <>
            <section className="billing-section">
              <div className="billing-section-heading">
                <h2>{zh ? "付款订单" : "Payment orders"}</h2>
                <span>{orders.data?.length ?? 0}</span>
              </div>
              {orders.error && (
                <BillingError
                  error={orders.error}
                  retry={() => void orders.refetch()}
                  zh={zh}
                />
              )}
              {orders.isLoading && (
                <p className="billing-empty" role="status">
                  {zh ? "正在加载订单…" : "Loading orders…"}
                </p>
              )}
              {orders.data?.length === 0 && (
                <p className="billing-empty">
                  {zh ? "还没有付款订单" : "No payment orders yet"}
                </p>
              )}
              <div className="billing-orders">
                {orders.data?.map((item) => (
                  <button
                    className="billing-order-row"
                    key={item.id}
                    onClick={() => selectOrder(item.id)}
                  >
                    <div>
                      <strong>{item.pack_name}</strong>
                      <small>
                        {item.type === "membership"
                          ? zh
                            ? "会员"
                            : "Membership"
                          : zh
                            ? "积分"
                            : "Points"}{" "}
                        · {billingDate(item.created_at, zh)} ·{" "}
                        {item.provider_name}
                      </small>
                    </div>
                    <div>
                      <strong>
                        {money(item.amount_minor, item.currency, zh)}
                      </strong>
                      <span className={`billing-status is-${item.status}`}>
                        {paymentOrderLabel(item, zh)}
                      </span>
                      {item.refunds.length > 0 && (
                        <small>{refundLabel(item.refunds[0].status, zh)}</small>
                      )}
                    </div>
                    <ArrowRight size={16} />
                  </button>
                ))}
              </div>
              {orders.data && (offset > 0 || orders.data.length >= 50) && (
                <div className="billing-pagination">
                  <Button
                    variant="outline"
                    disabled={offset === 0 || orders.isFetching}
                    onClick={() => setOffset(Math.max(0, offset - 50))}
                  >
                    {zh ? "上一页" : "Previous"}
                  </Button>
                  <span>
                    {zh
                      ? `第 ${Math.floor(offset / 50) + 1} 页`
                      : `Page ${Math.floor(offset / 50) + 1}`}
                  </span>
                  <Button
                    variant="outline"
                    disabled={orders.data.length < 50 || orders.isFetching}
                    onClick={() => setOffset(offset + 50)}
                  >
                    {zh ? "下一页" : "Next"}
                  </Button>
                </div>
              )}
            </section>
            <section className="billing-section">
              <div className="billing-section-heading">
                <h2>{zh ? "积分记录" : "Points activity"}</h2>
                <span>{zh ? "最近 50 条" : "Latest 50 entries"}</span>
              </div>
              {wallet.data?.entries.length === 0 && (
                <p className="billing-empty">
                  {zh ? "还没有积分变动记录" : "No points activity yet"}
                </p>
              )}
              <div className="billing-ledger">
                {wallet.data?.entries.map((entry) => (
                  <div key={entry.id}>
                    <div>
                      <strong>
                        {entryNames[entry.kind]?.[zh ? 0 : 1] ??
                          (zh ? "积分变动" : "Points update")}
                      </strong>
                      <small>{billingDate(entry.created_at, zh)}</small>
                    </div>
                    <div>
                      <strong>
                        {entry.available_delta > 0 ? "+" : ""}
                        {points(entry.available_delta, zh)}
                      </strong>
                      {entry.reserved_delta !== 0 && (
                        <small>
                          {zh ? "预留 " : "Reserved "}
                          {entry.reserved_delta > 0 ? "+" : ""}
                          {points(entry.reserved_delta, zh)}
                        </small>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </main>
      <Modal
        open={checkoutOpen && (preview || !orderId) && !!product}
        onClose={() => {
          if (!busy) setCheckoutOpen(false);
        }}
        title={
          preview
            ? zh
              ? "套餐预览"
              : "Plan preview"
            : zh
              ? "确认付款"
              : "Confirm payment"
        }
        width={560}
      >
        {product && (
          <div className="billing-confirm-order">
            <h2>{product.name}</h2>
            <dl className="billing-details">
              {plan && (
                <div>
                  <dt>{zh ? "购买期限" : "Purchased term"}</dt>
                  <dd>{membershipPurchaseDuration(plan, zh)}</dd>
                </div>
              )}
              <div>
                <dt>{zh ? "期内积分额度" : "Term allowance"}</dt>
                <dd>{points(product.credits, zh)}</dd>
              </div>
              {preview && (
                <div>
                  <dt>{zh ? "套餐价格" : "Plan price"}</dt>
                  <dd>{money(product.amount_minor, product.currency, zh)}</dd>
                </div>
              )}
            </dl>
            <p className="billing-muted">
              {zh
                ? "一次支付本期费用，积分整期一次发放，到期失效。续购同一套餐时，下一期从当前到期日开始。"
                : "Pay for the full term once. All term points are granted together and expire at the end of the term. Renewing the same plan starts the next term when the current one ends."}
            </p>
            {preview ? (
              <div className="billing-actions">
                <Button
                  variant="outline"
                  onClick={() => setCheckoutOpen(false)}
                >
                  {zh ? "关闭" : "Close"}
                </Button>
              </div>
            ) : (
              <>
                {error && <BillingError error={error} zh={zh} />}
                {paymentControls}
              </>
            )}
          </div>
        )}
      </Modal>
      <Modal
        open={!!orderId && !preview}
        onClose={() => selectOrder(null)}
        title={zh ? "订单详情" : "Order details"}
        width={560}
      >
        {order.isLoading && (
          <p role="status">{zh ? "正在加载订单…" : "Loading order…"}</p>
        )}
        {order.error && (
          <BillingError
            error={order.error}
            retry={() => void order.refetch()}
            zh={zh}
          />
        )}
        {error && <BillingError error={error} zh={zh} />}
        {selectedOrder && (
          <BillingOrderDetail
            order={selectedOrder}
            zh={zh}
            busy={checking}
            onCheck={() => void checkOrder()}
            checkout
          >
            {selectedOrder.status === "pending" &&
              selectedOrder.cancel_allowed && (
                <Button
                  variant="ghost"
                  disabled={checking}
                  onClick={() => void cancelOrder()}
                >
                  {zh ? "取消订单" : "Cancel order"}
                </Button>
              )}
            {canRefund && (
              <Button
                variant="ghost"
                onClick={() => {
                  setRefundOpen(true);
                  setRefundError("");
                }}
              >
                {zh ? "申请退款" : "Request refund"}
              </Button>
            )}
          </BillingOrderDetail>
        )}
        {refundOpen && selectedOrder && (
          <form
            className="billing-refund-form"
            onSubmit={(e) => {
              e.preventDefault();
              void requestRefund();
            }}
          >
            <h3>{zh ? "申请退款" : "Request refund"}</h3>
            <p>
              {selectedOrder.type === "membership"
                ? zh
                  ? "仅未使用的会员套餐可申请全额退款。审核通过后将收回该笔订单的会员权益。"
                  : "Only an unused membership is eligible for a full refund. Its membership benefits will be revoked after approval."
                : zh
                  ? "提交后由管理员审核，退款将扣回对应积分。"
                  : "An administrator will review your request. The corresponding points will be deducted."}
            </p>
            <label>
              {zh ? "退款原因" : "Reason"}
              <textarea
                autoFocus
                required
                maxLength={500}
                value={reason}
                disabled={refundBusy}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            {refundError && <BillingError error={refundError} zh={zh} />}
            <div className="billing-actions">
              <Button
                type="button"
                variant="ghost"
                disabled={refundBusy}
                onClick={() => setRefundOpen(false)}
              >
                {zh ? "取消" : "Cancel"}
              </Button>
              <Button disabled={refundBusy || !reason.trim()}>
                {refundBusy
                  ? zh
                    ? "提交中…"
                    : "Submitting…"
                  : zh
                    ? "提交退款申请"
                    : "Submit refund request"}
              </Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}

export function BillingError({
  error,
  retry,
  zh = true,
}: {
  error: unknown;
  retry?: () => void;
  zh?: boolean;
}) {
  const text = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="billing-error">
      <span>{text}</span>
      {retry && (
        <Button variant="outline" onClick={retry}>
          {zh ? "重试" : "Retry"}
        </Button>
      )}
    </div>
  );
}
