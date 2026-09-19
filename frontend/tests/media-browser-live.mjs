import { toggleAccountTheme } from "./helpers/account.mjs";
// Existing project smoke check. Only login and opt-in prompt optimization may write.
// Never generates media, selects versions, edits entities or deletes resources.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.APP_URL || "http://127.0.0.1:5187",
  out = process.env.ARTIFACT_DIR || "./test-results/media-live",
  project = process.env.TEST_PROJECT_ID;
assert.ok(project, "TEST_PROJECT_ID is required");
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  }),
  page = await context.newPage();
const errors = [],
  failed = [],
  writes = [],
  report = {};
page.on("pageerror", (e) => errors.push(e.message));
page.on("response", (r) => {
  if (r.url().includes("/api/") && r.status() >= 400)
    failed.push({ path: new URL(r.url()).pathname, status: r.status() });
});
await context.route("**/api/**", (route) => {
  const r = route.request(),
    path = new URL(r.url()).pathname;
  if (!["GET", "HEAD"].includes(r.method())) {
    writes.push(path);
    if (!(
      process.env.TEST_OPTIMIZER === "1" && path.endsWith("/prompts/optimize")
    ))
      return route.abort();
  }
  return route.continue();
});
try {
  const auth = await context.request.post(`${base}/api/v1/auth/login`, {
    data: {
      username: process.env.TEST_USERNAME || "demo",
      password: process.env.TEST_PASSWORD || "demo1234",
    },
  });
  assert.equal(auth.status(), 200);
  const { access_token } = await auth.json(),
    headers = { Authorization: `Bearer ${access_token}` };
  await context.addInitScript((token) => {
    localStorage.setItem("inspiration_token", token);
    localStorage.setItem("inspiration_lang", "zh");
  }, access_token);
  const get = async (p) => {
    const r = await context.request.get(
      `${base}/api/v1/projects/${project}${p}`,
      { headers },
    );
    assert.equal(r.status(), 200);
    return r.json();
  };
  const assets = await get("/assets");
  report.assets = assets.length;
  const candidate =
    assets.find((a) => a.ref_count > 0 && a.gen_count >= 2) ||
    assets.find((a) => a.ref_count > 0) ||
    assets[0];
  assert.ok(candidate);
  await page.goto(`${base}/projects/${project}/assets`);
  await page
    .getByRole("button", { name: `快速预览 ${candidate.name}`, exact: true })
    .waitFor();
  await page.waitForFunction(() =>
    [...document.images].every((i) => i.complete),
  );
  await page.screenshot({ path: `${out}/assets-dark.png` });
  await page
    .getByRole("button", { name: `快速预览 ${candidate.name}`, exact: true })
    .click();
  await page.getByRole("dialog").waitFor();
  await page.waitForFunction(() =>
    [...document.images].every((i) => i.complete),
  );
  await page.screenshot({ path: `${out}/asset-preview.png` });
  await page.keyboard.press("Escape");
  await toggleAccountTheme(page);
  await page.locator("main").evaluate(el => el.scrollTo(0, 0));
  await page.screenshot({ path: `${out}/assets-light.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("main").evaluate(el => el.scrollTo(0, 0));
  assert.ok(await page.locator("main").evaluate(el => el.scrollWidth <= el.clientWidth));
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({ path: `${out}/assets-mobile.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/projects/${project}/storyboard`);
  await page
    .getByRole("button", { name: "快速预览", exact: true })
    .first()
    .waitFor();
  await page.screenshot({ path: `${out}/storyboard.png` });
  await page.getByRole("button", { name: "分镜编辑", exact: true }).click();
  await page.getByLabel("镜头标题").first().waitFor();
  await page.getByRole("button", { name: "画面浏览", exact: true }).click();
  const comparisonAsset = assets.find((a) => a.gen_count >= 2) || candidate;
  await page.goto(
    `${base}/projects/${project}/assets/${comparisonAsset.id}?tab=gen`,
  );
  await page.getByRole("button", { name: "优化提示词", exact: true }).waitFor();
  const labels = page.locator("label").filter({ hasText: "加入比较" });
  if (comparisonAsset.gen_count >= 2) await labels.nth(1).waitFor();
  if ((await labels.count()) >= 2) {
    await labels.nth(0).getByRole("checkbox").check();
    await labels.nth(1).getByRole("checkbox").check();
    await page
      .getByRole("button", { name: "比较版本 (2/2)", exact: true })
      .click();
    await page.waitForFunction(() =>
      [...document.images].every((i) => i.complete),
    );
    await page.screenshot({ path: `${out}/compare-real.png` });
    await page.keyboard.press("Escape");
    report.realComparison = true;
  }
  await page.goto(`${base}/projects/${project}/assets/${candidate.id}?tab=gen`);
  await page.getByRole("button", { name: "优化提示词", exact: true }).waitFor();
  await page.getByRole("button", { name: "优化提示词", exact: true }).click();
  await page
    .getByLabel("原始想法")
    .fill(
      "保留参考图中主体的实际外观，整理成清楚的角色参考提示词。不要新增剧情，无法确定的细节请单独说明。",
    );
  await page.getByRole("button", { name: "结合参考", exact: true }).click();
  const images = page
    .getByRole("dialog")
    .locator("button[aria-pressed]")
    .filter({ has: page.locator("img") });
  await images.first().waitFor();
  await images.first().click();
  await page.screenshot({ path: `${out}/visual-reference.png` });
  if (process.env.TEST_OPTIMIZER === "1") {
    const responsePromise = page.waitForResponse(
      (r) =>
        r.url().endsWith("/prompts/optimize") &&
        r.request().method() === "POST",
      { timeout: 180000 },
    );
    await page
      .getByRole("button", { name: "生成优化建议", exact: true })
      .click();
    const r = await responsePromise;
    report.optimizerStatus = r.status();
    report.optimizerResult = await r.json();
    assert.equal(r.status(), 200, JSON.stringify(report.optimizerResult));
    await page.getByLabel("优化结果", { exact: true }).waitFor();
    await page.screenshot({ path: `${out}/visual-result.png`, fullPage: true });
    await page
      .getByRole("button", { name: "采用到生成指令", exact: true })
      .click();
    assert.equal(
      await page
        .getByLabel("本次生成指令（留空使用镜头 / 资产提示词）")
        .inputValue(),
      report.optimizerResult.prompt,
    );
    report.adoptedDraft = true;
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(failed, []);
  assert.ok(writes.every((p) => p.endsWith("/prompts/optimize")));
  await writeFile(
    `${out}/live.json`,
    JSON.stringify(
      { passed: true, ...report, errors, failed, writes },
      null,
      2,
    ),
  );
  console.log(
    JSON.stringify({
      passed: true,
      assets: report.assets,
      realComparison: report.realComparison,
      optimizerStatus: report.optimizerStatus,
    }),
  );
} catch (e) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  await writeFile(
    `${out}/failure.json`,
    JSON.stringify({ message: e.message, errors, failed, writes }, null, 2),
  );
  throw e;
} finally {
  await browser.close();
}
