export interface CreditPack {
  id: string;
  name: string;
  description: string;
  currency: string;
  amount_minor: number;
  credits: number;
  active: boolean;
}

export interface PaymentProvider {
  id: string;
  name: string;
  kind: string;
  currency: string;
  active: boolean;
  allow_refunds?: boolean;
}

export interface BillingCatalog {
  enabled: boolean;
  packs: CreditPack[];
  providers: PaymentProvider[];
}

export interface CreditWallet {
  available: number;
  reserved: number;
  entries: {
    id: string;
    kind: string;
    available_delta: number;
    reserved_delta: number;
    created_at: string;
  }[];
}

export interface PaymentRefund {
  id: string;
  status: "requested" | "pending" | "succeeded" | "failed" | "rejected";
  amount_minor: number;
  credits: number;
  reason: string;
  created_at: string;
}

export interface PaymentOrder {
  id: string;
  status: "pending" | "paid" | "completed" | "expired" | "cancelled" | "failed";
  pack_name: string;
  provider_name: string;
  currency: string;
  amount_minor: number;
  credits: number;
  checkout_url?: string | null;
  qr_payload?: string | null;
  qr_image_url?: string | null;
  created_at: string;
  expires_at: string;
  paid_at?: string | null;
  refunded_minor: number;
  refund_allowed?: boolean;
  cancel_allowed?: boolean;
  refunds: PaymentRefund[];
  user_id?: string;
  user_email?: string;
}

export interface BillingSettings {
  enabled: boolean;
  payment_required: boolean;
  public_origin: string;
  checkout_minutes: number;
}

export interface PaymentProviderType {
  kind: string;
  name: string;
  fields: { key: string; label: string; secret: boolean; required: boolean }[];
}

export const billingCurrencies = [
  "cny",
  "usd",
  "eur",
  "gbp",
  "hkd",
  "sgd",
  "jpy",
];
export const currencyDecimals = (currency: string) =>
  currency.toLowerCase() === "jpy" ? 0 : 2;
export const points = (hundredths: number, zh = true) =>
  (hundredths / 100).toLocaleString(zh ? "zh-CN" : "en-US", {
    maximumFractionDigits: 2,
  });
export function money(minor: number, currency: string, zh = true) {
  return new Intl.NumberFormat(zh ? "zh-CN" : "en-US", {
    style: "currency",
    currency: currency.toUpperCase(),
    minimumFractionDigits: currencyDecimals(currency),
    maximumFractionDigits: currencyDecimals(currency),
  }).format(minor / 10 ** currencyDecimals(currency));
}
export const decimalAmount = (minor: number, decimals = 2) =>
  (minor / 10 ** decimals).toFixed(decimals);

/** Parse decimal input without rounding away an extra digit or accepting exponent notation. */
export function parseMinor(input: string, decimals = 2): number | null {
  const value = input.trim();
  const pattern = decimals
    ? new RegExp(`^\\d+(?:\\.\\d{1,${decimals}})?$`)
    : /^\d+$/;
  if (!pattern.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  const result =
    Number(whole) * 10 ** decimals + Number(fraction.padEnd(decimals, "0"));
  return Number.isSafeInteger(result) && result > 0 ? result : null;
}

export function checkoutLink(value?: string | null): string | null {
  try {
    const url = new URL(value || "");
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

export function qrImage(value?: string | null): string | null {
  return value && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(value)
    ? value
    : null;
}

export const billingDate = (value: string, zh = true) =>
  new Date(value).toLocaleString(zh ? "zh-CN" : "en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

export const orderLabel = (status: PaymentOrder["status"], zh = true) =>
  ({
    pending: zh ? "待付款" : "Awaiting payment",
    paid: zh ? "已付款 · 到账处理中" : "Paid · crediting points",
    completed: zh ? "积分已到账" : "Points credited",
    expired: zh ? "已过期" : "Expired",
    cancelled: zh ? "已取消" : "Cancelled",
    failed: zh ? "处理失败" : "Failed",
  })[status] || status;

export const refundLabel = (status: PaymentRefund["status"], zh = true) =>
  ({
    requested: zh ? "退款待审核" : "Refund awaiting review",
    pending: zh ? "退款处理中" : "Refund processing",
    succeeded: zh ? "已退款" : "Refunded",
    failed: zh ? "退款失败" : "Refund failed",
    rejected: zh ? "退款申请已拒绝" : "Refund request declined",
  })[status] || status;
