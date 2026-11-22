import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.APP_URL || "http://127.0.0.1:5183";
assert.match(new URL(base).hostname, /^(127\.0\.0\.1|localhost)$/);
const output = resolve(
  process.env.ARTIFACT_DIR || "test-results/billing-browser",
);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const checks = [];
const pageErrors = [];
const unknown = [];
const now = new Date().toISOString();
const orderBase = {
  id: "order-existing",
  status: "completed",
  pack_name: "创作补给",
  provider_name: "微信支付",
  currency: "cny",
  amount_minor: 2800,
  credits: 12000,
  created_at: now,
  expires_at: new Date(Date.now() + 3600000).toISOString(),
  paid_at: now,
  refunded_minor: 0,
  refunds: [],
  refund_allowed: true,
  user_id: "billing-test",
  user_email: "creator@example.test",
};

async function fixture({
  width = 1440,
  height = 900,
  theme = "dark",
  role = "super",
  enabled = true,
  lang = "zh",
} = {}) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 1,
  });
  const requests = [],
    createBodies = [],
    refundBodies = [],
    packBodies = [],
    providerBodies = [];
  const state = {
    settings: {
      enabled,
      payment_required: false,
      public_origin: "https://example.test",
      checkout_minutes: 60,
    },
    packs: [
      {
        id: "pack-1",
        name: "创作补给",
        description: "为下一段创作补充积分",
        currency: "cny",
        amount_minor: 2800,
        credits: 12000,
        active: true,
      },
      {
        id: "pack-2",
        name: "持续创作",
        description: "适合持续制作与反复打磨",
        currency: "cny",
        amount_minor: 9800,
        credits: 48000,
        active: true,
      },
      {
        id: "pack-3",
        name: "美元积分包",
        description: "以美元结算",
        currency: "usd",
        amount_minor: 1200,
        credits: 26000,
        active: true,
      },
    ],
    providers: [
      {
        id: "wx-1",
        name: "微信支付",
        kind: "wechatpay",
        currency: "cny",
        active: true,
        allow_refunds: true,
      },
      {
        id: "stripe-1",
        name: "银行卡",
        kind: "stripe",
        currency: "usd",
        active: true,
        allow_refunds: true,
      },
    ],
    orders: [structuredClone(orderBase)],
    available: 12000,
    failCreate: false,
    failRefund: false,
    failAdminRefund: false,
    failProvider: false,
    failResolution: false,
    refundResolutions: [],
    reservations: [
      {
        job_id: "reservation-test",
        user_id: "billing-test",
        units: 1250,
        job_status: "failed",
        updated_at: now,
      },
    ],
    resolves: [],
    plans: [],
    memberships: [],
    planBodies: [],
    failPlan: false,
    previewStatus: 200,
  };
  await context.addInitScript(
    ({ theme, lang }) => {
      localStorage.setItem("inspiration_token", "billing-fixture-token");
      localStorage.setItem("inspiration_lang", lang);
      localStorage.setItem("inspiration_theme", theme);
    },
    { theme, lang },
  );
  await context.route("**/*", async (route) => {
    const req = route.request(),
      url = new URL(req.url());
    if (url.origin !== new URL(base).origin) return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname.replace("/api/v1", ""),
      method = req.method();
    requests.push(`${method} ${path}`);
    const send = (json, status = 200) => route.fulfill({ status, json });
    const body = () => req.postDataJSON();
    if (path === "/me")
      return send({
        user: {
          id: "billing-test",
          username: "creator",
          display_name: "创作者",
          email: "creator@example.test",
          is_active: true,
          is_platform_admin: role !== "user",
          platform_role: role === "user" ? null : role,
        },
        memberships: [],
      });
    if (path === "/billing/catalog")
      return send({
        enabled: state.settings.enabled,
        packs: state.packs,
        plans: state.plans,
        providers: state.providers,
      });
    if (
      path === "/billing/memberships" ||
      path === "/admin/billing/memberships"
    ) {
      const offset = Number(url.searchParams.get("offset") || 0);
      const limit = Number(url.searchParams.get("limit") || 100);
      return send(state.memberships.slice(offset, offset + limit));
    }
    if (path === "/billing/wallet")
      return send({
        available: state.available,
        reserved: 2500,
        entries: [
          {
            id: "ledger-1",
            kind: "purchase",
            available_delta: 12000,
            reserved_delta: 0,
            created_at: now,
          },
          {
            id: "ledger-2",
            kind: "reserve",
            available_delta: -2500,
            reserved_delta: 2500,
            created_at: now,
          },
        ],
      });
    if (path === "/billing/orders" && method === "GET")
      return send(state.orders);
    if (path === "/billing/orders" && method === "POST") {
      createBodies.push(body());
      if (state.failCreate) {
        state.failCreate = false;
        return send(
          {
            error: {
              code: "UPSTREAM_UNAVAILABLE",
              message: "支付服务暂不可用，请重试",
            },
          },
          502,
        );
      }
      const order = {
        ...structuredClone(orderBase),
        id: "order-created",
        status: "pending",
        paid_at: null,
        cancel_allowed: true,
        checkout_url: "https://checkout.example.test/pay",
      };
      const product = body().plan_id
        ? state.plans.find((plan) => plan.id === body().plan_id)
        : state.packs.find((pack) => pack.id === body().pack_id);
      assert.ok(
        product,
        "checkout must reference the selected catalog product",
      );
      const provider = state.providers.find(({ id }) => id === body().provider_id);
      const conversion = provider && provider.currency !== product.currency;
      const amount = conversion
        ? product.payment_amounts?.[provider.currency]
        : product.amount_minor;
      if (conversion && (amount == null || body().expected_amount_minor !== amount))
        return send({ error: { code: "PAYMENT_QUOTE_CHANGED" } }, 409);
      Object.assign(order, {
        pack_name: product.name,
        currency: provider?.currency ?? product.currency,
        amount_minor: amount,
        credits: product.credits,
        provider_name: provider?.name,
        type: body().plan_id ? "membership" : "credits",
      });
      if (conversion)
        order.conversion = {
          original_amount_minor: product.amount_minor,
          original_currency: product.currency,
          rate: state.settings.subscription_usd_to_cny_rate,
        };
      if (body().plan_id) {
        order.plan_id = product.id;
        order.membership_days =
          product.duration *
          { day: 1, week: 7, month: 30 }[product.duration_unit];
      }
      state.orders.unshift(order);
      return send(order);
    }
    const userOrder = path.match(
      /^\/billing\/orders\/([^/]+)(?:\/(check|refund-request|cancel))?$/,
    );
    if (userOrder) {
      const order = state.orders.find((o) => o.id === userOrder[1]);
      if (!order)
        return send(
          { error: { code: "NOT_FOUND", message: "订单不存在" } },
          404,
        );
      if (userOrder[2] === "check") {
        order.status = "paid";
        return send(order);
      }
      if (userOrder[2] === "cancel") {
        order.status = "cancelled";
        return send(order);
      }
      if (userOrder[2] === "refund-request") {
        refundBodies.push(body());
        if (state.failRefund) {
          state.failRefund = false;
          return send({ error: { message: "申请暂未提交，请重试" } }, 500);
        }
        order.refunds.push({
          id: "refund-request",
          status: "requested",
          amount_minor: order.amount_minor,
          credits: order.credits,
          reason: body().reason,
          created_at: now,
        });
      }
      return send(order);
    }
    if (path === "/admin/billing/provider-types")
      return send([
        {
          kind: "easypay",
          name: "易支付（V1）",
          fields: [
            {
              key: "api_base",
              label: "易支付接口地址",
              secret: false,
              required: true,
            },
            { key: "pid", label: "商户 PID", secret: false, required: true },
            { key: "pkey", label: "商户密钥", secret: true, required: true },
            {
              key: "payment_type",
              label: "支付方式",
              secret: false,
              required: true,
              default: "alipay",
              options: [
                { value: "alipay", label: "支付宝" },
                { value: "wxpay", label: "微信支付" },
              ],
            },
            {
              key: "payment_mode",
              label: "付款入口",
              secret: false,
              required: true,
              default: "qrcode",
              options: [
                { value: "qrcode", label: "二维码" },
                { value: "popup", label: "收银台" },
              ],
            },
            {
              key: "channel_id",
              label: "通道 ID",
              secret: false,
              required: false,
            },
            {
              key: "refund_success_code",
              label: "退款成功返回码",
              secret: false,
              required: false,
              default: "1",
              options: [
                { value: "1", label: "1" },
                { value: "0", label: "0" },
              ],
            },
          ],
        },
        {
          kind: "wechatpay",
          name: "微信支付 Native",
          fields: [
            { key: "app_id", label: "App ID", secret: false, required: true },
            {
              key: "mch_id",
              label: "微信商户号",
              secret: false,
              required: true,
            },
            {
              key: "merchant_serial",
              label: "商户 API 证书序列号",
              secret: false,
              required: true,
            },
            {
              key: "merchant_private_key",
              label: "商户 API 私钥 PEM",
              secret: true,
              required: true,
            },
            {
              key: "platform_public_key_id",
              label: "微信支付公钥 ID",
              secret: false,
              required: true,
            },
            {
              key: "platform_public_key",
              label: "微信支付公钥 PEM",
              secret: false,
              required: true,
            },
            {
              key: "api_v3_key",
              label: "API v3 密钥",
              secret: true,
              required: true,
            },
          ],
        },
        {
          kind: "stripe",
          name: "Stripe",
          fields: [
            {
              key: "secret_key",
              label: "Secret Key",
              secret: true,
              required: true,
            },
            {
              key: "webhook_secret",
              label: "Webhook Secret",
              secret: true,
              required: true,
            },
          ],
        },
      ]);
    if (path === "/admin/billing/settings") {
      if (method === "PATCH") Object.assign(state.settings, body());
      return send(state.settings);
    }
    if (path === "/admin/billing/orders") return send(state.orders);
    if (
      path === "/admin/billing/plans" ||
      path.startsWith("/admin/billing/plans/")
    ) {
      if (method === "GET") {
        if (state.previewStatus !== 200)
          return send(
            { error: { message: "无权预览会员套餐" } },
            state.previewStatus,
          );
        return send(state.plans);
      }
      state.planBodies.push(body());
      if (state.failPlan) {
        state.failPlan = false;
        return send({ error: { message: "套餐暂未保存，请重试" } }, 502);
      }
      if (method === "POST")
        state.plans.push({ id: `plan-${state.planBodies.length}`, ...body() });
      else
        Object.assign(
          state.plans.find((plan) => plan.id === path.split("/").at(-1)),
          body(),
        );
      return send(state.plans.at(-1));
    }
    if (path === "/admin/billing/reservations") return send(state.reservations);
    if (path.match(/^\/admin\/billing\/reservations\/[^/]+\/resolve$/)) {
      state.resolves.push(body());
      state.reservations = [];
      return send({ status: "settled" });
    }
    if (path === "/admin/billing/packs") {
      if (method === "POST") {
        packBodies.push(body());
        state.packs.push({ id: "new-pack", ...body() });
      }
      return send(state.packs);
    }
    if (path.startsWith("/admin/billing/packs/")) {
      Object.assign(
        state.packs.find((p) => p.id === path.split("/").at(-1)),
        body(),
      );
      return send({});
    }
    if (path === "/admin/billing/providers") {
      if (method === "POST") {
        providerBodies.push(body());
        if (state.failProvider) {
          state.failProvider = false;
          return send({ error: { message: "渠道保存失败，请重试" } }, 502);
        }
        const { config, ...publicFields } = body();
        state.providers.push({ id: "provider-created", ...publicFields });
      }
      return send(state.providers);
    }
    const resolution = path.match(
      /^\/admin\/billing\/refunds\/([^/]+)\/resolve$/,
    );
    if (resolution) {
      state.refundResolutions.push(body());
      if (state.failResolution) {
        state.failResolution = false;
        return send({ error: { message: "核实结果暂未保存，请重试" } }, 500);
      }
      const owner = state.orders.find((order) =>
        order.refunds.some((refund) => refund.id === resolution[1]),
      );
      const refund = owner.refunds.find(
        (refund) => refund.id === resolution[1],
      );
      refund.status = body().outcome;
      refund.manual_resolution_allowed = false;
      if (refund.status === "succeeded")
        owner.refunded_minor += refund.amount_minor;
      return send(owner);
    }
    if (path.startsWith("/admin/billing/providers/")) {
      Object.assign(
        state.providers.find((p) => p.id === path.split("/").at(-1)),
        body(),
      );
      return send({});
    }
    if (path.match(/^\/admin\/billing\/orders\/[^/]+\/refunds$/)) {
      refundBodies.push(body());
      if (state.failAdminRefund) {
        state.failAdminRefund = false;
        return send({ error: { message: "退款服务暂不可用，请重试" } }, 502);
      }
      state.orders[0].refunds.push({
        id: "admin-refund",
        status: "pending",
        amount_minor: body().amount_minor,
        credits: 100,
        reason: body().reason,
        created_at: now,
      });
      return send({});
    }
    if (
      path.match(
        /^\/admin\/billing\/(orders|refunds)\/[^/]+\/(check|approve|reject)$/,
      )
    )
      return send({});
    unknown.push(`${method} ${path}`);
    return send({ error: { message: "Unmatched isolated fixture API" } }, 501);
  });
  const page = await context.newPage();
  page.on("pageerror", (e) => pageErrors.push(e.message));
  return {
    context,
    page,
    state,
    requests,
    createBodies,
    refundBodies,
    packBodies,
    providerBodies,
  };
}

