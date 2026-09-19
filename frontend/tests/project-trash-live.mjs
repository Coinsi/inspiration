import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
await mkdir(out, { recursive: true });
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1500, height: 1040 },
  }),
  page = await context.newPage(),
  errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
let p;
try {
  const auth = await context.request.post(base + "/api/v1/auth/login", {
      data: { username: "demo", password: "demo1234" },
    }),
    token = (await auth.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](base + "/api/v1" + path, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  p = await call("post", "/projects", {
    name: "删除恢复验收 · " + Date.now(),
    description: "仅用于本轮删除恢复验证",
  });
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(base + "/projects");
  await page.getByLabel("搜索项目", { exact: true }).fill(p.name);
  const card = page.locator(`[data-project-id="${p.id}"]`);
  await card.scrollIntoViewIfNeeded();
  await card.getByRole("button", { name: /删除项目/ }).click();
  const confirmation = page.getByRole("alertdialog");
  await confirmation.waitFor();
  await page.screenshot({
    path: out + "/delete-confirm.png",
    animations: "disabled",
  });
  await confirmation.getByRole("button", { name: "取消", exact: true }).click();
  assert.equal((await call("get", "/projects/" + p.id)).id, p.id);
  checks.push(
    "Delete requires a concrete project confirmation; cancel keeps it accessible",
  );
  // First server response is lost; a repeated DELETE remains safe and completes.
  let lost = false;
  await page.route("**/projects/" + p.id, async (route) => {
    if (route.request().method() !== "DELETE" || lost) {
      await route.continue();
      return;
    }
    lost = true;
    const response = await route.fetch();
    assert.equal(response.status(), 204);
    await route.fulfill({
      status: 503,
      json: { error: { message: "验收：响应中断，请重试" } },
    });
  });
  await card.getByRole("button", { name: /删除项目/ }).click();
  await confirmation
    .getByRole("button", { name: "移入回收站", exact: true })
    .click();
  await page.getByText(/验收：响应中断，请重试/).waitFor();
  await card.getByRole("button", { name: /删除项目/ }).click();
  await confirmation
    .getByRole("button", { name: "移入回收站", exact: true })
    .click();
  await card.waitFor({ state: "hidden" });
  await page.unroute("**/projects/" + p.id);
  assert.equal(
    (
      await context.request.get(base + "/api/v1/projects/" + p.id, { headers })
    ).status(),
    403,
  );
  await page.reload();
  await page.getByLabel("搜索项目", { exact: true }).fill(p.name);
  await page.getByText("没有匹配的项目", { exact: true }).waitFor();
  assert.equal(
    await page
      .getByLabel("创作所在项目", { exact: true })
      .locator(`option[value="${p.id}"]`)
      .count(),
    0,
  );
  checks.push(
    "Deletion persists after reload, removes creation choices and blocks the old project URL; retry is idempotent",
  );
  await page.getByRole("button", { name: "回收站", exact: true }).click();
  const trash = page.getByRole("dialog", { name: "项目回收站", exact: true }),
    row = trash.locator(`[data-trash-project-id="${p.id}"]`);
  await row.waitFor();
  await page.screenshot({
    path: out + "/trash-dark.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: out + "/trash-mobile.png",
    animations: "disabled",
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 2,
    ),
    false,
  );
  await row.getByRole("button", { name: "恢复", exact: true }).click();
  await row.waitFor({ state: "hidden" });
  await trash.getByRole("button", { name: "关闭", exact: true }).click();
  await card.waitFor();
  assert.equal((await call("get", "/projects/" + p.id)).code, p.code);
  await card.getByRole("link", { name: p.name, exact: true }).click();
  await page.waitForURL("**/projects/" + p.id + "/workbench");
  checks.push(
    "Recycle-bin restore on narrow viewport restores the original project link and code",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify({ passed: true, project: p.id, checks, errors }, null, 2),
  );
  console.log(JSON.stringify({ passed: true, checks }));
} catch (e) {
  await page.screenshot({ path: out + "/failure.png", fullPage: true });
  await writeFile(
    out + "/failure.json",
    JSON.stringify(
      { error: String(e), project: p?.id, checks, errors },
      null,
      2,
    ),
  );
  throw e;
} finally {
  await browser.close();
}
