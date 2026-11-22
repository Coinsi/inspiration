import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const { outputFiles } = await build({
  entryPoints: [
    fileURLToPath(new URL("../src/lib/billingPlanPreview.ts", import.meta.url)),
  ],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
  target: "es2022",
});
const { loadPlanPreview } = await import(
  `data:text/javascript;base64,${Buffer.from(outputFiles[0].text).toString("base64")}`
);
const original = {
  fetch: globalThis.fetch,
  localStorage: globalThis.localStorage,
};
beforeEach(() => {
  globalThis.localStorage = { getItem: () => null, removeItem() {} };
});
afterEach(() => Object.assign(globalThis, original));
const response = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

test("preview preserves real drafts, prices and currencies with only an authorized read", async () => {
  const drafts = [
    { id: "usd-draft", active: false, currency: "usd", amount_minor: 1950 },
    { id: "cny-active", active: true, currency: "cny", amount_minor: 3900 },
  ];
  const requests = [];
  const signal = new AbortController().signal;
  globalThis.fetch = async (url, request) => {
    requests.push({ url, request });
    return response(drafts);
  };
  assert.deepEqual(await loadPlanPreview(signal), { plans: drafts, sample: false });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/v1/admin/billing/plans");
  assert.equal(requests[0].request.method, "GET");
  assert.equal(requests[0].request.body, undefined);
  assert.equal(requests[0].request.signal, signal);
});

test("only an authorized empty list yields unpublished USD monthly and annual examples", async () => {
  const requests = [];
  globalThis.fetch = async (url, request) => {
    requests.push({ url, method: request.method });
    return response([]);
  };
  const result = await loadPlanPreview();
  assert.equal(result.sample, true);
  assert.equal(result.plans.length, 4);
  assert.deepEqual(
    result.plans.map((plan) => [plan.amount_minor, plan.duration]),
    [[2000, 1], [4000, 1], [19200, 12], [38400, 12]],
  );
  const [creatorMonthly, plusMonthly, creatorAnnual, plusAnnual] = result.plans;
  assert.equal(creatorAnnual.monthly_plan_id, creatorMonthly.id);
  assert.equal(plusAnnual.monthly_plan_id, plusMonthly.id);
  assert.equal(creatorAnnual.credits, creatorMonthly.credits * 12);
  assert.equal(plusAnnual.credits, plusMonthly.credits * 12);
  assert.ok(result.plans.every((plan) => plan.currency === "usd" && !plan.active));
  assert.ok(result.plans.every((plan) => plan.duration_unit === "month"));
  assert.ok(result.plans.every((plan) => plan.id.startsWith("preview-")));
  result.plans[0].features.push("mutated by a consumer");
  const next = await loadPlanPreview();
  assert.ok(!next.plans[0].features.includes("mutated by a consumer"));
  assert.deepEqual(requests, [
    { url: "/api/v1/admin/billing/plans", method: "GET" },
    { url: "/api/v1/admin/billing/plans", method: "GET" },
  ]);
});

for (const status of [401, 403, 500]) {
  test(`HTTP ${status} never falls back to sample plans`, async () => {
    globalThis.fetch = async () =>
      response({ error: { code: "READ_DENIED", message: "Read failed" } }, status);
    await assert.rejects(loadPlanPreview(), (error) => error.code === "READ_DENIED");
  });
}

test("network errors, canceled requests and malformed responses never produce examples", async () => {
  const networkError = new TypeError("Offline");
  globalThis.fetch = async () => { throw networkError; };
  await assert.rejects(loadPlanPreview(), (error) => error === networkError);
  const controller = new AbortController();
  controller.abort();
  globalThis.fetch = async (_url, request) => {
    request.signal.throwIfAborted();
    return response([]);
  };
  await assert.rejects(loadPlanPreview(controller.signal), { name: "AbortError" });
  for (const malformed of [{}, null, { plans: [] }]) {
    globalThis.fetch = async () => response(malformed);
    await assert.rejects(loadPlanPreview(), /响应格式/);
  }
});
