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
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
});
const page = await context.newPage(),
  errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  const login = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(login.ok());
  const token = (await login.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      headers,
      data,
    });
    assert.ok(r.ok(), `API ${method} ${r.status()}`);
    return r.status() === 204 ? null : r.json();
  };
  const project = await call("post", "/projects", {
      name: "故事工作区验收 · 章节与设定",
      code: "STORY-" + Date.now(),
    }),
    root = `/projects/${project.id}`;
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(`${base}${root}/narrative`);
  await page.getByText("导入小说，开始阅读与改编").waitFor();
  const source =
    "第一章 海港来信\n" +
    "雾笼罩着码头。林舟看着远处的灯塔，等待一艘迟来的船。\n\n".repeat(16) +
    "第二章 黎明归途\n天亮时，船终于回来了。\n第三章 再次启程\n他们把信留在灯塔，踏上新的旅程。";
  await page
    .locator("input[type=file]")
    .setInputFiles({
      name: "海港来信.txt",
      mimeType: "text/plain",
      buffer: Buffer.from(source),
    });
  await page
    .getByRole("heading", { name: "第一章 海港来信", exact: true })
    .waitFor();
  assert.equal(await page.locator(".story-suggestions").count(), 0);
  assert.equal(
    await page
      .getByRole("button", { name: "上一章", exact: true })
      .isDisabled(),
    true,
  );
  const novel = (await call("get", root + "/novels"))[0];
  const detail = await call("get", root + "/novels/" + novel.id);
  assert.equal(detail.chapters.length, 3);
  await page.getByLabel("正文字号").selectOption("20");
  assert.equal(
    await page
      .locator(".story-reading-page")
      .evaluate((el) => getComputedStyle(el).fontSize),
    "20px",
  );
  await page
    .locator(".story-reader-scroll")
    .evaluate((el) => (el.scrollTop = 500));
  await page.getByRole("button", { name: "下一章", exact: true }).click();
  await page
    .getByRole("heading", { name: "第二章 黎明归途", exact: true })
    .waitFor();
  assert.equal(
    await page.locator(".story-reader-scroll").evaluate((el) => el.scrollTop),
    0,
  );
  await page.getByRole("textbox", { name: "搜索章节", exact: true }).fill("3");
  assert.equal(await page.locator(".story-chapter-list button").count(), 1);
  await page.locator(".story-chapter-list button").click();
  await page
    .getByRole("heading", { name: "第三章 再次启程", exact: true })
    .waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "下一章", exact: true })
      .isDisabled(),
    true,
  );
  await page.reload();
  await page
    .getByRole("heading", { name: "第三章 再次启程", exact: true })
    .waitFor();
  assert.equal(await page.getByLabel("正文字号").inputValue(), "20");
  await page.getByRole("button", { name: "上一章", exact: true }).click();
  await page.getByRole("button", { name: "上一章", exact: true }).click();
  await page.getByLabel("正文字号").selectOption("16");
  await page.screenshot({
    path: `${out}/reading-dark.png`,
    animations: "disabled",
  });
  // Only strategy selection is changed for this request; the backend really creates the script using its offline adapter.
  await page.route(`**/api/v1${root}/scripts/from-chapter`, (route) =>
    route.continue({
      postData: JSON.stringify({
        ...route.request().postDataJSON(),
        strategy: "mock",
      }),
    }),
  );
  await page.getByRole("button", { name: "AI 生成剧本", exact: true }).click();
  await page.waitForURL(`**${root}/scripts/*`);
  const scriptId = new URL(page.url()).pathname.split("/").pop();
  assert.equal(
    (await call("get", root + "/scripts/" + scriptId)).source_chapter_id,
    detail.chapters[0].id,
  );
  await page.goto(`${base}${root}/bible`);
  await page
    .getByRole("heading", { name: "为故事建立第一条设定", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "新建设定", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "新建设定", exact: true });
  await dialog.getByLabel("设定名称", { exact: true }).fill("潮汐之城");
  await dialog
    .getByLabel("设定正文", { exact: true })
    .fill(
      "小镇建在海边，居民靠灯塔辨别方向。\n".repeat(8) +
        "最后一条：每年秋分举行归航仪式。",
    );
  await dialog.getByRole("button", { name: "保存设定", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  let card = page
    .locator(".story-setting-card")
    .filter({
      has: page.getByRole("heading", { name: "潮汐之城", exact: true }),
    });
  await card.waitFor();
  await card.getByRole("button", { name: "阅读全文", exact: true }).click();
  assert.equal(
    await card
      .locator(".story-setting-content")
      .evaluate((el) => getComputedStyle(el).webkitLineClamp),
    "none",
  );
  await card
    .getByRole("button", { name: "编辑设定：潮汐之城", exact: true })
    .click();
  dialog = page.getByRole("dialog", { name: "编辑设定", exact: true });
  await dialog.getByLabel("设定名称", { exact: true }).fill("潮汐之城 · 修订");
  await page.keyboard.press("Escape");
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "返回编辑", exact: true })
    .click();
  assert.equal(
    await dialog.getByLabel("设定名称", { exact: true }).inputValue(),
    "潮汐之城 · 修订",
  );
  const setting = (await call("get", root + "/settings"))[0];
  let failSave = true;
  await page.route(`**/api/v1${root}/settings/${setting.id}`, (route) =>
    failSave && route.request().method() === "PATCH"
      ? route.fulfill({
          status: 503,
          json: { error: { code: "QA", message: "验收保存失败" } },
        })
      : route.continue(),
  );
  await dialog.getByRole("button", { name: "保存设定", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  assert.equal(
    await dialog.getByLabel("设定名称", { exact: true }).inputValue(),
    "潮汐之城 · 修订",
  );
  failSave = false;
  await dialog.getByRole("button", { name: "保存设定", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(
    (await call("get", root + "/settings"))[0].name,
    "潮汐之城 · 修订",
  );
  await page.getByLabel("设定来源小说").selectOption(novel.id);
  await page.getByRole("button", { name: "新建设定", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "新建设定", exact: true });
  await dialog.getByLabel("设定名称", { exact: true }).fill("林舟");
  await dialog.getByLabel("分类", { exact: true }).selectOption("character");
  await dialog
    .getByLabel("设定正文", { exact: true })
    .fill("灯塔守望者。喜欢蓝色外套，总是随身带着一封旧信。");
  await dialog.getByRole("button", { name: "保存设定", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await page
    .getByRole("button", { name: "转为资产：林舟", exact: true })
    .click();
  await page.getByText("已转为资产").waitFor();
  const assets = await call("get", root + "/assets");
  assert.ok(assets.some((a) => a.name === "林舟"));
  await page.getByLabel("设定来源小说").selectOption("");
  await page
    .getByRole("textbox", { name: "搜索设定", exact: true })
    .fill("蓝色外套");
  await page.locator(".story-setting-card").first().waitFor();
  assert.equal(await page.locator(".story-setting-card").count(), 1);
  await page
    .getByRole("textbox", { name: "搜索设定", exact: true })
    .fill("没有这个设定");
  await page
    .getByRole("heading", { name: "没有匹配的设定", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "清除筛选", exact: true })
    .first()
    .click();
  await page.getByLabel("按来源章节筛选").fill("8-3");
  await page
    .getByText("请输入正整数章节号，或从小到大的范围，例如 5-8。")
    .waitFor();
  await page.getByLabel("按来源章节筛选").fill("");
  await page
    .getByRole("navigation", { name: "设定分类" })
    .getByRole("button", { name: /^人物/ })
    .click();
  assert.equal(await page.locator(".story-setting-card").count(), 1);
  await page
    .getByRole("navigation", { name: "设定分类" })
    .getByRole("button", { name: /^全部/ })
    .click();
  await page
    .locator("[role=status]")
    .filter({ hasText: "已转为资产" })
    .waitFor({ state: "hidden" });
  await page.screenshot({
    path: `${out}/bible-dark.png`,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "从小说提取", exact: true }).click();
  await page.getByLabel("设定来源小说").selectOption(novel.id);
  await page.getByLabel("起始章", { exact: true }).fill("5");
  await page.getByLabel("结束章", { exact: true }).fill("2");
  assert.equal(
    await page
      .getByRole("button", { name: "开始提取", exact: true })
      .isDisabled(),
    true,
  );
  await page.getByRole("button", { name: "从小说提取", exact: true }).click();
  await page.getByLabel("设定来源小说").selectOption("");
  await page.evaluate(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  });
  await page.screenshot({
    path: `${out}/bible-light.png`,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: `${out}/bible-mobile.png`,
    animations: "disabled",
  });
  assert.ok(
    await page
      .locator("#main-content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  );
  await page
    .getByRole("button", { name: "编辑设定：林舟", exact: true })
    .click();
  dialog = page.getByRole("dialog", { name: "编辑设定", exact: true });
  await dialog
    .getByLabel("设定正文", { exact: true })
    .fill("手机上补充的人物设定。");
  await page.screenshot({
    path: `${out}/setting-editor-mobile.png`,
    animations: "disabled",
  });
  await dialog.getByRole("button", { name: "保存设定", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.goto(`${base}${root}/narrative`);
  await page
    .getByRole("heading", { name: "第一章 海港来信", exact: true })
    .waitFor();
  await page.screenshot({
    path: `${out}/reading-mobile.png`,
    animations: "disabled",
  });
  assert.ok(
    await page
      .locator("#main-content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  );
  // Actual novel lifecycle, only within this QA project.
  assert.equal(await page.locator(".story-library").isVisible(), false);
  await page.getByRole("button", { name: /^展开目录/ }).click();
  await page.getByRole("button", { name: "删除小说", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "删除", exact: true })
    .click();
  await page.getByRole("button", { name: "查看回收站", exact: true }).click();
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await page.getByRole("button", { name: "返回小说列表", exact: true }).click();
  assert.equal((await call("get", root + "/novels")).length, 1);
  await page.route(`**/api/v1${root}/settings`, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({
          status: 503,
          json: { error: { code: "QA", message: "设定读取失败验收" } },
        })
      : route.continue(),
  );
  await page.goto(`${base}${root}/bible`);
  await page
    .getByRole("alert")
    .filter({ hasText: "设定读取失败验收" })
    .waitFor();
  assert.equal(
    await page
      .getByRole("heading", { name: "为故事建立第一条设定", exact: true })
      .count(),
    0,
  );
  await page.unroute(`**/api/v1${root}/settings`);
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await page.locator(".story-setting-card").first().waitFor();
  assert.deepEqual(errors, []);
  await writeFile(
    `${out}/report.json`,
    JSON.stringify(
      {
        passed: true,
        project: project.id,
        novel: novel.id,
        script: scriptId,
        checks: [
          "Real novel import, chapter search/paging/font/reload",
          "Offline adaptation creates a real linked script",
          "Setting create/read/edit, failure retry, dirty close guard",
          "Novel scope, category/content filters and validation",
          "Asset conversion and actual delete/restore",
          "Dark/light/mobile editor and overflow",
          "Catalog failure distinct from empty",
        ],
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Story workspace live checks passed");
} catch (e) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  throw e;
} finally {
  await browser.close();
}
