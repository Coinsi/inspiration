import { toggleAccountTheme } from "./helpers/account.mjs";
// Read-only smoke check against a running app. Authentication is the only POST.
// PLAYWRIGHT_MODULE points to an installed Playwright package; no bundled dependency required.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.APP_URL || "http://127.0.0.1:5187";
const output = process.env.ARTIFACT_DIR || "./test-results/workspace";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1100 },
});
const page = await context.newPage();
const errors = [],
  writes = [],
  failedRequests = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("response", (r) => {
  if (r.url().includes("/api/") && r.status() >= 400)
    failedRequests.push({ url: new URL(r.url()).pathname, status: r.status() });
});
await context.route("**/api/**", (route) => {
  const request = route.request();
  if (
    !["GET", "HEAD"].includes(request.method()) &&
    !request.url().endsWith("/auth/login")
  ) {
    writes.push({
      method: request.method(),
      path: new URL(request.url()).pathname,
    });
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
  assert.equal(auth.status(), 200, "Existing test account must be available");
  const { access_token } = await auth.json();
  await context.addInitScript((token) => {
    localStorage.setItem("inspiration_token", token);
    localStorage.setItem("inspiration_lang", "zh");
  }, access_token);
  await page.goto(`${base}/projects`);
  await page
    .getByRole("heading", { name: "每个故事，都从这里开始。" })
    .waitFor();
  const projects = await (
    await context.request.get(`${base}/api/v1/projects`, {
      headers: { Authorization: `Bearer ${access_token}` },
    })
  ).json();
  assert.ok(projects.length, "Need at least one real project");
  const project =
    projects.find((p) => p.id === process.env.TEST_PROJECT_ID) || projects[0];
  await page.getByLabel("搜索项目", { exact: true }).fill(project.name);
  await page
    .getByRole("link")
    .filter({ hasText: project.code })
    .first()
    .waitFor();
  await page.screenshot({ path: `${output}/projects.png`, fullPage: true });
  await page.goto(`${base}/projects/${project.id}/workbench`);
  await page.getByRole("link", { name: "继续创作", exact: true }).waitFor();
  await page.getByRole("progressbar").waitFor();
  if (await page.getByLabel("章节范围").count()) await page.waitForFunction(() => !document.querySelector('[aria-label="章节范围"]')?.disabled);
  await page.waitForFunction(() =>
    [...document.images].every((img) => img.complete),
  );
  await page.screenshot({ path: `${output}/workbench-dark.png` });
  const stats = await page.locator(".workbench-stage").allTextContents();
  await page.getByRole("button", { name: "角色与资产", exact: true }).click();
  await page.getByLabel("筛选当前内容").fill("__no_such_asset__");
  await page.getByText("没有匹配的内容", { exact: true }).waitFor();
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  await page.keyboard.press("Control+k");
  await page.getByRole("dialog").waitFor();
  await page
    .getByRole("textbox", { name: "查找功能", exact: true })
    .fill("剪辑");
  assert.equal(await page.locator("[data-destination]").count(), 1);
  await page.screenshot({ path: `${output}/quick-navigation.png` });
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await page.waitForURL("**/cuts");
  await page.goto(`${base}/projects/${project.id}/workbench`);
  await page.getByRole("link", { name: "继续创作", exact: true }).waitFor();
  await toggleAccountTheme(page);
  await page.screenshot({ path: `${output}/workbench-light.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "No horizontal viewport overflow",
  );
  await page.screenshot({ path: `${output}/workbench-mobile.png` });
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await page.getByRole("dialog", { name: "项目导航" }).waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(
    await page
      .getByRole("button", { name: "打开导航", exact: true })
      .evaluate((el) => el === document.activeElement),
    true,
  );
  await page.setViewportSize({ width: 1440, height: 1100 });
  const checkedRoutes = [];
  for (const route of ["narrative", "bible", "scripts", "assets", "prompts", "storyboard", "shots", "tasks", "members", "settings", "assets/trash", "cuts"]) {
    await page.goto(`${base}/projects/${project.id}/${route}`);
    await page.locator("#main-content").waitFor();
    await page.waitForLoadState("networkidle");
    assert.ok(await page.locator("#main-content").innerText(), `${route} must render content`);
    checkedRoutes.push(route);
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  assert.deepEqual(failedRequests, []);
  await writeFile(
    `${output}/live-report.json`,
    JSON.stringify(
      {
        passed: true,
        projectId: project.id,
        stats,
        checkedRoutes,
        errors,
        writes,
        failedRequests,
      },
      null,
      2,
    ),
  );
  console.log(
    JSON.stringify({ passed: true, stats, errors, writes, failedRequests }),
  );
} finally {
  await browser.close();
}
