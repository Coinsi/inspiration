// Real Chromium screenshots with browser-only API fixtures. No backend needed.
// APP_URL, ARTIFACT_DIR and PLAYWRIGHT_MODULE follow the existing test convention.
// MOBILE_STRICT=1 fails on page errors / document overflow after saving evidence.
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import assert from "node:assert/strict";
import { installMobileFixture, projectId } from "./helpers/mobile-fixture.mjs";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(resolve(process.env.PLAYWRIGHT_MODULE)).href : "playwright");
const base = process.env.APP_URL || "http://127.0.0.1:24199";
const out = resolve(process.env.ARTIFACT_DIR || `test-results/mobile-${new Date().toISOString().replaceAll(/[:.]/g, "-")}`);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome", args: ["--enable-unsafe-swiftshader"] });
const routes = [
  ["projects", "/projects"], ["chat", "/workbench"], ["overview", "/workbench?view=overview"],
  ["chat-history", "/workbench?conversation=chat-fixture"],
  ["assets", "/assets"], ["materials", "/materials"], ["library", "/library"], ["tasks", "/tasks"],
  ["canvas", "/canvas?canvas=canvas-1"], ["director", "/director"], ["cuts", "/cuts?timeline=timeline-1"],
  ["skills", "/skills"], ["prompts", "/prompts"], ["settings", "/settings"],
];
const variants = [
  { name: "phone-390-dark", viewport: { width: 390, height: 844 }, theme: "dark", isMobile: true, hasTouch: true },
  { name: "phone-360-light", viewport: { width: 360, height: 800 }, theme: "light", isMobile: true, hasTouch: true },
  { name: "desktop-1440-dark", viewport: { width: 1440, height: 1000 }, theme: "dark" },
].filter(v => !process.env.MOBILE_VARIANTS || process.env.MOBILE_VARIANTS.split(",").includes(v.name));
const cases = [];
const interactions = [];
const fixtureAudits = [];
async function saveReport() {
  await writeFile(resolve(out, "report.json"), JSON.stringify({ base, fixtureOnly: true, generatedAt: new Date().toISOString(), cases, interactions, fixtureAudits }, null, 2));
}
async function phoneInteractions(context, variant, repeat) {
  const page = await context.newPage(), errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.setDefaultTimeout(12000);
  const shot = async name => page.screenshot({ path: resolve(out, `${variant.name}-${name}${repeat > 1 ? `-${repeat}` : ""}.png`), fullPage: true, animations: "disabled" });
  const visit = path => page.goto(`${base}${path === "/projects" ? path : `/projects/${projectId}${path}`}`, { waitUntil: "networkidle" });
  const bounded = async locator => {
    await page.waitForFunction(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.x >= -1 && r.right <= innerWidth + 1;
    }, await locator.elementHandle());
    const bounds = await locator.boundingBox();
    assert.ok(bounds && bounds.x >= -1 && bounds.x + bounds.width <= variant.viewport.width + 1, "interactive surface fits mobile width");
  };
  const result = { variant: variant.name, repeat, checks: [], errors };
  try {
    await visit("/workbench");
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
    const nav = page.getByRole("dialog", { name: "项目导航", exact: true });
    await nav.waitFor({ state: "visible" });
    await bounded(nav);
    await shot("navigation-open");
    await page.keyboard.press("Escape");
    await nav.waitFor({ state: "hidden" });
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "打开导航", undefined, { timeout: 3000 });
    assert.equal(await page.getByRole("button", { name: "打开导航", exact: true }).evaluate(el => el === document.activeElement), true);
    result.checks.push("navigation opens, fits, closes with Escape and restores focus");
    const draft = `手机草稿 ${repeat}：从雨夜重逢开始，安排三个开场镜头。`;
    await page.getByLabel("对话消息", { exact: true }).fill(draft);
    await page.reload({ waitUntil: "networkidle" });
    await page.getByLabel("对话消息", { exact: true }).waitFor();
    assert.equal(await page.getByLabel("对话消息", { exact: true }).inputValue(), draft);
    await page.setViewportSize({ width: variant.viewport.width, height: 520 });
    await page.getByLabel("对话消息", { exact: true }).click();
    await bounded(page.getByLabel("对话消息", { exact: true }));
    await page.getByRole("button", { name: "发送消息", exact: true }).scrollIntoViewIfNeeded();
    const send = await page.getByRole("button", { name: "发送消息", exact: true }).boundingBox();
    assert.ok(send && send.y >= 0 && send.y + send.height <= 521, "send control remains reachable with short viewport");
    await shot("chat-short-viewport");
    await page.setViewportSize(variant.viewport);
    result.checks.push("chat draft survives refresh; composer and send are reachable at 520px height");
    await visit("/projects");
    await page.getByRole("button", { name: "新建项目", exact: true }).last().click();
    const modal = page.getByRole("dialog", { name: "新建项目", exact: true });
    await modal.waitFor();
    await page.getByLabel("项目名称", { exact: true }).fill("手机创作草稿");
    await bounded(modal);
    await shot("new-project-dialog");
    await modal.getByRole("button", { name: "关闭", exact: true }).click();
    await modal.waitFor({ state: "hidden" });
    result.checks.push("new-project modal fits and can be dismissed without writing");
    await visit("/assets");
    await page.getByRole("button", { name: "列表", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "列表", exact: true }).getAttribute("aria-pressed"), "true");
    await shot("assets-list");
    await page.getByRole("button", { name: "按标签筛选", exact: true }).click();
    const filter = page.getByRole("listbox", { name: "按标签筛选", exact: true });
    await filter.waitFor();
    await bounded(filter);
    await shot("assets-filter");
    await filter.getByRole("option", { name: "开场", exact: true }).click();
    await page.getByRole("button", { name: "林间旅人的更多操作", exact: true }).click();
    await bounded(page.getByRole("menu", { name: "林间旅人的更多操作", exact: true }));
    await page.getByRole("menuitem", { name: "查看引用镜头", exact: true }).waitFor();
    await page.keyboard.press("Escape");
    result.checks.push("asset list, tag choices and item actions fit the mobile viewport");
    await visit("/skills");
    await page.getByRole("button", { name: "查看技能 电影镜头设计", exact: true }).click();
    const reader = page.getByRole("dialog", { name: "电影镜头设计", exact: true });
    await reader.waitFor();
    await bounded(reader);
    await shot("skill-reader");
    await page.getByRole("button", { name: "切换文件目录", exact: true }).click();
    await page.getByRole("button", { name: "references/shot-guide.md", exact: true }).waitFor({ state: "visible" });
    await shot("skill-files");
    await page.getByRole("button", { name: "references/shot-guide.md", exact: true }).click();
    await page.getByRole("heading", { name: "镜头参考", exact: true }).waitFor();
    await shot("skill-reference");
    await reader.getByRole("button", { name: "关闭", exact: true }).click();
    await reader.waitFor({ state: "hidden" });
    result.checks.push("skill reader opens, file drawer switches reference, closes");
    await visit("/canvas?canvas=canvas-1");
    await page.getByRole("button", { name: "添加内容", exact: true }).click();
    await page.locator(".canvas-add-menu").waitFor();
    await bounded(page.locator(".canvas-add-menu"));
    await shot("canvas-add-menu");
    await page.getByRole("button", { name: "添加内容", exact: true }).click();
    await page.locator(".canvas-add-menu").waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
    const canvasPanel = page.getByLabel("画布属性与记录", { exact: true });
    await canvasPanel.waitFor();
    await bounded(canvasPanel);
    await shot("canvas-inspector");
    await page.getByRole("button", { name: "关闭画布面板", exact: true }).click();
    await canvasPanel.waitFor({ state: "hidden" });
    const canvasName = `手机画布草稿 ${repeat}`;
    await page.getByLabel("画布名称", { exact: true }).fill(canvasName);
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await page.getByText("Browser fixture blocks server mutations", { exact: false }).first().waitFor();
    assert.equal(await page.getByLabel("画布名称", { exact: true }).inputValue(), canvasName);
    result.checks.push("canvas add menu and inspector open/close; failed save preserves name draft");
    await visit("/director");
    await page.getByRole("button", { name: "预演场景", exact: true }).click();
    await page.getByRole("option", { name: /窗边对话预演/ }).click();
    await page.getByRole("button", { name: "场景与布景", exact: true }).click();
    await page.getByRole("button", { name: "旅人", exact: true }).click();
    await shot("director-scene-panel");
    await page.getByRole("button", { name: "对象与机位", exact: true }).click();
    const objectName = `手机对象 ${repeat}`;
    await page.getByLabel("对象名称", { exact: true }).fill(objectName);
    await bounded(page.getByLabel("对象名称", { exact: true }));
    await shot("director-object-panel");
    await page.getByRole("button", { name: "画面与输出", exact: true }).click();
    await page.locator(".director-viewport canvas").first().waitFor({ state: "visible" });
    await page.getByRole("button", { name: "对象与机位", exact: true }).click();
    assert.equal(await page.getByLabel("对象名称", { exact: true }).inputValue(), objectName);
    await page.getByRole("button", { name: "保存场景", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "Browser fixture blocks server mutations" }).waitFor();
    assert.equal(await page.getByLabel("对象名称", { exact: true }).inputValue(), objectName);
    result.checks.push("director switches all 3 mobile panels; object edit survives switches and failed save");
    await visit("/cuts?timeline=timeline-1");
    await page.getByRole("button", { name: "联动修剪", exact: true }).click();
    await page.getByRole("button", { name: "预览片段 1", exact: true }).click();
    const properties = page.getByLabel("剪辑属性", { exact: true });
    await properties.waitFor();
    await bounded(properties);
    await properties.evaluate(el => el.scrollIntoView({ block: "start", behavior: "instant" }));
    await page.getByLabel("时长 1", { exact: true }).waitFor({ state: "visible" });
    await shot("cuts-clip-properties");
    await page.getByLabel("时长 1", { exact: true }).fill("4");
    await page.getByLabel("时长 1", { exact: true }).press("Enter");
    await page.getByRole("button", { name: "收起属性", exact: true }).click();
    await properties.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "预览片段 1", exact: true }).click();
    await properties.waitFor();
    assert.equal(await page.getByLabel("时长 1", { exact: true }).inputValue(), "4");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await page.getByText("Browser fixture blocks server mutations", { exact: false }).first().waitFor();
    assert.equal(await page.getByLabel("时长 1", { exact: true }).inputValue(), "4");
    await page.reload({ waitUntil: "networkidle" });
    await page.getByRole("button", { name: "预览片段 1", exact: true }).click();
    await page.getByLabel("时长 1", { exact: true }).waitFor({ state: "visible" });
    assert.equal(await page.getByLabel("时长 1", { exact: true }).inputValue(), "4");
    result.checks.push("timeline clip inspector opens/closes; duration edit survives close, failed save and refresh from local draft");
    assert.deepEqual(errors, []);
    result.passed = true;
  } catch (e) { result.passed = false; result.failure = e.message; await shot("interaction-failure").catch(() => {}); }
  interactions.push(result);
  await saveReport();
  console.log(`${result.passed ? "PASS" : "FAIL"} interactions ${variant.name} #${repeat}: ${result.failure || result.checks.join("; ")}`);
  await page.close();
}
try {
  for (const variant of variants) {
    const context = await browser.newContext({ viewport: variant.viewport, isMobile: variant.isMobile, hasTouch: variant.hasTouch, deviceScaleFactor: 1 });
    const fixture = await installMobileFixture(context, variant);
    for (const [name, path] of (process.env.MOBILE_INTERACTIONS_ONLY === "1" ? [] : routes).filter(([name]) => !process.env.MOBILE_PAGES || process.env.MOBILE_PAGES.split(",").includes(name))) {
      const page = await context.newPage(), errors = [];
      page.on("pageerror", e => errors.push(e.message));
      const item = { variant: variant.name, name, errors };
      try {
        await page.goto(`${base}${name === "projects" ? path : `/projects/${projectId}${path}`}`, { waitUntil: "networkidle" });
        await page.waitForFunction(() => document.body.innerText.length > 100);
        if (name === "director") {
          await page.getByRole("button", { name: "预演场景", exact: true }).click();
          await page.getByRole("option", { name: /窗边对话预演/ }).click();
        }
        await page.waitForTimeout(name === "director" ? 700 : 250);
        item.metrics = await page.evaluate(() => {
          const width = document.documentElement.clientWidth;
          const overflows = [...document.querySelectorAll("body *")].flatMap(el => {
            const r = el.getBoundingClientRect(), style = getComputedStyle(el);
            if (r.width <= 0 || r.height <= 0 || style.visibility === "hidden" || style.display === "none" || r.right <= width + 1 && r.left >= -1) return [];
            if (r.bottom < 0 || r.top > innerHeight) return [];
            return [{ tag: el.tagName, className: String(el.className).slice(0, 150), label: (el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 65), left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width) }];
          }).slice(0, 35);
          return { viewport: width, document: document.documentElement.scrollWidth, body: document.body.scrollWidth, textLength: document.body.innerText.length, overflows };
        });
        item.screenshot = `${variant.name}-${name}.png`;
        await page.screenshot({ path: resolve(out, item.screenshot), fullPage: true });
        item.passed = !errors.length && item.metrics.document <= variant.viewport.width + 1 && item.metrics.body <= variant.viewport.width + 1 && item.metrics.textLength > 100;
      } catch (e) { item.failure = e.message; item.passed = false; await page.screenshot({ path: resolve(out, `${variant.name}-${name}-failure.png`), fullPage: true }).catch(() => {}); }
      cases.push(item);
      await saveReport();
      console.log(`${item.passed ? "PASS" : "FAIL"} ${variant.name} ${name}: ${JSON.stringify(item.metrics ? { width: item.metrics.document, body: item.metrics.body, text: item.metrics.textLength, errors } : { failure: item.failure })}`);
      await page.close();
    }
    if (variant.isMobile && process.env.MOBILE_INTERACTIONS !== "0") {
      for (let repeat = 1; repeat <= Number(process.env.MOBILE_REPEATS || 1); repeat++) await phoneInteractions(context, variant, repeat);
    }
    await writeFile(resolve(out, `${variant.name}-requests.json`), JSON.stringify(fixture, null, 2));
    fixtureAudits.push({ variant: variant.name, unmatched: [...new Set(fixture.unmatched)], blockedWrites: fixture.blockedWrites });
    await saveReport();
    await context.close();
  }
} finally { await browser.close(); }
console.log(`Evidence: ${out}`);
if (process.env.MOBILE_STRICT === "1" && ([...cases, ...interactions].some(item => !item.passed) || fixtureAudits.some(item => item.unmatched.length))) process.exitCode = 1;
