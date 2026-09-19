import { toggleAccountTheme } from "./helpers/account.mjs";
// Isolated real persistence; only failure cases are intercepted. No cloud model calls.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
assert.ok(base && out && process.env.TEST_SOURCE);
await mkdir(out, { recursive: true });
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage(),
  errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
const check = (s) => {
  checks.push(s);
  console.log(s);
};
try {
  const auth = await context.request.post(base + "/api/v1/auth/login", {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(auth.ok());
  const token = (await auth.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](base + "/api/v1" + path, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.status() === 204 ? null : r.json();
  };
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
  }, token);
  const project = await call("post", "/projects", {
    code: "SHOT-FLOW-" + Date.now(),
    name: "创作交互验收 · 镜头与素材",
  });
  const root = `/projects/${project.id}`;
  const script = await call("post", root + "/scripts", { title: "街头片段" });
  await call("post", `${root}/scripts/${script.id}/apply-scenes`, {
    scenes: [
      {
        title: "雨夜街道",
        shots: [{ title: "镜头1", description: "街灯亮起" }],
      },
      {
        title: "清晨街道",
        shots: [{ title: "镜头1", description: "阳光穿过树叶" }],
      },
    ],
  });
  const shots = await call("get", root + "/shots");
  const asset = await call("post", root + "/assets", {
    type: "character",
    name: "雨夜旅人",
    summary: "穿着深色风衣",
  });
  const source = await readFile(process.env.TEST_SOURCE);
  let version = await call("post", root + "/library/uploads", {
    filename: "街道参考.mp4",
    size_bytes: source.length,
    fingerprint: createHash("sha256").update(source).digest("hex"),
  });
  for (let offset = 0; offset < source.length; offset += version.chunk_size) {
    const r = await context.request.post(
      `${base}/api/v1${root}/library/versions/${version.id}/chunk?offset=${offset}`,
      {
        headers,
        multipart: {
          file: {
            name: "chunk",
            mimeType: "application/octet-stream",
            buffer: source.subarray(offset, offset + version.chunk_size),
          },
        },
      },
    );
    assert.ok(r.ok(), await r.text());
  }
  await call("post", `${root}/library/versions/${version.id}/complete`, {});
  for (let i = 0; i < 100; i++) {
    version = await call("get", `${root}/library/versions/${version.id}`);
    if (["ready", "failed"].includes(version.status)) break;
    await page.waitForTimeout(500);
  }
  assert.equal(version.status, "ready");
  await page.goto(base + root + "/storyboard");
  await page
    .getByRole("link", { name: /生成与引用/ })
    .first()
    .click();
  const currentId = new URL(page.url()).searchParams.get("shot");
  assert.ok(shots.some((s) => s.id === currentId));
  const prompt = page.getByLabel("本次生成指令", { exact: true });
  await prompt.fill("保留这段尚未生成的创作草稿");
  assert.equal(await page.getByLabel("制作状态看板").isVisible(), false);
  await page.getByRole("button", { name: "参考素材", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const card = dialog.getByRole("button", {
    name: `参考资产 ${asset.name} ${asset.code}`,
    exact: true,
  });
  await page.route("**/asset-refs", async (route) => {
    if (route.request().method() === "PUT")
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "验收：保存暂不可用" }),
      });
    await route.continue();
  });
  await card.click();
  await dialog.getByRole("alert").waitFor();
  assert.equal(await card.getAttribute("aria-pressed"), "false");
  assert.equal(
    (await call("get", `${root}/shots/${currentId}/asset-refs`)).length,
    0,
  );
  await page.unroute("**/asset-refs");
  await card.click();
  await page.waitForFunction(() =>
    document.querySelector('.reference-card[aria-pressed="true"]'),
  );
  const refs = await call("get", `${root}/shots/${currentId}/asset-refs`);
  assert.equal(refs[0].asset_id, asset.id);
  check(
    "Visual reference selection persists; failed save is visible and retry does not lose state",
  );
  await page.screenshot({
    animations: "disabled",
    path: out + "/reference-assets-dark.png",
  });
  await dialog.getByRole("tab", { name: "视频片段", exact: true }).click();
  await dialog
    .getByRole("button", { name: /选择视频 街道参考.*版本 1/ })
    .click();
  await dialog
    .getByLabel("引用终点秒")
    .fill(String(version.duration_ms / 1000 + 1));
  assert.equal(
    await dialog.getByRole("button", { name: "添加到当前镜头" }).isEnabled(),
    false,
  );
  await dialog.getByLabel("引用起点秒").fill("0.2");
  await dialog.getByLabel("引用终点秒").fill("1.2");
  await dialog.getByLabel("片段引用用途").selectOption("editing_source");
  await dialog.getByRole("button", { name: "添加到当前镜头" }).click();
  await dialog
    .getByText("片段已加入当前镜头，原片版本与时间范围已保留。")
    .waitFor();
  const usages = await call(
    "get",
    `${root}/library/usages?shot_id=${currentId}`,
  );
  assert.equal(usages.length, 1);
  assert.equal(usages[0].version_id, version.id);
  assert.equal(usages[0].start_ms, 200);
  assert.equal(usages[0].end_ms, 1200);
  assert.equal(usages[0].purpose, "editing_source");
  await dialog
    .locator("video")
    .first()
    .evaluate((video) => video.play());
  await page.screenshot({
    animations: "disabled",
    path: out + "/reference-video-dark.png",
  });
  check(
    "Actual uploaded video previews; invalid range blocked; exact source version and range persisted",
  );
  await dialog.getByRole("button", { name: "完成，继续创作" }).click();
  assert.equal(await prompt.inputValue(), "保留这段尚未生成的创作草稿");
  await page.reload();
  await prompt.waitFor();
  assert.equal(await prompt.inputValue(), "保留这段尚未生成的创作草稿");
  await page.screenshot({
    animations: "disabled",
    path: out + "/shot-workspace-dark.png",
  });
  await toggleAccountTheme(page);
  await page.screenshot({
    animations: "disabled",
    path: out + "/shot-workspace-light.png",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.locator("main").evaluate((e) => e.scrollWidth <= e.clientWidth),
  );
  await page.getByRole("button", { name: "参考素材", exact: true }).click();
  await dialog.getByRole("tab", { name: "角色与场景", exact: true }).click();
  assert.ok(await dialog.evaluate((e) => e.scrollWidth <= e.clientWidth));
  await page.screenshot({
    animations: "disabled",
    path: out + "/reference-mobile.png",
  });
  await dialog.getByRole("button", { name: "完成，继续创作" }).click();
  await page.getByRole("button", { name: "返回分镜", exact: true }).click();
  await page
    .getByRole("link", { name: /生成与引用/ })
    .first()
    .waitFor();
  assert.ok(new URL(page.url()).pathname.endsWith("/storyboard"));
  check(
    "Draft survives reference dialog and reload; return to storyboard; mobile has no overflow",
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(base + root + "/agent");
  await page.getByRole("button", { name: "新的创作任务", exact: true }).click();
  await page.getByRole("button", { name: /^选择创作对象/ }).click();
  await dialog.getByLabel("搜索创作对象").fill(shots[1].code);
  const option = dialog.getByRole("button", { name: /^选择对象 / });
  assert.equal(await option.count(), 1);
  await option.click();
  await page.screenshot({
    animations: "disabled",
    path: out + "/object-picker.png",
  });
  await dialog.getByRole("button", { name: "完成选择" }).click();
  await page
    .getByText(`${shots[1].code} · ${shots[1].title}`, { exact: false })
    .first()
    .waitFor();
  assert.equal((await call("get", root + "/agent/runs")).length, 0);
  check(
    "Same-name shots distinguished by searchable code; picking a scope never starts an Agent",
  );
  await page.goto(base + root + "/prompts");
  await page.getByRole("button", { name: "新建提示词", exact: true }).click();
  await dialog.getByLabel("提示词名称").fill("雨夜霓虹");
  await dialog
    .getByLabel("提示词内容")
    .fill("霓虹映照湿润路面，冷暖光线交织。");
  await page.route("**/prompts", async (route) => {
    if (route.request().method() === "POST")
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "验收：保存失败" }),
      });
    await route.continue();
  });
  await dialog.getByRole("button", { name: "保存提示词", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  assert.equal(
    await dialog.getByLabel("提示词内容").inputValue(),
    "霓虹映照湿润路面，冷暖光线交织。",
  );
  await page.unroute("**/prompts");
  await dialog.getByRole("button", { name: "保存提示词", exact: true }).click();
  await page.getByRole("button", { name: "查看提示词 雨夜霓虹" }).click();
  await page.getByRole("button", { name: "编辑提示词", exact: true }).click();
  await dialog
    .getByLabel("提示词内容")
    .fill("霓虹映照湿润路面，镜头缓缓推近。");
  await dialog.getByRole("button", { name: "保存提示词", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(
    (await call("get", root + "/prompts"))[0].positive,
    "霓虹映照湿润路面，镜头缓缓推近。",
  );
  await page.getByRole("button", { name: "返回提示词列表" }).click();
  await page.getByRole("button", { name: "新建提示词", exact: true }).click();
  await dialog.getByLabel("提示词类型").selectOption("fragment");
  await dialog.getByLabel("片段分类").selectOption("lighting");
  await dialog.getByLabel("提示词名称").fill("柔和侧光");
  await dialog.getByLabel("提示词内容").fill("从画面左侧照入柔和晨光。");
  await dialog.getByRole("button", { name: "保存提示词", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "光线", exact: true }).click();
  assert.equal(
    await page.getByRole("button", { name: /^查看提示词 / }).count(),
    1,
  );
  await page.getByRole("button", { name: "全部", exact: true }).click();
  await page.screenshot({
    animations: "disabled",
    path: out + "/prompt-library-light.png",
  });
  await toggleAccountTheme(page);
  await page.screenshot({
    animations: "disabled",
    path: out + "/prompt-library-dark.png",
  });
  await page.goto(base + root + "/shots?shot=" + currentId);
  await page
    .getByRole("button", { name: "从提示词库添加", exact: true })
    .click();
  await dialog.getByLabel("搜索提示词").fill("霓虹");
  await dialog.getByRole("button", { name: "查看提示词 雨夜霓虹" }).click();
  await dialog.getByRole("button", { name: "追加到创作指令" }).click();
  assert.equal(
    await prompt.inputValue(),
    "保留这段尚未生成的创作草稿\n\n霓虹映照湿润路面，镜头缓缓推近。",
  );
  await prompt.fill("甲".repeat(9999));
  await page
    .getByRole("button", { name: "从提示词库添加", exact: true })
    .click();
  await dialog.getByRole("button", { name: "查看提示词 柔和侧光" }).click();
  await dialog.getByRole("button", { name: "追加到创作指令" }).click();
  await page
    .getByText("追加后超过 10000 字，请先缩短当前指令。", { exact: false })
    .waitFor();
  assert.equal((await prompt.inputValue()).length, 9999);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await dialog.evaluate((e) => e.scrollWidth <= e.clientWidth));
  await page.screenshot({
    animations: "disabled",
    path: out + "/prompt-picker-mobile.png",
  });
  check(
    "Prompt library create/edit, failed-save recovery, Chinese categories, append preserves draft, length limit and mobile",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify(
      { passed: true, project: project.id, checks, errors },
      null,
      2,
    ),
  );
} catch (e) {
  await page.screenshot({
    animations: "disabled",
    path: out + "/failure.png",
    fullPage: true,
  });
  await writeFile(
    out + "/failure.json",
    JSON.stringify({ error: String(e), errors, checks }, null, 2),
  );
  throw e;
} finally {
  await browser.close();
}
