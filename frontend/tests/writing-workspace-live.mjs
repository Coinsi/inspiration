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
  viewport: { width: 1600, height: 1050 },
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
    assert.ok(r.ok(), `Fixture request failed: ${method} ${r.status()}`);
    return r.json();
  };
  const project = await call("post", "/projects", {
    name: "剧本工作区验收 · 阅读与专注",
    code: "WRITING-" + Date.now(),
  });
  const root = `/projects/${project.id}`;
  const first = await call("post", root + "/scripts", {
    title: "夜航 · 第一次抵达",
  });
  const second = await call("post", root + "/scripts", { title: "雨停之前" });
  const blocks = [
    { block_type: "scene_heading", text: "外景 海港 夜" },
    {
      block_type: "action",
      text: "雾笼罩着码头。林舟站在岸边，等一艘迟来的船。远处的灯塔划过海面。",
    },
    { block_type: "character", text: "林舟" },
    { block_type: "dialogue", text: "你说过，天亮之前一定会回来。" },
    { block_type: "scene_heading", text: "内景 驾驶室 黎明" },
    { block_type: "action", text: "无线电突然响起。她伸手调低了音量。" },
  ];
  const current = await call("get", root + "/scripts/" + first.id);
  await call("put", root + "/scripts/" + first.id + "/blocks", {
    expected_revision: current.content_revision,
    blocks,
  });
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(`${base}${root}/scripts`);
  await page.locator(".writing-script-card").first().waitFor();
  assert.equal(await page.locator(".writing-script-card").count(), 2);
  await page
    .getByRole("textbox", { name: "搜索剧本", exact: true })
    .fill("夜航");
  assert.equal(await page.locator(".writing-script-card").count(), 1);
  await page
    .getByRole("textbox", { name: "搜索剧本", exact: true })
    .fill("不存在的名字");
  await page.getByText("没有找到匹配的剧本").waitFor();
  await page.getByRole("button", { name: "清除搜索", exact: true }).click();
  await page.getByLabel("剧本排序").selectOption("title");
  await page.screenshot({
    path: `${out}/library-dark.png`,
    animations: "disabled",
  });
  await page.getByRole("link", { name: /夜航.*打开剧本/ }).click();
  const outline = page.getByRole("navigation", { name: "剧本场景大纲" });
  await outline.waitFor();
  await page
    .getByRole("textbox", { name: "搜索场景", exact: true })
    .fill("驾驶室");
  assert.equal(await outline.getByRole("button").count(), 1);
  await outline.getByRole("button").click();
  assert.equal(
    await page.evaluate(() => document.activeElement.value),
    "内景 驾驶室 黎明",
  );
  assert.equal(
    await outline.getByRole("button").getAttribute("aria-current"),
    "location",
  );
  await page.getByRole("textbox", { name: "搜索场景", exact: true }).fill("");
  await page
    .locator(".writing-paper-scroll")
    .evaluate((el) => (el.scrollTop = 0));
  await page.screenshot({
    path: `${out}/editor-dark.png`,
    animations: "disabled",
  });
  const assistantInput = page.locator(".writing-assistant textarea");
  await assistantInput.fill("待发送的创作想法");
  await page.getByRole("button", { name: "专注写作", exact: true }).click();
  assert.equal(await page.locator(".writing-assistant").isVisible(), false);
  assert.equal(await page.locator(".studio-sidebar").isVisible(), false);
  const focusTitle = await page
    .locator(".writing-editor-header h1")
    .boundingBox();
  assert.ok(focusTitle && focusTitle.x > 0 && focusTitle.x < 200);
  const action = page.getByPlaceholder("动作", { exact: true }).first();
  await action.fill("林舟抬头，看见第一缕晨光。");
  await page.screenshot({
    path: `${out}/focus-dark.png`,
    animations: "disabled",
  });
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "专注写作", exact: true }).waitFor();
  assert.equal(await assistantInput.inputValue(), "待发送的创作想法");
  assert.equal(await action.inputValue(), "林舟抬头，看见第一缕晨光。");
  const saved = page.waitForResponse(
    (r) => r.request().method() === "PUT" && r.url().endsWith("/blocks"),
  );
  await page.keyboard.press("Control+s");
  assert.ok((await saved).ok());
  await page.reload();
  assert.equal(await action.inputValue(), "林舟抬头，看见第一缕晨光。");
  await page.evaluate(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  });
  await page.screenshot({
    path: `${out}/editor-light.png`,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: `${out}/editor-mobile.png`,
    animations: "disabled",
  });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  assert.ok(
    await page
      .locator("#main-content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  );
  await page.getByRole("button", { name: "专注写作", exact: true }).click();
  await action.fill("手机上也可以继续写。");
  await page.getByRole("button", { name: "退出专注", exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page
    .getByRole("button", { name: "保存", exact: true })
    .waitFor({ state: "visible" });
  await page.waitForFunction(() =>
    document
      .querySelector(".writing-editor-header")
      .textContent.includes("已保存到项目"),
  );
  await page.getByRole("button", { name: "返回剧本库", exact: true }).click();
  await page.locator(".writing-script-card").first().waitFor();
  await page.screenshot({
    path: `${out}/library-mobile.png`,
    animations: "disabled",
  });
  // Failure state is injected only for the directory read, not for writing/save tests above.
  await page.route(`**/api/v1${root}/scripts`, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ status: 503, json: { detail: "验收读取失败" } })
      : route.continue(),
  );
  await page.reload();
  await page
    .getByRole("alert")
    .getByRole("button", { name: "重新读取剧本", exact: true })
    .waitFor();
  assert.equal(await page.getByText("你的第一场戏，从这里开始").count(), 0);
  await page.unroute(`**/api/v1${root}/scripts`);
  await page.getByRole("button", { name: "重新读取剧本", exact: true }).click();
  await page.locator(".writing-script-card").first().waitFor();
  // Delete via keyboard; opening the delete action must not navigate into the card.
  const deleteButton = page.getByRole("button", {
    name: `删除剧本：${second.title}`,
    exact: true,
  });
  await deleteButton.focus();
  await page.keyboard.press("Enter");
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  assert.equal((await call("get", root + "/scripts")).length, 2);
  assert.deepEqual(errors, []);
  await writeFile(
    `${out}/report.json`,
    JSON.stringify(
      {
        passed: true,
        project: project.id,
        script: first.id,
        checks: [
          "Library search/sort/no matches",
          "Outline search, focus and active scene",
          "Focus mode preserves editor and assistant input",
          "Save and reload",
          "Dark/light/mobile without horizontal overflow",
          "Read failure distinct from empty and retry",
          "Keyboard delete cancellation",
        ],
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Writing workspace live checks passed");
} catch (e) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  throw e;
} finally {
  await browser.close();
}
