// Browser integration tests for navigation, query failure, scope and persistence.
// All API traffic is intercepted. No real projects or providers are modified.
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
const a = "11111111-1111-4111-8111-111111111111",
  b = "22222222-2222-4222-8222-222222222222";
const projects = [
  {
    id: a,
    code: "DAWN",
    name: "黎明之前",
    description: "一部关于告别与重逢的短片",
    created_at: "2026-09-14T09:00:00Z",
    settings: {},
  },
  {
    id: b,
    code: "SEA",
    name: "远海来信",
    description: "",
    created_at: "2026-09-13T09:00:00Z",
    settings: {},
  },
];
const cases = [];
async function scenario(name, run, options = {}) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const errors = [],
    writes = [],
    requests = [];
  let broken = options.broken || "",
    empty = options.empty || false;
  page.on("pageerror", (error) => errors.push(error.message));
  await context.addInitScript(
    ({ a, b, stale }) => {
      localStorage.setItem("inspiration_token", "fixture-token");
      localStorage.setItem("inspiration_lang", "zh");
      if (localStorage.getItem(`wb.chapter.${a}`) === null)
        localStorage.setItem(
          `wb.chapter.${a}`,
          JSON.stringify(stale ? "deleted-chapter" : ""),
        );
      if (localStorage.getItem(`wb.chapter.${b}`) === null)
        localStorage.setItem(`wb.chapter.${b}`, JSON.stringify(""));
    },
    { a, b, stale: options.stale },
  );
  await context.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname.replace("/api/v1", "");
    requests.push(`${path}${url.search}`);
    const respond = (body, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (request.method() !== "GET") {
      writes.push(path);
      return respond({ error: { message: "Write blocked by test" } }, 403);
    }
    if (path === "/me")
      return respond({
        user: {
          id: "user",
          username: "test",
          display_name: "创作者",
          is_active: true,
        },
        memberships: [],
      });
    if (path === "/projects")
      return broken === "projects"
        ? respond({ error: { message: "Unavailable" } }, 503)
        : respond(projects);
    const pieces = path.split("/").filter(Boolean),
      projectId = pieces[1],
      collection = pieces[2];
    if (!collection) return respond(projects.find((p) => p.id === projectId));
    if (broken === collection)
      return respond({ error: { message: "Fixture failure" } }, 503);
    if (options.delayShots && collection === "shots")
      await new Promise((resolve) => setTimeout(resolve, 1800));
    if (collection === "quota")
      return respond({ used_cost: 12, limit_cost: 100 });
    if (collection === "novels") {
      if (pieces[3])
        return respond({
          id: pieces[3],
          title: "故事",
          chapters: [
            {
              id: `${projectId}-ch1`,
              ordinal: 1,
              title: "序章",
              content: "正文",
            },
          ],
        });
      return respond(
        empty ? [] : [{ id: `${projectId}-novel`, title: "故事", code: "NOV" }],
      );
    }
    if (collection === "scripts")
      return respond(
        empty ? [] : [{ id: "script", title: "剧本", code: "SCR" }],
      );
    if (collection === "assets")
      return respond(
        empty || url.searchParams.has("chapter_id")
          ? []
          : [
              {
                id: "asset",
                name: projectId === a ? "林间旅人" : "海边旅人",
                code: "CHAR",
                type: "character",
                summary: "一位旅人",
                tags: [],
                representative_blob_hash: null,
              },
            ],
      );
    if (collection === "shots")
      return respond(
        empty || url.searchParams.has("chapter_id")
          ? []
          : [
              {
                id: "shot1",
                title: "晨光",
                code: "S01",
                description: "林间的晨光",
                production_status: "approved",
                selected_generation_id: "gen1",
              },
              {
                id: "shot2",
                title: "归途",
                code: "S02",
                description: "踏上归途",
                production_status: "draft",
                selected_generation_id: null,
              },
            ],
      );
    if (collection === "generations")
      return respond([
        { id: "gen1", output_type: "image", output_blob_hash: null },
      ]);
    if (collection === "jobs")
      return respond(
        empty
          ? []
          : [
              {
                id: "job",
                request_type: "image",
                status: "failed",
                created_at: "2026-09-14T10:00:00Z",
                input_snapshot: { operation: "character" },
              },
            ],
      );
    return respond([]);
  });
  try {
    await run({
      context,
      page,
      requests,
      recover: () => {
        broken = "";
      },
    });
    assert.deepEqual(errors, [], name);
    assert.deepEqual(writes, [], name);
    cases.push({ name, passed: true });
    console.log(`PASS ${name}`);
  } finally {
    await context.close();
  }
}
const workbench = (page, id = a) =>
  page.goto(`${base}/projects/${id}/workbench`);
