import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(
  new URL("../src/lib/billing.ts", import.meta.url),
  "utf8",
);
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ES2022,
  },
});
const {
  parseMinor,
  points,
  money,
  currencyDecimals,
  checkoutLink,
  qrImage,
  orderLabel,
  providerConfigDefaults,
  paymentOrderLabel,
  membershipDuration,
  membershipAnnualPricing,
  membershipPeriod,
  membershipLabel,
  billingPaymentQuote,
} = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

test("membership conversion uses the server quote without changing direct prices or points packs", () => {
  const plan = {
    currency: "usd", amount_minor: 2000,
    payment_amounts: { usd: 9999, cny: 14488, eur: 1800 },
  };
  assert.deepEqual(billingPaymentQuote(plan, { currency: "usd" }, true), {
    amount_minor: 2000, currency: "usd", converted: false,
  });
  assert.deepEqual(billingPaymentQuote(plan, { currency: "cny" }, true), {
    amount_minor: 14488, currency: "cny", converted: true,
  });
  assert.equal(billingPaymentQuote(plan, { currency: "cny" }), null);
  assert.equal(billingPaymentQuote(plan, { currency: "eur" }, true), null);
  assert.equal(billingPaymentQuote({ ...plan, currency: "eur" }, { currency: "cny" }, true), null);
  for (const amount of [undefined, null, 0, -1, 123.45, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(billingPaymentQuote({ ...plan, payment_amounts: { cny: amount } }, { currency: "cny" }, true), null);
  }
  assert.equal(billingPaymentQuote({ currency: "usd", amount_minor: 2000 }, { currency: "cny" }, true), null);
  assert.equal(billingPaymentQuote(undefined, { currency: "cny" }, true), null);
  assert.equal(billingPaymentQuote(plan, undefined, true), null);
});

test("membership period groups the configured term and preserves other durations", () => {
  assert.equal(membershipPeriod({ duration: 1, duration_unit: "month" }), "monthly");
  assert.equal(membershipPeriod({ duration: 30, duration_unit: "day" }), "monthly");
  assert.equal(membershipPeriod({ duration: 12, duration_unit: "month" }), "yearly");
  assert.equal(membershipPeriod({ duration: 365, duration_unit: "day" }), "yearly");
  assert.equal(membershipPeriod({ duration: 2, duration_unit: "week" }), "other");
  assert.equal(membershipPeriod({ duration: 3, duration_unit: "month" }), "other");
});

test("annual savings use a linked and comparable monthly offer", () => {
  const monthly = { id: "monthly", active: true, currency: "usd", duration: 1,
    duration_unit: "month", credits: 60000, amount_minor: 2000 };
  const annual = { ...monthly, id: "annual", duration: 12, monthly_plan_id: monthly.id,
    credits: 720000, amount_minor: 19200 };
  assert.deepEqual(membershipAnnualPricing(annual, [monthly]), {
    monthlyEquivalentMinor: 1600,
    savingsPercent: 20,
    monthlyYearMinor: 24000,
  });
  for (const invalid of [
    { ...monthly, duration: 2 },
    { ...monthly, currency: "cny" },
    { ...monthly, active: false },
    { ...monthly, credits: 60001 },
    { ...monthly, id: "unlinked" },
  ]) {
    assert.deepEqual(membershipAnnualPricing(annual, [invalid]), {
      monthlyEquivalentMinor: 1600, savingsPercent: null, monthlyYearMinor: null,
    });
  }
  assert.equal(membershipAnnualPricing(annual, [{ ...monthly, active: false }], true).savingsPercent, 20);
  assert.equal(membershipAnnualPricing({ ...annual, amount_minor: 24000 }, [monthly]).savingsPercent, null);
  assert.equal(membershipAnnualPricing({ ...annual, amount_minor: 23900 }, [monthly]).savingsPercent, null);
  assert.equal(membershipAnnualPricing({ ...annual, duration: 365, duration_unit: "day" }, [monthly]), null);
});

test("provider defaults retain numeric zero and only apply to the selected provider", () => {
  const provider = {
    kind: "easypay",
    fields: [
      { key: "refund_success_code", required: true, default: 0 },
      {
        key: "payment_mode",
        required: true,
        options: [
          { value: "qrcode", label: "扫码" },
          { value: "popup", label: "收银台" },
        ],
      },
      { key: "payment_type", required: true, default: "alipay" },
      { key: "pkey", required: true, secret: true },
      { key: "channel_id", required: false },
    ],
  };
  assert.deepEqual(providerConfigDefaults(provider), {
    refund_success_code: "0",
    payment_mode: "qrcode",
    payment_type: "alipay",
  });
  assert.deepEqual(
    providerConfigDefaults({
      kind: "stripe",
      fields: [{ key: "secret_key", required: true, secret: true }],
    }),
    {},
  );
  assert.deepEqual(providerConfigDefaults(), {});
});

test("points and payment minor units remain distinct and reject silent rounding", () => {
  assert.equal(points(1200), "12");
  assert.equal(points(1255, false), "12.55");
  assert.equal(parseMinor("12.34"), 1234);
  assert.equal(parseMinor("0.01"), 1);
  for (const value of [
    "12.345",
    "1e3",
    "-1",
    "0",
    "NaN",
    "Infinity",
    "9007199254740992",
  ])
    assert.equal(parseMinor(value), null, value);
  assert.equal(currencyDecimals("JPY"), 0);
  assert.equal(parseMinor("1200", 0), 1200);
  assert.equal(parseMinor("12.1", 0), null);
  assert.match(money(1200, "jpy", false), /1,200/);
  assert.match(money(1200, "usd", false), /12\.00/);
});

test("checkout surfaces reject unsafe protocols and non-PNG image payloads", () => {
  assert.equal(
    checkoutLink("https://checkout.example.test/session/123"),
    "https://checkout.example.test/session/123",
  );
  for (const url of [
    "javascript:alert(1)",
    "data:text/html,hi",
    "http://example.test",
    "https://user:secret@example.test",
    "/relative",
  ])
    assert.equal(checkoutLink(url), null);
  assert.equal(
    qrImage("data:image/png;base64,aGVsbG8="),
    "data:image/png;base64,aGVsbG8=",
  );
  for (const url of [
    "data:image/svg+xml,<svg/>",
    "https://example.test/qr.png",
    "javascript:bad",
  ])
    assert.equal(qrImage(url), null);
});

test("paid status never claims points have arrived", () => {
  assert.match(orderLabel("paid"), /处理中/);
  assert.equal(orderLabel("completed"), "积分已到账");
  assert.notEqual(orderLabel("paid", false), orderLabel("completed", false));
});

test("membership activation, scheduling and expiration never claim permanent points were credited", () => {
  assert.equal(
    membershipDuration({ duration: 1, duration_unit: "month" }),
    "1 个月",
  );
  assert.equal(
    membershipDuration({ duration: 2, duration_unit: "week" }, false),
    "2 weeks",
  );
  assert.equal(paymentOrderLabel({ status: "completed" }), "积分已到账");
  assert.match(
    paymentOrderLabel({ type: "membership", status: "paid" }),
    /权益开通中/,
  );
  for (const status of [
    "active",
    "scheduled",
    "expired",
    "refund_pending",
    "refunded",
  ]) {
    const order = {
      type: "membership",
      status: "completed",
      membership: { status },
    };
    assert.doesNotMatch(paymentOrderLabel(order), /积分已到账/);
    assert.doesNotMatch(paymentOrderLabel(order, false), /Points credited/);
    if (status !== "active")
      assert.equal(paymentOrderLabel(order), membershipLabel(status));
  }
});
