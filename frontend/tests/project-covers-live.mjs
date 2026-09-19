import { toggleAccountTheme } from "./helpers/account.mjs";
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR,
  media = process.env.TEST_MEDIA_DIR;
await mkdir(out, { recursive: true });
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1500, height: 1100 },
  }),
  page = await context.newPage(),
  errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
let created;
try {
  const login = await context.request.post(base + "/api/v1/auth/login", {
      data: { username: "demo", password: "demo1234" },
    }),
    token = (await login.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (path) => {
    const r = await context.request.get(base + "/api/v1" + path, { headers });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    if (!localStorage.getItem("inspiration_theme"))
      localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(base + "/projects");
  await page
    .getByRole("button", { name: "新建项目", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  assert.equal(await dialog.getByText("项目编码", { exact: true }).count(), 0);
  const name = "封面体验验收 · 雨夜重逢 " + Date.now();
  await dialog.getByLabel("项目名称", { exact: true }).fill(name);
  await dialog
    .getByLabel("项目简介选填", { exact: true })
    .fill("在一场雨里重逢，寻找故事的第一帧。");
  await dialog
    .getByLabel("上传项目封面", { exact: true })
    .setInputFiles({
      name: "reference-a.jpg",
      mimeType: "image/jpeg",
      buffer: await readFile(media + "/reference-a.jpg"),
    });
  await dialog.getByAltText("项目封面预览").waitFor();
  await page.screenshot({
    path: out + "/create-dark.png",
    animations: "disabled",
  });
  // Commit succeeds but its first response is lost; retry must not duplicate the project.
  let interrupted = false;
  await page.route("**/projects/with-cover", async (route) => {
    if (interrupted) {
      await route.continue();
      return;
    }
    interrupted = true;
    const response = await route.fetch();
    if (!response.ok()) {
      await route.fulfill({ response });
      return;
    }
    created = await response.json();
    await route.fulfill({
      status: 503,
      json: { error: { message: "验收：响应中断，可重试" } },
    });
  });
  await dialog.getByRole("button", { name: "创建项目", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  await dialog.getByRole("button", { name: "创建项目", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.unroute("**/projects/with-cover");
  assert.equal(
    (await call("/projects")).filter((p) => p.name === name).length,
    1,
  );
  assert.ok(created.code);
  assert.ok(created.cover_blob_hash);
  checks.push(
    "Name-first creation with original cover and response-loss retry creates exactly one project",
  );
  await page.getByLabel("搜索项目", { exact: true }).fill(name);
  const card = page.locator(`[data-project-id="${created.id}"]`);
  await card.scrollIntoViewIfNeeded();
  await card.locator("img").waitFor();
  await card.locator("img").evaluate((img) => {
    if (!img.complete) return new Promise((r) => (img.onload = r));
  });
  assert.ok(await card.locator("img").evaluate((i) => i.naturalWidth > 0));
  await page.screenshot({
    path: out + "/project-card.png",
    animations: "disabled",
  });
  await card.getByRole("button", { name: /设置封面/ }).click();
  await dialog
    .getByLabel("上传项目封面", { exact: true })
    .setInputFiles(media + "/reference-b.jpg");
  await dialog.getByRole("button", { name: "保存封面", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  let project = await call("/projects/" + created.id);
  assert.notEqual(project.cover_blob_hash, created.cover_blob_hash);
  assert.equal(project.code, created.code);
  await page.reload();
  await page.getByLabel("搜索项目", { exact: true }).fill(name);
  await card.scrollIntoViewIfNeeded();
  assert.ok(
    (await card.locator("img").getAttribute("src")).includes(
      project.cover_blob_hash,
    ),
  );
  checks.push(
    "Existing-project cover replacement persists after reload without changing project code",
  );
  await card.getByRole("button", { name: /设置封面/ }).click();
  await dialog.getByLabel("移除封面", { exact: true }).click();
  await dialog.getByRole("button", { name: "保存封面", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal((await call("/projects/" + created.id)).cover_blob_hash, null);
  assert.equal(await card.locator("img").count(), 0);
  await card.getByRole("button", { name: /设置封面/ }).click();
  await dialog
    .getByLabel("上传项目封面", { exact: true })
    .setInputFiles({
      name: "broken.png",
      mimeType: "image/png",
      buffer: Buffer.from("bad image"),
    });
  await dialog.getByRole("button", { name: "保存封面", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  assert.equal((await call("/projects/" + created.id)).cover_blob_hash, null);
  await dialog
    .getByLabel("上传项目封面", { exact: true })
    .setInputFiles(media + "/reference-a.jpg");
  await dialog.getByRole("button", { name: "保存封面", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  checks.push(
    "Remove, invalid image rejection and corrected-file retry keep project intact",
  );
  await toggleAccountTheme(page);
  assert.ok(
    await page.locator("html").evaluate((e) => e.classList.contains("light")),
  );
  await card.getByRole("button", { name: /设置封面/ }).click();
  await page.screenshot({
    path: out + "/cover-light.png",
    animations: "disabled",
  });
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("button", { name: "新建项目", exact: true })
    .last()
    .click();
  await dialog
    .getByLabel("项目名称", { exact: true })
    .fill("无封面体验验收 " + Date.now());
  await page.screenshot({
    path: out + "/create-mobile.png",
    animations: "disabled",
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 2,
    ),
    false,
  );
  const createdWithout = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.url().endsWith("/projects/with-cover"),
  );
  await dialog.getByRole("button", { name: "创建项目", exact: true }).click();
  const plain = await (await createdWithout).json();
  assert.equal(plain.cover_blob_hash, null);
  await dialog.waitFor({ state: "hidden" });
  checks.push("Optional cover, light theme and 390px creation flow");
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify(
      { passed: true, project: created.id, plain: plain.id, checks, errors },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ passed: true, checks }));
} catch (e) {
  await page.screenshot({ path: out + "/failure.png", fullPage: true });
  await writeFile(
    out + "/failure.json",
    JSON.stringify(
      { error: String(e), created: created?.id, checks, errors },
      null,
      2,
    ),
  );
  throw e;
} finally {
  await browser.close();
}
