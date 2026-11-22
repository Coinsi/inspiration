import { useEffect, useState, type ReactNode } from "react";
import { CheckCircle2, Clock3, ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  billingDate,
  checkoutLink,
  money,
  paymentOrderLabel,
  points,
  qrImage,
  refundLabel,
  type PaymentOrder,
} from "@/lib/billing";

export function BillingOrderDetail({
  order,
  zh = true,
  busy,
  onCheck,
  children,
  checkout = false,
}: {
  order: PaymentOrder;
  zh?: boolean;
  busy?: boolean;
  onCheck: () => void;
  children?: ReactNode;
  checkout?: boolean;
}) {
  const link = checkoutLink(order.checkout_url);
  const qr = qrImage(order.qr_image_url);
  const pending = order.status === "pending";
  const membership = order.type === "membership";
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!pending || !checkout) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [pending, checkout, order.id]);
  const remaining = Math.max(
    0,
    Math.ceil((Date.parse(order.expires_at) - now) / 1000),
  );
  const label =
    order.refunded_minor >= order.amount_minor
      ? zh
        ? "已全额退款"
        : "Fully refunded"
      : paymentOrderLabel(order, zh);
  const retryEntry = checkout && pending && remaining > 0 && !qr && !link;
  let checkLabel = zh ? "查询付款结果" : "Check payment";
  if (retryEntry) checkLabel = zh ? "重试获取付款入口" : "Retry payment entry";
  if (busy) checkLabel = zh ? "查询中…" : "Checking…";
  const checkAction = (
    <Button variant="outline" disabled={busy} onClick={onCheck}>
      <RefreshCw size={15} className={busy ? "animate-spin" : ""} />
      {checkLabel}
    </Button>
  );
  return (
    <div className="billing-order-detail">
      <div className={`billing-order-state is-${order.status}`} role="status">
        {order.status === "completed" ? (
          <CheckCircle2 size={22} />
        ) : (
          <Clock3 size={22} />
        )}
        <div>
          <strong>{label}</strong>
          {order.status === "paid" && (
            <p>
              {membership
                ? zh
                  ? "付款已确认，会员权益开通后会自动更新。请勿重复付款。"
                  : "Payment confirmed. Membership activation will update automatically. Do not pay again."
                : zh
                  ? "付款已确认，积分到账后会自动更新。请勿重复付款。"
                  : "Payment confirmed. Points will update automatically. Do not pay again."}
            </p>
          )}
          {pending && (
            <p>
              {checkout
                ? zh
                  ? "付款完成后会自动更新，也可手动查询。"
                  : "The status updates after payment. You can also check manually."
                : zh
                  ? "付款人完成付款后，可查询最新状态。"
                  : "Check the latest status after the buyer completes payment."}
            </p>
          )}
          {order.status === "failed" && (
            <p>
              {zh
                ? "请先查询订单状态；如已扣款，请保留订单号联系管理员。"
                : "Check the order first. If charged, contact the administrator with this order ID."}
            </p>
          )}
          {order.status === "expired" && (
            <p>
              {zh
                ? "请勿继续支付此订单。如已扣款，请查询付款结果。"
                : "Do not pay this expired order. If charged, check its payment status."}
            </p>
          )}
          {order.status === "cancelled" && order.cancel_mode === "local" && (
            <p>
              {zh
                ? "已停止在本站继续付款。已有支付链接可能仍有效，请勿继续支付；已扣款请查询付款结果。"
                : "Payment on this site has stopped for this order. Existing payment links may still work; do not use them. If charged, check the payment status."}
            </p>
          )}
        </div>
      </div>
      {checkout && pending && remaining > 0 && (
        <div className="billing-checkout">
          {qr && (
            <img
              className="billing-qr"
              src={qr}
              alt={zh ? "此订单的付款二维码" : "Payment QR code for this order"}
            />
          )}
          <p>
            {zh ? "剩余付款时间 " : "Time remaining "}
            {Math.floor(remaining / 60)}:
            {String(remaining % 60).padStart(2, "0")}
          </p>
          {link && (
            <a
              className="billing-checkout-link"
              href={link}
              target="_blank"
              rel="noopener noreferrer"
            >
              {zh ? "打开收银台" : "Open checkout"}
              <ExternalLink size={16} />
            </a>
          )}
          {retryEntry && (
            <>
              <p>
                {zh
                  ? "暂未获取到付款入口，可重试获取原订单的付款入口。已扣款请先查询付款结果。"
                  : "The payment link or QR code is not available yet. Retry this order; if you have been charged, check its payment status first."}
              </p>
              <div className="billing-actions">
                {checkAction}
                {children}
              </div>
            </>
          )}
          {qr && (
            <p>
              {zh
                ? "使用对应支付应用扫描二维码"
                : "Scan with the selected payment app"}
            </p>
          )}
        </div>
      )}
      {checkout && pending && remaining === 0 && (
        <p className="billing-notice">
          {zh
            ? "付款时间已结束，请勿继续支付。可查询订单确认最终结果。"
            : "The payment window has ended. Do not pay this order; check its final status."}
        </p>
      )}
      <dl className="billing-details">
        <div>
          <dt>
            {membership
              ? zh
                ? "会员套餐"
                : "Membership plan"
              : zh
                ? "积分包"
                : "Points pack"}
          </dt>
          <dd>{order.pack_name}</dd>
        </div>
        <div>
          <dt>{zh ? "付款金额" : "Payment"}</dt>
          <dd>{money(order.amount_minor, order.currency, zh)}</dd>
        </div>
        {order.conversion && (
          <>
            <div>
              <dt>{zh ? "套餐标价" : "Plan list price"}</dt>
              <dd>
                {money(
                  order.conversion.original_amount_minor,
                  order.conversion.original_currency,
                  zh,
                )}
              </dd>
            </div>
            <div>
              <dt>{zh ? "下单时换算比例" : "Rate at checkout"}</dt>
              <dd>1 USD = {order.conversion.rate} CNY</dd>
            </div>
          </>
        )}
        <div>
          <dt>
            {membership
              ? zh
                ? "期内积分额度"
                : "Term allowance"
              : zh
                ? "购买积分"
                : "Points purchased"}
          </dt>
          <dd>{points(order.credits, zh)}</dd>
        </div>
        {membership && order.membership_days != null && (
          <div>
            <dt>{zh ? "购买期限" : "Purchased term"}</dt>
            <dd>
              {order.membership_days} {zh ? "天" : "days"}
            </dd>
          </div>
        )}
        {membership && order.membership && (
          <>
            <div>
              <dt>{zh ? "生效时间" : "Starts"}</dt>
              <dd>{billingDate(order.membership.starts_at, zh)}</dd>
            </div>
            <div>
              <dt>{zh ? "到期时间" : "Expires"}</dt>
              <dd>{billingDate(order.membership.expires_at, zh)}</dd>
            </div>
          </>
        )}
        <div>
          <dt>{zh ? "支付方式" : "Payment method"}</dt>
          <dd>{order.provider_name}</dd>
        </div>
        <div>
          <dt>{zh ? "创建时间" : "Created"}</dt>
          <dd>{billingDate(order.created_at, zh)}</dd>
        </div>
        {pending && (
          <div>
            <dt>{zh ? "付款截止" : "Pay before"}</dt>
            <dd>{billingDate(order.expires_at, zh)}</dd>
          </div>
        )}
        <div className="billing-details-wide">
          <dt>{zh ? "订单号" : "Order ID"}</dt>
          <dd className="billing-id">{order.id}</dd>
        </div>
      </dl>
      {!!order.refunded_minor && (
        <p className="billing-muted">
          {zh ? "累计已退款：" : "Total refunded: "}
          {money(order.refunded_minor, order.currency, zh)}
        </p>
      )}
      {!!order.refunds.length && (
        <section className="billing-refund-history">
          <h3>{zh ? "退款记录" : "Refund history"}</h3>
          {order.refunds.map((refund) => (
            <div key={refund.id}>
              <div className="billing-row">
                <strong>{refundLabel(refund.status, zh)}</strong>
                <span>{money(refund.amount_minor, order.currency, zh)}</span>
              </div>
              <p>{refund.reason}</p>
              <small>{billingDate(refund.created_at, zh)}</small>
            </div>
          ))}
        </section>
      )}
      {!retryEntry && (
        <div className="billing-actions">
          {checkAction}
          {children}
        </div>
      )}
    </div>
  );
}