try {
  await scenario(
    "loaded counts, scoped gallery, navigation keyboard and focus",
    async ({ page }) => {
      await workbench(page);
      await page.getByRole("link", { name: "继续创作", exact: true }).waitFor();
      assert.equal(
        await page.getByRole("progressbar").getAttribute("aria-valuenow"),
        "50",
      );
      await page
        .getByText("1 个镜头已通过审阅或进入成片。选定画面不代表已完成审阅。", {
          exact: true,
        })
        .waitFor();
      await page.getByLabel("章节范围").selectOption(`${a}-ch1`);
      await page.getByText("这一章还没有关联内容", { exact: true }).waitFor();
      assert.equal(
        await page.getByRole("progressbar").getAttribute("aria-valuenow"),
        "50",
        "Project metrics must not shrink to chapter metrics",
      );
      await page.reload();
      await page.getByText("这一章还没有关联内容", { exact: true }).waitFor();
      assert.equal(await page.getByLabel("章节范围").inputValue(), `${a}-ch1`);
      await page.getByRole("button", { name: "查找功能", exact: true }).click();
      const dialog = page.getByRole("dialog");
      assert.equal(await dialog.locator("[data-destination]").count(), 20);
      await dialog.getByText("视频素材", { exact: true }).waitFor();
      await dialog.getByRole("textbox").fill("none-such");
      await dialog.getByText("没有匹配的功能，换个关键词试试。").waitFor();
      await dialog.getByRole("textbox").fill("回收站");
      await page.keyboard.press("ArrowDown");
      assert.ok(
        await page.evaluate(() =>
          document.activeElement.hasAttribute("data-destination"),
        ),
      );
      await page.keyboard.press("Escape");
      assert.equal(await page.getByRole("dialog").count(), 0);
      assert.ok(
        await page
          .getByRole("button", { name: "查找功能", exact: true })
          .evaluate((el) => el === document.activeElement),
      );
    },
  );
  await scenario(
    "failed collection is not shown as empty and retry restores progress",
    async ({ page, recover }) => {
      await workbench(page);
      await page.getByText("项目进度暂时无法获取", { exact: true }).waitFor();
      assert.equal(
        await page.getByRole("link", { name: "继续创作", exact: true }).count(),
        0,
      );
      await page
        .getByRole("button", { name: "角色与资产", exact: true })
        .click();
      await page.getByRole("alert").first().waitFor();
      recover();
      await page
        .getByRole("button", { name: "重新加载进度", exact: true })
        .click();
      await page.getByRole("link", { name: "继续创作", exact: true }).waitFor();
      await page
        .getByRole("heading", { name: "林间旅人", exact: true })
        .waitFor();
    },
    { broken: "assets" },
  );
  await scenario(
    "pending data cannot suggest a false starting stage",
    async ({ page }) => {
      await workbench(page);
      await page.getByText("正在整理项目进度…", { exact: true }).waitFor();
      assert.equal(
        await page.getByRole("link", { name: "继续创作", exact: true }).count(),
        0,
      );
      await page.getByRole("link", { name: "继续创作", exact: true }).waitFor();
      await page
        .getByRole("heading", { name: "下一步 · 分镜与画面", exact: true })
        .waitFor();
    },
    { delayShots: true },
  );
  await scenario(
    "empty project has actionable start without false completed stage",
    async ({ page }) => {
      await workbench(page);
      await page
        .getByRole("heading", { name: "下一步 · 故事章节", exact: true })
        .waitFor();
      assert.equal(
        await page
          .getByRole("link", { name: "继续创作", exact: true })
          .getAttribute("href"),
        `/projects/${a}/narrative`,
      );
      assert.equal(
        await page.getByRole("progressbar").getAttribute("aria-valuenow"),
        "0",
      );
      await page.getByText("生成和导出任务会显示在这里。").waitFor();
      await page.screenshot({ path: `${output}/empty-project.png` });
    },
    { empty: true },
  );
  await scenario(
    "deleted chapter is not sent to backend; projects remain isolated",
    async ({ page, requests }) => {
      await workbench(page);
      await page.getByLabel("章节范围").selectOption(`${a}-ch1`);
      await page.getByText("这一章还没有关联内容").waitFor();
      assert.ok(!requests.some((r) => r.includes("deleted-chapter")));
      await page.getByRole("link", { name: "所有项目", exact: true }).click();
      await page.getByRole("link").filter({ hasText: "SEA" }).click();
      await page
        .getByRole("heading", { name: "远海来信", exact: true })
        .waitFor();
      await page.getByRole("link", { name: "继续创作", exact: true }).waitFor();
      assert.ok(
        !requests.some(
          (r) => r.startsWith(`/projects/${b}/`) && r.includes(`${a}-ch1`),
        ),
      );
      await page
        .getByRole("button", { name: "角色与资产", exact: true })
        .click();
      await page
        .getByRole("heading", { name: "海边旅人", exact: true })
        .waitFor();
      assert.equal(
        await page.getByText("林间旅人", { exact: true }).count(),
        0,
      );
    },
    { stale: true },
  );
  await scenario(
    "projects search, sort, error recovery and create form validation",
    async ({ page, recover }) => {
      await page.goto(`${base}/projects`);
      await page.getByRole("alert").waitFor();
      recover();
      await page.getByRole("button", { name: "重试", exact: true }).click();
      await page.getByLabel("搜索项目").fill("SEA");
      assert.equal(await page.locator('a[href$="/workbench"]').count(), 1);
      await page.getByLabel("搜索项目").fill("__no_project__");
      await page.getByText("没有匹配的项目").waitFor();
      await page.getByRole("button", { name: "清除搜索" }).click();
      await page.getByLabel("项目排序").selectOption("name");
      assert.equal(await page.locator('a[href$="/workbench"]').count(), 2);
      await page.getByRole("button", { name: /新建项目/ }).click();
      await page.locator("form input").first().fill("   ");
      await page.locator("form input").last().fill("   ");
      assert.equal(
        await page.locator('form button[type="submit"]').isDisabled(),
        true,
      );
      await page.getByRole("button", { name: "关闭新建项目" }).click();
    },
    { broken: "projects" },
  );
  await scenario(
    "responsive navigation, focus trap and English labels",
    async ({ page }) => {
      await workbench(page);
      await page.getByRole("link", { name: "继续创作", exact: true }).waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      await page.getByRole("button", { name: "打开导航", exact: true }).click();
      const nav = page.getByRole("dialog", { name: "项目导航" });
      await nav.waitFor();
      const first = nav.locator("button").first();
      await first.focus();
      await page.keyboard.press("Shift+Tab");
      assert.ok(
        await nav.evaluate((el) => el.contains(document.activeElement)),
      );
      await page.keyboard.press("Escape");
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.getByTitle("语言", { exact: true }).click();
      await page
        .getByRole("heading", { name: "Production workflow" })
        .waitFor();
      await page
        .getByRole("button", { name: "Find a tool", exact: true })
        .click();
      await page.getByRole("dialog").getByRole("textbox").fill("trash");
      await page.getByRole("link", { name: "Trash", exact: true }).waitFor();
    },
  );
  // Mount the actual hook without routing. This catches key changes on a mounted
  // component, unlike a full page reload, which would hide the original bug.
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`${base}/tests/fixtures/persistent-state.html`);
  await page.getByText("first", { exact: true }).waitFor();
  await page.getByRole("button", { name: "switch" }).click();
  await page.getByText("second", { exact: true }).waitFor();
  await page.getByRole("button", { name: "edit" }).click();
  await page.getByText("second!", { exact: true }).waitFor();
  await page.getByRole("button", { name: "switch" }).click();
  await page.getByText("first", { exact: true }).waitFor();
  assert.deepEqual(
    await page.evaluate(() => [
      localStorage.getItem("scope-a"),
      localStorage.getItem("scope-b"),
    ]),
    ['"first"', '"second!"'],
  );
  await context.close();
  cases.push({
    name: "persistent state switches keys without overwriting another scope",
    passed: true,
  });
  await writeFile(
    `${output}/regression-report.json`,
    JSON.stringify({ passed: true, cases }, null, 2),
  );
  console.log(JSON.stringify({ passed: true, cases }));
} finally {
  await browser.close();
}