async function screenshot(page, name) {
  await page.screenshot({ path: resolve(output, `${name}.png`) });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth + 1,
  );
  if (overflow)
    console.log(
      await page.evaluate(() => ({
        viewport: innerWidth,
        document: document.documentElement.scrollWidth,
        elements: [
          ...document.querySelectorAll(
            "body,#root,.admin-shell,.admin-sidebar,.admin-sidebar nav,.admin-workspace,.admin-content,.admin-topbar,.billing-admin,.admin-panel,.admin-table-scroll",
          ),
        ].map((e) => ({
          tag: e.tagName,
          cls: e.className,
          right: e.getBoundingClientRect().right,
          width: e.getBoundingClientRect().width,
          scroll: e.scrollWidth,
          overflow: getComputedStyle(e).overflowX,
        })),
      })),
    );
  assert.equal(overflow, false, `${name} document overflow`);
}

try {
  const user = await fixture();
  await user.page.goto(`${base}/billing`);
  await user.page
    .getByRole("heading", { name: "选择你的创作套餐", exact: true })
    .waitFor();
  assert.equal(await user.page.locator(".billing-wallet").count(), 0);
  await user.page.getByRole("button", { name: "积分包", exact: true }).click();
  await user.page
    .getByRole("button", { name: "创建付款订单", exact: true })
    .waitFor();
  await screenshot(user.page, "user-desktop-dark");
  user.state.failCreate = true;
  await user.page
    .getByRole("button", { name: "创建付款订单", exact: true })
    .click();
  await user.page
    .getByRole("alert")
    .filter({ hasText: "支付服务暂不可用" })
    .waitFor();
  await user.page
    .getByRole("button", { name: "创建付款订单", exact: true })
    .click();
  await user.page.getByRole("dialog").waitFor();
  await user.page.getByRole("link", { name: "打开收银台" }).waitFor();
  assert.equal(user.createBodies.length, 2);
  assert.equal(
    user.createBodies[0].idempotency_key,
    user.createBodies[1].idempotency_key,
  );
  assert.match(user.page.url(), /order=order-created/);
  await user.page.reload();
  await user.page.getByRole("link", { name: "打开收银台" }).waitFor();
  assert.equal(
    user.requests.filter((r) => r.endsWith("/check")).length,
    0,
    "polling must not POST checks",
  );
  await user.page.getByRole("button", { name: "查询付款结果" }).click();
  await user.page
    .getByText("付款已确认，积分到账后会自动更新。请勿重复付款。")
    .waitFor();
  assert.equal(
    await user.page.getByRole("link", { name: "打开收银台" }).count(),
    0,
  );
  await screenshot(user.page, "order-paid-dark");
  user.state.orders[0].status = "completed";
  user.state.available = 24000;
  await user.page
    .getByRole("dialog")
    .getByText("积分已到账", { exact: true })
    .waitFor({ timeout: 10000 });
  await user.page
    .getByRole("button", { name: "申请退款", exact: true })
    .click();
  await user.page.getByLabel("退款原因").fill("未使用的积分，希望申请退款");
  user.state.failRefund = true;
  await user.page.getByRole("button", { name: "提交退款申请" }).click();
  await user.page
    .getByRole("alert")
    .filter({ hasText: "申请暂未提交" })
    .waitFor();
  assert.equal(
    await user.page.getByLabel("退款原因").inputValue(),
    "未使用的积分，希望申请退款",
  );
  await user.page.getByRole("button", { name: "提交退款申请" }).click();
  await user.page
    .getByRole("dialog")
    .getByText("退款待审核", { exact: true })
    .waitFor();
  await user.page.getByRole("button", { name: "关闭", exact: true }).click();
  await user.page.getByRole("link", { name: "我的会员", exact: true }).click();
  assert.match(await user.page.locator(".billing-wallet").innerText(), /240/);
  assert.match(user.page.url(), /section=memberships/);
  await user.page.getByRole("link", { name: "订单记录", exact: true }).click();
  await user.page.locator(".billing-order-row").first().click();
  assert.match(user.page.url(), /section=orders/);
  assert.match(user.page.url(), /order=order-created/);
  await user.page.reload();
  await user.page
    .getByRole("dialog")
    .getByText("退款待审核", { exact: true })
    .waitFor();
  await user.page.getByRole("button", { name: "关闭", exact: true }).click();
  assert.match(user.page.url(), /section=orders/);
  await user.page
    .getByRole("heading", { name: "付款订单", exact: true })
    .waitFor();
  checks.push(
    "user purchase retry preserves idempotency, reload restores order, GET polling, paid/completed distinction, refund failure preserves reason",
  );
  await user.context.close();

  const mobile = await fixture({ width: 390, height: 844, theme: "light" });
  await mobile.page.goto(`${base}/billing`);
  await mobile.page
    .getByRole("button", { name: "积分包", exact: true })
    .click();
  await mobile.page
    .getByRole("button", { name: "创建付款订单", exact: true })
    .waitFor();
  await screenshot(mobile.page, "user-phone-light");
  await mobile.page
    .getByRole("button", { name: "创建付款订单", exact: true })
    .click();
  await mobile.page.getByRole("link", { name: "打开收银台" }).waitFor();
  await screenshot(mobile.page, "order-phone-light");
  await mobile.page
    .getByRole("button", { name: "取消订单", exact: true })
    .click();
  await mobile.page
    .getByRole("button", { name: "确认取消订单", exact: true })
    .click();
  await mobile.page
    .getByRole("dialog")
    .getByText("已取消", { exact: true })
    .waitFor();
  assert.equal(
    await mobile.page.getByRole("link", { name: "打开收银台" }).count(),
    0,
  );
  await mobile.context.close();
  checks.push("390px light user page and checkout without horizontal overflow");

  const off = await fixture({ enabled: false, role: "user" });
  await off.page.goto(`${base}/billing`);
  await off.page
    .getByText("暂未开放购买。你的积分和历史订单仍可查看。")
    .waitFor();
  assert.equal(
    await off.page
      .getByRole("button", { name: "创建付款订单", exact: true })
      .count(),
    0,
  );
  await off.page.getByRole("link", { name: "我的会员", exact: true }).click();
  await off.page.locator(".billing-wallet").waitFor();
  await off.page.getByRole("link", { name: "订单记录", exact: true }).click();
  await off.page.getByRole("heading", { name: "付款订单" }).waitFor();
  await off.context.close();
  checks.push("disabled billing retains wallet/history and hides new purchase");

  const admin = await fixture();
  await admin.page.goto(`${base}/admin/billing`);
  await admin.page.getByRole("button", { name: "查看订单" }).waitFor();
  await screenshot(admin.page, "admin-orders-desktop-dark");
  await admin.page.getByRole("button", { name: "积分包", exact: true }).click();
  await admin.page.getByRole("button", { name: "新建积分包" }).click();
  await admin.page.getByLabel("积分包名称").fill("小额补给");
  await admin.page.getByLabel("售价", { exact: true }).fill("12.34");
  await admin.page.getByLabel("到账积分", { exact: true }).fill("150.25");
  await admin.page.getByRole("button", { name: "保存积分包" }).click();
  await admin.page.getByRole("cell", { name: "小额补给" }).waitFor();
  assert.equal(admin.packBodies[0].amount_minor, 1234);
  assert.equal(admin.packBodies[0].credits, 15025);
  await admin.page
    .getByRole("button", { name: "支付渠道", exact: true })
    .click();
  await admin.page.getByRole("button", { name: "添加支付渠道" }).click();
  await admin.page
    .getByRole("button", { name: "支付服务商", exact: true })
    .click();
  await admin.page
    .getByRole("option", { name: "微信支付 Native", exact: true })
    .click();
  await admin.page.getByLabel("渠道名称").fill("新微信渠道");
  await admin.page.getByLabel("App ID", { exact: false }).fill("fixture-app");
  await admin.page
    .getByLabel("商户 API 证书序列号", { exact: false })
    .fill("fixture-serial");
  await admin.page
    .getByLabel("商户 API 私钥 PEM", { exact: false })
    .fill("fixture-key-not-a-real-credential");
  await admin.page
    .getByLabel("微信支付公钥 ID", { exact: false })
    .fill("fixture-public-id");
  await admin.page
    .getByLabel("微信支付公钥 PEM", { exact: false })
    .fill("fixture-public-key");
  await admin.page
    .getByLabel("微信商户号", { exact: false })
    .fill("fixture-merchant");
  await admin.page
    .getByLabel("API v3 密钥", { exact: false })
    .fill("fixture-secret-not-a-real-credential");
  await screenshot(admin.page, "admin-provider-form-dark");
  await admin.page.getByRole("button", { name: "保存支付渠道" }).click();
  await admin.page.getByRole("cell", { name: "新微信渠道" }).waitFor();
  await admin.page.getByRole("button", { name: "添加支付渠道" }).click();
  await admin.page
    .getByRole("button", { name: "支付服务商", exact: true })
    .click();
  await admin.page
    .getByRole("option", { name: "微信支付 Native", exact: true })
    .click();
  assert.equal(
    await admin.page.getByLabel("API v3 密钥", { exact: false }).inputValue(),
    "",
  );
  assert.match(admin.providerBodies[0].id, /^[\da-f-]{36}$/);
  await admin.page.getByRole("button", { name: "关闭", exact: true }).click();
  await admin.page
    .getByRole("button", { name: "收款设置", exact: true })
    .click();
  await admin.page.getByLabel("付款有效期（分钟）").fill("75");
  await admin.page.getByRole("button", { name: "保存设置" }).click();
  await admin.page.getByText("收款设置已保存").waitFor();
  assert.equal(admin.state.settings.checkout_minutes, 75);
  await admin.page
    .getByRole("button", { name: "订单与退款", exact: true })
    .click();
  await admin.page.getByRole("button", { name: "查看订单" }).click();
  await admin.page
    .getByRole("button", { name: "发起退款", exact: true })
    .click();
  await admin.page.getByLabel("退款原因").fill("后台人工退款审核通过");
  admin.state.failAdminRefund = true;
  await admin.page.getByRole("button", { name: "核对并退款" }).click();
  await admin.page
    .getByRole("button", { name: "确认退款", exact: true })
    .click();
  await admin.page
    .getByRole("alert")
    .filter({ hasText: "退款服务暂不可用" })
    .waitFor();
  await admin.page.getByRole("button", { name: "核对并退款" }).click();
  await admin.page
    .getByRole("button", { name: "确认退款", exact: true })
    .click();
  await admin.page.getByText("退款处理中", { exact: true }).first().waitFor();
  assert.equal(
    admin.refundBodies[0].idempotency_key,
    admin.refundBodies[1].idempotency_key,
  );
  checks.push(
    "admin pack decimal conversions, provider secrets clear after save, settings save, refund retry idempotency",
  );
  await admin.context.close();

  const initialSetup = await fixture({ enabled: false });
  initialSetup.state.settings.public_origin = "";
  initialSetup.state.providers = [];
  await initialSetup.page.goto(`${base}/admin/billing`);
  const freshPage = initialSetup.page;
  const channelsTab = freshPage.getByRole("button", { name: "支付渠道", exact: true });
  const addChannel = freshPage.getByRole("button", { name: "添加支付渠道" });
  await channelsTab.click();
  const setupShortcut = freshPage.getByRole("button", { name: "去收款设置" });
  await setupShortcut.waitFor();
  assert.equal(await addChannel.isEnabled(), false);
  await setupShortcut.click();
  const salesSwitch = freshPage.getByRole("checkbox", { name: /^开放套餐购买/ });
  assert.equal(await salesSwitch.isChecked(), false);
  const exchangeRate = freshPage.getByLabel("订阅美元兑人民币汇率");
  assert.equal(await exchangeRate.inputValue(), "");
  await exchangeRate.fill("7.20");
  await freshPage.getByLabel("网站公开地址").fill("https://example.test");
  await freshPage.getByRole("button", { name: "保存设置" }).click();
  await freshPage.getByText("收款设置已保存").waitFor();
  assert.equal(initialSetup.state.settings.public_origin, "https://example.test");
  assert.equal(initialSetup.state.settings.enabled, false);
  assert.equal(initialSetup.state.settings.subscription_usd_to_cny_rate, "7.20");
  await screenshot(freshPage, "admin-exchange-rate");
  await channelsTab.click();
  await addChannel.click();
  assert.match(
    await freshPage.getByLabel("支付通知地址").inputValue(),
    /^https:\/\/example\.test\/api\/v1\/billing\/webhook\/[\da-f-]{36}$/,
  );
  await freshPage.getByRole("button", { name: "关闭", exact: true }).click();
  await freshPage.getByRole("button", { name: "收款设置", exact: true }).click();
  assert.equal(await exchangeRate.inputValue(), "7.20");
  await exchangeRate.fill("");
  await freshPage.getByRole("button", { name: "保存设置" }).click();
  await freshPage.getByText("收款设置已保存").waitFor();
  assert.equal(initialSetup.state.settings.subscription_usd_to_cny_rate, null);
  await initialSetup.context.close();
  checks.push("fresh payment setup saves the website address with purchases closed, unlocks channel creation and saves or clears the manual exchange rate");

  const smallAdmin = await fixture({ width: 390, height: 844, theme: "light" });
  await smallAdmin.page.goto(`${base}/admin/billing`);
  await smallAdmin.page.getByRole("button", { name: "查看订单" }).waitFor();
  await screenshot(smallAdmin.page, "admin-orders-phone-light");
  await smallAdmin.page
    .getByRole("button", { name: "收款设置", exact: true })
    .click();
  await smallAdmin.page.getByLabel("付款有效期（分钟）").waitFor();
  await screenshot(smallAdmin.page, "admin-settings-phone-light");
  await smallAdmin.page
    .getByRole("button", { name: "积分预留", exact: true })
    .click();
  await smallAdmin.page
    .getByRole("button", { name: "处理预留", exact: true })
    .click();
  await smallAdmin.page.getByLabel("实际扣除积分").fill("3.25");
  await smallAdmin.page
    .getByLabel("处理依据与原因")
    .fill("已核实上游实际费用，按实际积分结算");
  await screenshot(smallAdmin.page, "admin-reservation-phone-light");
  await smallAdmin.page
    .getByRole("button", { name: "核对并处理", exact: true })
    .click();
  await smallAdmin.page
    .getByRole("button", { name: "确认处理", exact: true })
    .click();
  await smallAdmin.page.getByText("没有需要人工处理的积分预留").waitFor();
  assert.equal(smallAdmin.state.resolves[0].units, 325);
  assert.equal(smallAdmin.state.resolves[0].action, "settle");
  await smallAdmin.context.close();
  const english = await fixture({
    width: 360,
    height: 800,
    theme: "dark",
    lang: "en",
  });
  await english.page.goto(`${base}/billing`);
  await english.page
    .getByRole("button", { name: "Points packs", exact: true })
    .click();
  await english.page
    .getByRole("button", { name: "Create payment order", exact: true })
    .waitFor();
  await screenshot(english.page, "user-phone-360-english-dark");
  await english.context.close();
  const easy = await fixture({ width: 390, height: 844, theme: "light" });
  await easy.page.goto(`${base}/admin/billing`);
  await easy.page
    .getByRole("button", { name: "支付渠道", exact: true })
    .click();
  await easy.page
    .getByRole("button", { name: "添加支付渠道", exact: true })
    .click();
  assert.equal(
    (
      await easy.page
        .getByRole("button", { name: "支付服务商", exact: true })
        .innerText()
    ).trim(),
    "易支付",
  );
  assert.match(
    await easy.page
      .getByRole("button", { name: "支付方式", exact: true })
      .innerText(),
    /支付宝/,
  );
  assert.match(
    await easy.page
      .getByRole("button", { name: "付款入口", exact: true })
      .innerText(),
    /二维码/,
  );
  assert.match(
    await easy.page
      .getByRole("button", { name: "退款成功返回码", exact: true })
      .innerText(),
    /1/,
  );
  assert.equal(
    await easy.page
      .getByRole("button", { name: "币种", exact: true })
      .isDisabled(),
    true,
  );
  await easy.page.getByLabel("渠道名称").fill("易支付 · 微信");
  await easy.page
    .getByLabel("易支付接口地址")
    .fill("https://gateway.example.test");
  await easy.page.getByLabel("商户 PID").fill("fixture-pid");
  await easy.page.getByLabel("商户密钥").fill("fixture-pkey");
  await easy.page.getByLabel("通道 ID").fill("123");
  for (const [name, option] of [
    ["支付方式", "微信支付"],
    ["付款入口", "收银台"],
    ["退款成功返回码", "0"],
  ]) {
    await easy.page.getByRole("button", { name, exact: true }).click();
    await easy.page.getByRole("option", { name: option, exact: true }).click();
  }
  easy.state.failProvider = true;
  await easy.page
    .getByRole("button", { name: "保存支付渠道", exact: true })
    .click();
  await easy.page
    .getByRole("alert")
    .filter({ hasText: "渠道保存失败" })
    .waitFor();
  assert.equal(
    await easy.page.getByLabel("商户密钥").inputValue(),
    "fixture-pkey",
  );
  assert.match(
    await easy.page
      .getByRole("button", { name: "付款入口", exact: true })
      .innerText(),
    /收银台/,
  );
  await screenshot(easy.page, "easypay-parameters-phone-light");
  await easy.page
    .getByRole("button", { name: "保存支付渠道", exact: true })
    .click();
  await easy.page
    .getByRole("cell", { name: "易支付 · 微信", exact: true })
    .waitFor();
  await easy.page
    .getByRole("row", { name: /易支付 · 微信/ })
    .getByRole("cell", { name: "易支付", exact: true })
    .waitFor();
  assert.deepEqual(easy.providerBodies[0], easy.providerBodies[1]);
  assert.equal(easy.providerBodies[1].currency, "cny");
  assert.deepEqual(easy.providerBodies[1].config, {
    api_base: "https://gateway.example.test",
    pid: "fixture-pid",
    pkey: "fixture-pkey",
    channel_id: "123",
    payment_type: "wxpay",
    payment_mode: "popup",
    refund_success_code: "0",
  });
  await easy.page.reload();
  await easy.page
    .getByRole("button", { name: "支付渠道", exact: true })
    .click();
  await easy.page
    .getByRole("cell", { name: "易支付 · 微信", exact: true })
    .waitFor();
  await easy.page
    .getByRole("button", { name: "添加支付渠道", exact: true })
    .click();
  assert.equal(await easy.page.getByLabel("商户密钥").inputValue(), "");
  assert.match(
    await easy.page
      .getByRole("button", { name: "退款成功返回码", exact: true })
      .innerText(),
    /1/,
  );
  await easy.page.getByRole("button", { name: "关闭", exact: true }).click();

  const pendingRefund = {
    id: "manual-refund",
    status: "pending",
    amount_minor: 2800,
    credits: 12000,
    reason: "商户受理后等待核实",
    created_at: now,
    manual_resolution_allowed: false,
  };
  easy.state.orders[0].refunds = [pendingRefund];
  await easy.page
    .getByRole("button", { name: "订单与退款", exact: true })
    .click();
  await easy.page
    .getByRole("button", { name: "查看订单", exact: true })
    .click();
  assert.equal(
    await easy.page
      .getByRole("button", { name: "核实商户退款结果", exact: true })
      .count(),
    0,
  );
  pendingRefund.manual_resolution_allowed = true;
  await easy.page
    .getByRole("button", { name: "查询退款结果", exact: true })
    .click();
  await easy.page
    .getByRole("button", { name: "核实商户退款结果", exact: true })
    .waitFor();
  assert.equal(pendingRefund.status, "pending");
  await easy.page
    .getByRole("button", { name: "核实商户退款结果", exact: true })
    .click();
  assert.equal(
    await easy.page
      .getByRole("button", { name: "确认核实结果", exact: true })
      .isDisabled(),
    true,
  );
  await easy.page
    .getByRole("button", { name: "商户实际退款结果", exact: true })
    .click();
  await easy.page
    .getByRole("option", { name: "已核实退款成功", exact: true })
    .click();
  await easy.page
    .getByLabel("核实依据", { exact: true })
    .fill("已核对商户后台退款记录及到账凭证，确认原路退回。");
  await screenshot(easy.page, "easypay-refund-verification-phone-light");
  easy.state.failResolution = true;
  await easy.page
    .getByRole("button", { name: "确认核实结果", exact: true })
    .click();
  await easy.page
    .getByRole("button", { name: "确认执行", exact: true })
    .click();
  await easy.page
    .getByRole("alert")
    .filter({ hasText: "核实结果暂未保存" })
    .waitFor();
  assert.equal(pendingRefund.status, "pending");
  if ((await easy.page.getByLabel("核实依据", { exact: true }).count()) === 0) {
    await writeFile(
      resolve(output, "manual-refund-failure-dom.txt"),
      await easy.page.locator("body").innerText(),
    );
    await screenshot(easy.page, "manual-refund-failure");
    throw new Error("Manual refund form disappeared after failed request");
  }
  assert.match(
    await easy.page.getByLabel("核实依据", { exact: true }).inputValue(),
    /到账凭证/,
  );
  await screenshot(easy.page, "easypay-refund-retry-phone-light");
  await easy.page
    .getByRole("button", { name: "确认核实结果", exact: true })
    .click();
  await easy.page
    .getByRole("button", { name: "确认执行", exact: true })
    .click();
  await easy.page.getByText("已全额退款", { exact: true }).waitFor();
  assert.equal(easy.state.refundResolutions.at(-1).outcome, "succeeded");
  assert.equal(
    await easy.page
      .getByRole("button", { name: "核实商户退款结果", exact: true })
      .count(),
    0,
  );
  easy.state.orders[0].refunded_minor = 0;
  easy.state.orders[0].refunds = [
    {
      ...pendingRefund,
      id: "manual-refund-failed",
      status: "pending",
      manual_resolution_allowed: true,
    },
  ];
  await easy.page.reload();
  await easy.page
    .getByRole("button", { name: "查看订单", exact: true })
    .click();
  await easy.page
    .getByRole("button", { name: "核实商户退款结果", exact: true })
    .click();
  await easy.page
    .getByRole("button", { name: "商户实际退款结果", exact: true })
    .click();
  await easy.page
    .getByRole("option", { name: "已核实未发生退款", exact: true })
    .click();
  await easy.page
    .getByLabel("核实依据", { exact: true })
    .fill("商户后台明确退款失败，无实际扣款记录。");
  const priorResolutionCount = easy.state.refundResolutions.length;
  await easy.page
    .getByRole("button", { name: "确认核实结果", exact: true })
    .click();
  await easy.page
    .getByRole("alertdialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  assert.equal(easy.state.refundResolutions.length, priorResolutionCount);
  assert.equal(easy.state.orders[0].refunds[0].status, "pending");
  await easy.page
    .getByRole("button", { name: "确认核实结果", exact: true })
    .click();
  await easy.page
    .getByRole("button", { name: "确认执行", exact: true })
    .click();
  await easy.page
    .getByRole("dialog")
    .getByText("退款失败", { exact: true })
    .first()
    .waitFor();
  assert.equal(easy.state.refundResolutions.at(-1).outcome, "failed");
  assert.equal(easy.state.orders[0].refunded_minor, 0);
  await easy.context.close();
  checks.push(
    "EasyPay catalog defaults and options persist across failed/retried save; CNY fixed and secrets cleared; pending manual refund requires explicit verified result, reason and confirmation, preserving pending on errors",
  );
  const easyBuyer = await fixture({ role: "user" });
  easyBuyer.state.orders = [
    {
      ...orderBase,
      id: "easypay-checkout",
      provider_name: "易支付 · 微信",
      status: "pending",
      cancel_allowed: false,
      checkout_url: "https://cashier.example.test/pay",
    },
  ];
  await easyBuyer.page.goto(`${base}/billing?order=easypay-checkout`);
  await easyBuyer.page
    .getByRole("dialog")
    .getByText("易支付 · 微信", { exact: true })
    .waitFor();
  assert.equal(
    await easyBuyer.page
      .getByRole("link", { name: "打开收银台", exact: true })
      .getAttribute("href"),
    "https://cashier.example.test/pay",
  );
  assert.equal(
    await easyBuyer.page
      .getByRole("button", { name: "取消订单", exact: true })
      .count(),
    0,
  );
  await easyBuyer.context.close();
  checks.push(
    "EasyPay user checkout uses provider name and hosted checkout; unsupported cancellation stays hidden; manual failed result requires confirmation and does not count as refunded",
  );
  const planBase = {
    id: "monthly-starter",
    name: "创作月卡",
    description: "适合每月持续创作",
    features: ["独立期内积分额度", "到期后可手动续购"],
    currency: "cny",
    amount_minor: 2900,
    original_amount_minor: 3900,
    credits: 30000,
    duration: 1,
    duration_unit: "month",
    sort_order: 0,
    active: true,
  };
  const termBase = {
    order_id: "membership-active",
    plan_id: planBase.id,
    plan_name: planBase.name,
    starts_at: now,
    expires_at: new Date(Date.now() + 30 * 86400000).toISOString(),
    status: "active",
    credits: 30000,
    available: 20000,
    reserved: 1000,
    user_id: "billing-test",
    user_name: "创作者",
    user_email: "creator@example.test",
  };
  const member = await fixture();
  member.state.plans = [
    {
      ...planBase,
      id: "monthly-pro",
      name: "进阶月卡",
      amount_minor: 5900,
      credits: 70000,
      sort_order: 1,
    },
    structuredClone(planBase),
    {
      ...planBase,
      id: "usd-monthly",
      name: "月度创作",
      currency: "usd",
      amount_minor: 2490,
      original_amount_minor: null,
    },
    {
      ...planBase,
      id: "annual-starter",
      name: "年度创作",
      monthly_plan_id: "usd-monthly",
      currency: "usd",
      duration: 12,
      amount_minor: 23904,
      original_amount_minor: null,
      credits: 360000,
    },
    { ...planBase, id: "inactive", name: "已下架会员", active: false },
  ];
  member.state.memberships = [structuredClone(termBase)];
  await member.page.goto(`${base}/billing`);
  await member.page.getByRole("group", { name: "选择会员套餐" }).waitFor();
  assert.equal(
    await member.page
      .getByRole("button", { name: "选择套餐 创作月卡", exact: true })
      .count(),
    1,
  );
  assert.equal(
    await member.page.getByText("已下架会员", { exact: true }).count(),
    0,
  );
  assert.equal(await member.page.locator(".billing-wallet").count(), 0);
  assert.equal(await member.page.locator(".billing-memberships").count(), 0);
  await screenshot(member.page, "memberships-desktop-dark");
  await member.page
    .getByRole("button", { name: "选择套餐 创作月卡", exact: true })
    .click();
  await member.page
    .getByRole("dialog", { name: "确认付款", exact: true })
    .waitFor();
  assert.match(
    await member.page
      .getByRole("dialog")
      .locator(".billing-details")
      .innerText(),
    /30 天/,
  );
  assert.equal(
    member.createBodies.length,
    0,
    "selecting a plan must not create an order",
  );
  await member.page.getByRole("button", { name: "关闭", exact: true }).click();
  await member.page.getByRole("button", { name: "年付", exact: true }).click();
  await member.page.getByText("比月付省 20%", { exact: true }).waitFor();
  assert.match(
    await member.page.locator(".billing-plan-price").innerText(),
    /239\.04/,
  );
  assert.match(
    await member.page.locator(".billing-plan-pricing-note").innerText(),
    /19\.92/,
  );
  assert.match(
    await member.page.locator(".billing-plan-pricing-note").innerText(),
    /一次支付/,
  );
  await member.page
    .getByRole("button", { name: "选择套餐 年度创作", exact: true })
    .click();
  await member.page
    .getByRole("group", { name: "选择支付方式", exact: true })
    .getByRole("button", { name: "银行卡", exact: true })
    .waitFor();
  assert.match(
    await member.page
      .getByRole("dialog")
      .locator(".billing-purchase-total")
      .innerText(),
    /239\.04/,
  );
  assert.match(
    await member.page
      .getByRole("dialog")
      .locator(".billing-details")
      .innerText(),
    /12 个月/,
  );
  assert.equal(
    await member.page
      .getByRole("dialog")
      .getByRole("button", { name: "微信支付", exact: true })
      .count(),
    0,
  );
  await member.page.getByRole("button", { name: "关闭", exact: true }).click();
  await member.page.getByRole("button", { name: "月付", exact: true }).click();
  await member.page
    .getByRole("button", { name: "积分包", exact: true })
    .click();
  await member.page
    .getByRole("button", { name: "会员套餐", exact: true })
    .click();
  assert.equal(member.createBodies.length, 0);
  await member.page
    .getByRole("button", { name: "选择套餐 进阶月卡", exact: true })
    .click();
  await member.page
    .getByRole("dialog")
    .getByRole("button", { name: "微信支付", exact: true })
    .waitFor();
  await screenshot(member.page, "membership-confirmation-desktop-dark");
  member.state.failCreate = true;
  await member.page
    .getByRole("button", { name: "创建付款订单", exact: true })
    .click();
  await member.page
    .getByRole("alert")
    .filter({ hasText: "支付服务暂不可用" })
    .waitFor();
  await member.page
    .getByRole("button", { name: "创建付款订单", exact: true })
    .click();
  await member.page
    .getByRole("dialog")
    .getByText("进阶月卡", { exact: true })
    .waitFor();
  assert.equal(member.createBodies[0].plan_id, "monthly-pro");
  assert.equal("pack_id" in member.createBodies[0], false);
  assert.equal(
    member.createBodies[0].idempotency_key,
    member.createBodies[1].idempotency_key,
  );
  await member.page.reload();
  await member.page
    .getByRole("dialog")
    .getByText("30 天", { exact: true })
    .waitFor();
  assert.equal(
    member.createBodies.length,
    2,
    "reloading must not create another membership order",
  );
  await member.page
    .getByRole("button", { name: "查询付款结果", exact: true })
    .click();
  await member.page
    .getByText("付款已确认，会员权益开通后会自动更新。请勿重复付款。")
    .waitFor();
  assert.equal(
    await member.page
      .getByRole("dialog")
      .getByText("积分已到账", { exact: true })
      .count(),
    0,
  );
  member.state.orders[0].status = "completed";
  member.state.orders[0].membership = {
    ...termBase,
    order_id: "order-created",
    plan_id: "monthly-pro",
    plan_name: "进阶月卡",
    credits: 70000,
    available: 70000,
  };
  member.state.memberships.unshift(member.state.orders[0].membership);
  await member.page
    .getByRole("dialog")
    .getByText("会员已生效", { exact: true })
    .waitFor({ timeout: 10000 });
  await screenshot(member.page, "membership-order-active-desktop-dark");
  await member.page
    .getByRole("button", { name: "申请退款", exact: true })
    .click();
  await member.page
    .getByText(
      "仅未使用的会员套餐可申请全额退款。审核通过后将收回该笔订单的会员权益。",
    )
    .waitFor();
  await member.page.getByLabel("退款原因").fill("未使用会员额度");
  await member.page
    .getByRole("button", { name: "提交退款申请", exact: true })
    .click();
  await member.page
    .getByRole("dialog")
    .getByText("退款待审核", { exact: true })
    .waitFor();
  assert.equal(member.refundBodies.length, 1);
  await member.page.getByRole("button", { name: "关闭", exact: true }).click();
  await member.page
    .getByRole("link", { name: "我的会员", exact: true })
    .click();
  assert.match(
    await member.page.locator(".billing-memberships").innerText(),
    /200/,
  );
  assert.match(await member.page.locator(".billing-wallet").innerText(), /120/);
  await member.page.reload();
  await member.page
    .getByRole("heading", { name: "我的会员", exact: true })
    .waitFor();
  await member.context.close();
  checks.push(
    "membership catalog sorting and hidden plans, exact plan_id order binding, failed checkout idempotency, reload, paid/active distinction and full-refund request",
  );

  const converted = await fixture({ width: 390, theme: "light" });
  const usdPlan = {
    ...planBase,
    id: "usd-fx-monthly",
    name: "创作会员",
    currency: "usd",
    amount_minor: 2000,
    original_amount_minor: null,
    payment_amounts: { cny: 14400 },
  };
  converted.state.plans = [usdPlan];
  converted.state.providers[0] = {
    ...converted.state.providers[0],
    kind: "easypay",
    name: "易支付",
  };
  converted.state.settings.subscription_usd_to_cny_rate = "7.2";
  await converted.page.goto(`${base}/billing`);
  await converted.page
    .getByRole("button", { name: "选择套餐 创作会员", exact: true })
    .click();
  const conversionDialog = converted.page.getByRole("dialog", { name: "确认付款", exact: true });
  const total = conversionDialog.locator(".billing-purchase-total");
  const createPayment = conversionDialog.getByRole("button", { name: "创建付款订单", exact: true });
  await conversionDialog.getByRole("button", { name: "银行卡", exact: true }).click();
  assert.match(await total.innerText(), /\$20/);
  await conversionDialog.getByRole("button", { name: "易支付", exact: true }).click();
  assert.match(await total.innerText(), /144.*\$20/s);
  await screenshot(converted.page, "membership-cny-checkout");

  usdPlan.payment_amounts.cny = 14500;
  converted.state.settings.subscription_usd_to_cny_rate = "7.25";
  await createPayment.click();
  await conversionDialog.getByText(/请重新核对后再创建订单/).waitFor();
  assert.match(await total.innerText(), /145.*\$20/s);
  assert.equal(converted.createBodies.length, 1);
  assert.equal(converted.createBodies[0].expected_amount_minor, 14400);
  assert.equal(converted.state.orders.some(({ id }) => id === "order-created"), false);
  await createPayment.click();
  const convertedDetail = converted.page.getByRole("dialog", { name: "订单详情", exact: true });
  await convertedDetail.getByText("1 USD = 7.25 CNY", { exact: true }).waitFor();
  assert.equal(converted.createBodies[1].expected_amount_minor, 14500);
  assert.equal(converted.createBodies[1].plan_id, usdPlan.id);
  assert.notEqual(converted.createBodies[0].idempotency_key, converted.createBodies[1].idempotency_key);
  assert.match(await convertedDetail.locator(".billing-details").innerText(), /145.*\$20/s);
  await converted.context.close();
  checks.push("USD membership uses quoted CNY at EasyPay checkout, stale quote waits for reconfirmation, and the order shows its charged amount and original dollar price");

  const memberMobile = await fixture({
    width: 390,
    height: 844,
    theme: "light",
  });
  memberMobile.state.plans = [
    structuredClone(planBase),
    {
      ...planBase,
      id: "pro",
      name: "进阶月卡",
      amount_minor: 5900,
      credits: 70000,
      sort_order: 1,
    },
  ];
  memberMobile.state.memberships = [
    structuredClone(termBase),
    {
      ...termBase,
      order_id: "next-term",
      status: "scheduled",
      available: 0,
      reserved: 0,
      starts_at: termBase.expires_at,
      expires_at: new Date(Date.now() + 60 * 86400000).toISOString(),
    },
    {
      ...termBase,
      order_id: "expired-term",
      status: "expired",
      available: 0,
      reserved: 0,
    },
  ];
  await memberMobile.page.goto(`${base}/billing?section=memberships`);
  await memberMobile.page.getByText("待生效", { exact: true }).waitFor();
  await memberMobile.page.getByText("已到期", { exact: true }).waitFor();
  await screenshot(memberMobile.page, "memberships-phone-light");
  await memberMobile.page
    .getByRole("link", { name: "购买套餐", exact: true })
    .click();
  await memberMobile.page
    .getByRole("group", { name: "选择会员套餐" })
    .scrollIntoViewIfNeeded();
  await screenshot(memberMobile.page, "membership-plans-phone-light");
  await memberMobile.page
    .getByRole("button", { name: "选择套餐 创作月卡", exact: true })
    .click();
  await screenshot(memberMobile.page, "membership-confirmation-phone-light");
  assert.equal(memberMobile.createBodies.length, 0);
  await memberMobile.page
    .getByRole("button", { name: "关闭", exact: true })
    .click();
  await memberMobile.page
    .getByRole("button", { name: "积分包", exact: true })
    .click();
  await memberMobile.page
    .getByRole("button", { name: "创建付款订单", exact: true })
    .click();
  await memberMobile.page.getByRole("dialog").waitFor();
  assert.equal(memberMobile.createBodies[0].pack_id, "pack-1");
  assert.equal("plan_id" in memberMobile.createBodies[0], false);
  await memberMobile.context.close();

  const memberOff = await fixture({
    enabled: false,
    role: "user",
    width: 360,
    height: 800,
    lang: "en",
  });
  memberOff.state.memberships = [structuredClone(termBase)];
  await memberOff.page.goto(`${base}/billing?section=memberships`);
  await memberOff.page.getByText("Active", { exact: true }).waitFor();
  assert.equal(
    await memberOff.page
      .getByRole("button", { name: "Create payment order", exact: true })
      .count(),
    0,
  );
  await screenshot(memberOff.page, "memberships-disabled-english-360");
  await memberOff.context.close();
  checks.push(
    "membership active/scheduled/expired allowances stay separate from wallet, legacy points purchase remains functional, disabled billing preserves memberships, 390px light and 360px English",
  );

  const preview = await fixture({ width: 390, height: 844, theme: "light" });
  await preview.page.goto(
    `${base}/billing?preview=1&section=memberships&order=order-existing`,
  );
  await preview.page
    .getByText("当前展示示例套餐，仅供预览，不可购买。", { exact: true })
    .waitFor();
  await preview.page
    .getByRole("button", { name: "查看套餐 创作会员", exact: true })
    .click();
  await preview.page
    .getByRole("dialog", { name: "套餐预览", exact: true })
    .waitFor();
  assert.equal(
    await preview.page
      .getByRole("button", { name: "创建付款订单", exact: true })
      .count(),
    0,
  );
  assert.equal(
    await preview.page
      .getByRole("group", { name: "选择支付方式", exact: true })
      .count(),
    0,
  );
  assert.equal(
    preview.requests.some((request) => /^GET \/billing\//.test(request)),
    false,
  );
  assert.equal(
    preview.requests.some((request) => /^(POST|PATCH|DELETE) /.test(request)),
    false,
  );
  await screenshot(preview.page, "admin-preview-confirmation-phone-light");
  await preview.page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .last()
    .click();
  await screenshot(preview.page, "admin-preview-samples-phone-light");
  await preview.page.setViewportSize({ width: 1440, height: 900 });
  await preview.page.evaluate(() => window.scrollTo(0, 0));
  await screenshot(preview.page, "monthly-preview-desktop-light");
  assert.equal(await preview.page.locator(".billing-plan-saving").count(), 0);
  await preview.page.getByRole("button", { name: "年付", exact: true }).click();
  const annualCards = preview.page.locator(".billing-plan-card");
  assert.equal(await annualCards.count(), 2);
  assert.match(
    await annualCards.first().locator(".billing-plan-price").innerText(),
    /192\.00/,
  );
  assert.match(
    await annualCards.first().locator(".billing-plan-pricing-note").innerText(),
    /16\.00/,
  );
  assert.equal(
    await preview.page.getByText("比月付省 20%", { exact: true }).count(),
    2,
  );
  await preview.page
    .getByRole("group", { name: "付款周期", exact: true })
    .scrollIntoViewIfNeeded();
  await screenshot(preview.page, "annual-preview-desktop-light");
  await preview.page.setViewportSize({ width: 390, height: 844 });
  await preview.page
    .getByRole("group", { name: "付款周期", exact: true })
    .scrollIntoViewIfNeeded();
  await screenshot(preview.page, "annual-preview-phone-light");
  await preview.page
    .getByRole("button", { name: "查看套餐 创作会员", exact: true })
    .click();
  await preview.page
    .getByRole("dialog", { name: "套餐预览", exact: true })
    .waitFor();
  assert.match(await preview.page.getByRole("dialog").innerText(), /192\.00/);
  assert.match(
    await preview.page.getByRole("dialog").innerText(),
    /积分整期一次发放/,
  );
  await screenshot(preview.page, "annual-confirmation-phone-light");
  await preview.page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .last()
    .click();
  preview.state.plans = [
    { ...planBase, id: "real-draft", name: "后台草稿会员", active: false },
  ];
  await preview.page.reload();
  await preview.page
    .getByRole("button", { name: "查看套餐 后台草稿会员", exact: true })
    .waitFor();
  assert.equal(
    await preview.page
      .getByText("当前展示示例套餐，仅供预览，不可购买。", { exact: true })
      .count(),
    0,
  );
  assert.equal(
    preview.requests.some((request) => /^GET \/billing\//.test(request)),
    false,
  );
  await preview.context.close();

  const annualNoSaving = await fixture({ width: 360, height: 800, lang: "en" });
  annualNoSaving.state.plans = [
    {
      ...planBase,
      id: "jpy-month",
      name: "Creator monthly",
      currency: "jpy",
      amount_minor: 1200,
      original_amount_minor: null,
    },
    {
      ...planBase,
      id: "jpy-year",
      name: "Creator annual",
      currency: "jpy",
      duration: 12,
      amount_minor: 15000,
      original_amount_minor: 18000,
      credits: 360000,
      monthly_plan_id: "jpy-month",
    },
  ];
  await annualNoSaving.page.goto(`${base}/billing`);
  await annualNoSaving.page
    .getByRole("button", { name: "Yearly", exact: true })
    .click();
  assert.equal(
    await annualNoSaving.page.locator(".billing-plan-saving").count(),
    0,
    "a higher annual price must not advertise savings",
  );
  assert.match(
    await annualNoSaving.page.locator(".billing-plan-pricing-note").innerText(),
    /1,250/,
  );
  await annualNoSaving.page
    .getByRole("group", { name: "Payment term", exact: true })
    .scrollIntoViewIfNeeded();
  await screenshot(annualNoSaving.page, "annual-no-saving-360-english-dark");
  annualNoSaving.state.plans[1].monthly_plan_id = null;
  await annualNoSaving.page.reload();
  await annualNoSaving.page
    .getByRole("button", { name: "Yearly", exact: true })
    .click();
  assert.equal(
    await annualNoSaving.page.locator(".billing-plan-saving").count(),
    0,
    "unlinked annual plans must not invent a monthly discount",
  );
  assert.equal(
    await annualNoSaving.page.locator("del").count(),
    1,
    "independent reference pricing remains available",
  );
  assert.equal(annualNoSaving.createBodies.length, 0);
  await annualNoSaving.context.close();
  checks.push(
    "annual total remains primary, valid monthly comparison shows accurate savings and monthly equivalent, no savings for higher or unlinked prices, reference price retained, one-time term grant explained; desktop/390px/360px-English captured",
  );

  const failedPreview = await fixture();
  failedPreview.state.previewStatus = 403;
  await failedPreview.page.goto(`${base}/billing?preview=1`);
  await failedPreview.page
    .getByRole("alert")
    .filter({ hasText: "无权预览会员套餐" })
    .waitFor();
  assert.equal(
    await failedPreview.page.getByRole("button", { name: /^查看套餐/ }).count(),
    0,
  );
  assert.equal(
    failedPreview.requests.some((request) => /^GET \/billing\//.test(request)),
    false,
  );
  await failedPreview.context.close();

  const deniedPreview = await fixture({ role: "user" });
  await deniedPreview.page.goto(`${base}/billing?preview=1`);
  await deniedPreview.page
    .getByRole("alert")
    .filter({ hasText: "套餐预览仅供超级管理员使用" })
    .waitFor();
  assert.equal(
    deniedPreview.requests.some((request) =>
      /\/(admin\/)?billing\//.test(request),
    ),
    false,
  );
  await deniedPreview.context.close();
  checks.push(
    "admin preview is read-only: samples only after successful empty list, real drafts preserved, no billing read/write requests even with return order query, denied access has no sample fallback",
  );

  const planAdmin = await fixture({ width: 390, height: 844, theme: "light" });
  planAdmin.state.plans = [structuredClone(planBase)];
  planAdmin.state.memberships = [structuredClone(termBase)];
  planAdmin.state.orders = [
    {
      ...orderBase,
      type: "membership",
      membership_days: 30,
      membership: termBase,
      pack_name: planBase.name,
      amount_minor: 2900,
      credits: 30000,
    },
  ];
  await planAdmin.page.goto(`${base}/admin/billing`);
  await planAdmin.page
    .getByRole("button", { name: "会员套餐", exact: true })
    .click();
  await planAdmin.page
    .getByRole("button", { name: "新建会员套餐", exact: true })
    .click();
  await planAdmin.page
    .getByLabel("套餐名称", { exact: true })
    .fill("进阶创作月卡");
  await planAdmin.page
    .getByLabel("说明", { exact: true })
    .fill("适合连续项目创作");
  await planAdmin.page
    .getByLabel("权益列表", { exact: false })
    .fill("独立积分额度\n手动续购");
  await planAdmin.page.getByLabel("售价", { exact: true }).fill("59.90");
  await planAdmin.page
    .getByLabel("原价（可选）", { exact: false })
    .fill("49.90");
  await planAdmin.page
    .getByLabel("期内积分额度", { exact: false })
    .fill("700.25");
  await planAdmin.page.getByLabel("排序", { exact: false }).fill("-2");
  await planAdmin.page.getByLabel("上架出售", { exact: false }).check();
  await planAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await planAdmin.page
    .getByRole("alert")
    .filter({ hasText: "原价应不低于售价" })
    .waitFor();
  assert.equal(planAdmin.state.planBodies.length, 0);
  await planAdmin.page
    .getByLabel("原价（可选）", { exact: false })
    .fill("79.90");
  planAdmin.state.failPlan = true;
  await planAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await planAdmin.page
    .getByRole("alert")
    .filter({ hasText: "套餐暂未保存" })
    .waitFor();
  assert.equal(
    await planAdmin.page.getByLabel("套餐名称", { exact: true }).inputValue(),
    "进阶创作月卡",
  );
  await screenshot(planAdmin.page, "membership-plan-form-phone-light");
  await planAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await planAdmin.page
    .getByRole("cell", { name: "进阶创作月卡 适合连续项目创作", exact: true })
    .waitFor();
  const submittedPlan = planAdmin.state.planBodies[1];
  assert.equal(submittedPlan.amount_minor, 5990);
  assert.equal(submittedPlan.original_amount_minor, 7990);
  assert.equal(submittedPlan.credits, 70025);
  assert.equal(submittedPlan.duration, 1);
  assert.equal(submittedPlan.duration_unit, "month");
  assert.equal(submittedPlan.sort_order, -2);
  assert.equal(submittedPlan.active, true);
  assert.deepEqual(submittedPlan.features, ["独立积分额度", "手动续购"]);
  await planAdmin.page.reload();
  await planAdmin.page
    .getByRole("button", { name: "会员套餐", exact: true })
    .click();
  await planAdmin.page
    .getByRole("button", { name: "编辑会员套餐 进阶创作月卡", exact: true })
    .click();
  assert.equal(
    await planAdmin.page.getByLabel("售价", { exact: true }).inputValue(),
    "59.90",
  );
  await planAdmin.page.getByLabel("原价（可选）", { exact: false }).fill("");
  await planAdmin.page.getByLabel("上架出售", { exact: false }).uncheck();
  await planAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await planAdmin.page.getByRole("cell", { name: /已下架/ }).waitFor();
  assert.equal(planAdmin.state.planBodies.at(-1).original_amount_minor, null);
  await screenshot(planAdmin.page, "admin-memberships-phone-light");
  await planAdmin.page
    .getByRole("button", { name: "订单与退款", exact: true })
    .click();
  await planAdmin.page
    .getByRole("button", { name: "查看订单", exact: true })
    .click();
  await planAdmin.page
    .getByRole("button", { name: "发起退款", exact: true })
    .click();
  assert.equal(
    await planAdmin.page.getByLabel(/退款金额/).count(),
    0,
    "membership refund amount must not be editable",
  );
  await planAdmin.page
    .getByLabel("退款原因", { exact: true })
    .fill("确认会员未使用");
  await planAdmin.page
    .getByRole("button", { name: "核对并退款", exact: true })
    .click();
  await planAdmin.page
    .getByRole("button", { name: "确认退款", exact: true })
    .click();
  await planAdmin.page
    .getByText("退款处理中", { exact: true })
    .first()
    .waitFor();
  assert.equal(planAdmin.refundBodies[0].amount_minor, 2900);
  assert.match(planAdmin.refundBodies[0].idempotency_key, /^[\da-f-]{36}$/);
  await planAdmin.context.close();
  checks.push(
    "admin membership plan validation, save failure retains all fields, decimal units, create/edit/reload/unpublish, membership inspection and full-only refund",
  );

  const annualAdmin = await fixture({
    width: 390,
    height: 844,
    theme: "light",
  });
  const monthlyDraft = {
    ...planBase,
    id: "monthly-usd-draft",
    name: "美元月付草稿",
    currency: "usd",
    amount_minor: 1990,
    original_amount_minor: null,
    active: false,
  };
  annualAdmin.state.plans = [
    monthlyDraft,
    { ...planBase, name: "人民币月付" },
    { ...monthlyDraft, id: "too-large", name: "高额度月付", credits: 40000 },
    { ...monthlyDraft, id: "three-months", name: "季度套餐", duration: 3 },
  ];
  await annualAdmin.page.goto(`${base}/admin/billing`);
  await annualAdmin.page
    .getByRole("button", { name: "会员套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("button", { name: "新建会员套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByLabel("套餐名称", { exact: true })
    .fill("对照年付套餐");
  await annualAdmin.page.getByLabel("售价", { exact: true }).fill("199.00");
  await annualAdmin.page.getByLabel("期限", { exact: true }).fill("12");
  await annualAdmin.page
    .getByLabel("期内积分额度", { exact: false })
    .fill("3600");
  await annualAdmin.page
    .getByRole("button", { name: "对照月付套餐", exact: true })
    .click();
  for (const excluded of ["人民币月付", "高额度月付", "季度套餐"]) {
    assert.equal(
      await annualAdmin.page
        .getByRole("option", { name: new RegExp(excluded) })
        .count(),
      0,
    );
  }
  await annualAdmin.page.getByRole("option", { name: /美元月付草稿/ }).click();
  assert.match(
    await annualAdmin.page.getByText(/月付 12 期合计/).innerText(),
    /238\.80/,
  );
  assert.match(
    await annualAdmin.page.getByText(/相比月付 12 期节省/).innerText(),
    /39\.80/,
  );
  assert.equal(
    await annualAdmin.page.getByLabel("售价", { exact: true }).inputValue(),
    "199.00",
  );
  assert.equal(
    await annualAdmin.page.getByLabel("原价（可选）", { exact: false }).count(),
    0,
  );
  await annualAdmin.page
    .getByText(/相比月付 12 期节省/)
    .scrollIntoViewIfNeeded();
  await screenshot(
    annualAdmin.page,
    "admin-annual-monthly-comparison-phone-light",
  );
  await annualAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("button", { name: "编辑会员套餐 对照年付套餐", exact: true })
    .waitFor();
  assert.equal(
    annualAdmin.state.planBodies.at(-1).monthly_plan_id,
    monthlyDraft.id,
  );
  assert.equal(annualAdmin.state.planBodies.at(-1).original_amount_minor, null);
  assert.equal(annualAdmin.state.planBodies.at(-1).amount_minor, 19900);

  await annualAdmin.page.reload();
  await annualAdmin.page
    .getByRole("button", { name: "会员套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("button", { name: "编辑会员套餐 对照年付套餐", exact: true })
    .click();
  assert.match(
    await annualAdmin.page
      .getByRole("button", { name: "对照月付套餐", exact: true })
      .innerText(),
    /美元月付草稿/,
  );
  await annualAdmin.page
    .getByLabel("期内积分额度", { exact: false })
    .fill("3599.99");
  await annualAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("alert")
    .filter({ hasText: "对照月付套餐不匹配" })
    .waitFor();
  assert.equal(annualAdmin.state.planBodies.length, 1);
  await annualAdmin.page
    .getByLabel("期内积分额度", { exact: false })
    .fill("3600");
  await annualAdmin.page
    .getByRole("button", { name: "期限单位", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("option", { name: "天", exact: true })
    .click();
  await annualAdmin.page.getByLabel("期限", { exact: true }).fill("365");
  await annualAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("alert")
    .filter({ hasText: "对照月付套餐不匹配" })
    .waitFor();
  assert.equal(annualAdmin.state.planBodies.length, 1);
  await annualAdmin.page.getByLabel("期限", { exact: true }).fill("360");
  assert.match(
    await annualAdmin.page.getByText(/月付 12 期合计/).innerText(),
    /238\.80/,
  );
  await annualAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("button", { name: "编辑会员套餐 对照年付套餐", exact: true })
    .waitFor();
  assert.equal(annualAdmin.state.planBodies.at(-1).duration, 360);
  assert.equal(annualAdmin.state.planBodies.at(-1).duration_unit, "day");
  assert.equal(
    annualAdmin.state.planBodies.at(-1).monthly_plan_id,
    monthlyDraft.id,
  );
  await annualAdmin.page
    .getByRole("button", { name: "编辑会员套餐 对照年付套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("button", { name: "对照月付套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("option", { name: "独立设置参考价", exact: true })
    .click();
  await annualAdmin.page
    .getByLabel("原价（可选）", { exact: false })
    .fill("299.00");
  await annualAdmin.page.getByLabel("期限", { exact: true }).fill("365");
  await annualAdmin.page
    .getByRole("button", { name: "保存会员套餐", exact: true })
    .click();
  await annualAdmin.page
    .getByRole("button", { name: "编辑会员套餐 对照年付套餐", exact: true })
    .waitFor();
  assert.equal(annualAdmin.state.planBodies.at(-1).monthly_plan_id, null);
  assert.equal(
    annualAdmin.state.planBodies.at(-1).original_amount_minor,
    29900,
  );
  assert.equal(annualAdmin.state.planBodies.at(-1).amount_minor, 19900);
  await annualAdmin.context.close();
  checks.push(
    "annual admin comparison accepts same-currency eligible monthly drafts, preserves actual price, persists linking on reload, blocks mismatched allowance/term and supports independent reference prices",
  );

  const denied = await fixture({ role: "admin" });
  await denied.page.goto(`${base}/admin/billing`);
  await denied.page
    .getByRole("heading", { name: "支付管理仅供超级管理员使用" })
    .waitFor();
  assert.equal(
    denied.requests.some((r) => r.includes("/admin/billing")),
    false,
  );
  assert.equal(
    await denied.page
      .getByRole("link", { name: "支付与积分", exact: true })
      .count(),
    0,
  );
  await denied.context.close();
  checks.push(
    "390px admin responsive views; ordinary admin denied and issues no billing management API request",
  );
  checks.push(
    "390px reservation form settles decimal points with confirmation; pending payment cancellation waits for server",
  );
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(unknown, []);
  await writeFile(
    resolve(output, "report.json"),
    JSON.stringify(
      {
        checks,
        pageErrors,
        unknown,
        boundary:
          "Real Chrome with all APIs intercepted; no provider calls or live payments.",
      },
      null,
      2,
    ),
  );
  console.log(
    JSON.stringify(
      { passed: checks.length, screenshots: output, checks },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}
