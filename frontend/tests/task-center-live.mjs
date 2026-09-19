import { toggleAccountTheme } from "./helpers/account.mjs";
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
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
    viewport: { width: 1512, height: 1080 },
  }),
  page = await context.newPage(),
  errors = [],
  report = { checks: [] };
page.on("pageerror", (e) => errors.push(e.message));
try {
  const auth = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(auth.ok());
  const token = (await auth.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  const p = await call("post", "/projects", {
    name: "任务中心验收 · 创作记录",
  });
  report.project = p.id;
  const root = `/projects/${p.id}`,
    script = await call("post", `${root}/scripts`, { title: "任务素材" });
  await call("post", `${root}/scripts/${script.id}/apply-scenes`, {
    scenes: [
      { title: "海边", shots: [{ title: "海边开场" }, { title: "星空结尾" }] },
    ],
  });
  const shots = await call("get", `${root}/shots`),
    upload = async (shot, file, mime) => {
      const r = await context.request.post(
        `${base}/api/v1${root}/media/shot/${shot}/upload`,
        {
          headers,
          multipart: {
            file: {
              name: file.split("/").at(-1),
              mimeType: mime,
              buffer: await readFile(file),
            },
          },
        },
      );
      assert.ok(r.ok(), await r.text());
      return r.json();
    };
  const first = await upload(
    shots[0].id,
    `${process.env.TEST_MEDIA_DIR}/beach.png`,
    "image/png",
  );
  for (let i = 0; i < 25; i++)
    await upload(
      shots[0].id,
      `${process.env.TEST_MEDIA_DIR}/beach.png`,
      "image/png",
    );
  const last = await upload(
    shots[1].id,
    `${process.env.TEST_MEDIA_DIR}/astronaut.png`,
    "image/png",
  );
  const timeline = await call("post", `${root}/timelines`, {
    name: "任务中心成片",
  });
  await call("put", `${root}/timelines/${timeline.id}/items`, {
    items: Array.from({ length: 24 }, () => ({
      shot_id: shots[0].id,
      generation_id: first.id,
      duration_ms: 500,
    })),
  });
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  const tasks = `${base}${root}/tasks`;
  await page.goto(tasks);
  await page.waitForFunction(
    () => document.querySelectorAll(".task-row").length === 24,
  );
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelectorAll(".task-row").length === 3,
  );
  assert.equal(
    await page
      .getByRole("button", { name: "下一页", exact: true })
      .isDisabled(),
    true,
  );
  await page.getByLabel("搜索任务", { exact: true }).fill("星空结尾");
  await page.getByLabel("搜索任务", { exact: true }).press("Enter");
  await page.waitForFunction(
    () => document.querySelectorAll(".task-row").length === 1,
  );
  await page.locator(".task-row").click();
  await page.getByAltText("任务图片结果", { exact: true }).waitFor();
  assert.ok(page.url().includes(last.job_id));
  await page.reload();
  await page.getByAltText("任务图片结果", { exact: true }).waitFor();
  assert.equal(
    await page.getByLabel("搜索任务", { exact: true }).inputValue(),
    "星空结尾",
  );
  assert.ok(
    (
      await page
        .getByRole("link", { name: "回到创作位置", exact: true })
        .getAttribute("href")
    ).includes(shots[1].id),
  );
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  await page.getByLabel("任务类型", { exact: true }).selectOption("render");
  await page
    .getByRole("heading", { name: "没有符合条件的任务", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  report.checks.push(
    "Real 27-task pagination, whole-project counts, target-name search, empty filters, reload and source link",
  );
  // Real cancellation followed by an actual render retry; no fixture database mutations.
  const job = await call("post", `${root}/timelines/${timeline.id}/render`, {
    height: 720,
    aspect_ratio: "16:9",
  });
  report.original_job = job.id;
  await page.goto(`${tasks}?job=${job.id}`);
  await page.getByRole("button", { name: "取消任务", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await page
    .getByRole("button", { name: "按原始输入重试", exact: true })
    .waitFor();
  assert.equal(
    (await call("get", `${root}/jobs/${job.id}`)).status,
    "canceled",
  );
  await page
    .getByRole("button", { name: "按原始输入重试", exact: true })
    .click();
  await page.waitForURL((u) => u.searchParams.get("job") !== job.id);
  const retry = new URL(page.url()).searchParams.get("job");
  report.retry_job = retry;
  await page.getByRole("button", { name: /查看原任务/ }).waitFor();
  for (let n = 0; n < 90; n++) {
    const j = await call("get", `${root}/jobs/${retry}`);
    if (j.status === "succeeded") break;
    assert.notEqual(j.status, "failed", j.error);
    await page.waitForTimeout(1000);
  }
  await page.getByLabel("任务视频结果", { exact: true }).waitFor();
  await page.waitForFunction(
    () => document.querySelector(".task-output video")?.readyState >= 2,
  );
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载结果", exact: true }).click();
  await (await download).saveAs(`${out}/task-result.mp4`);
  await page.getByRole("button", { name: /查看原任务/ }).click();
  await page.getByRole("button", { name: /重试任务.*已完成/ }).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "按原始输入重试", exact: true })
      .count(),
    0,
  );
  await page.getByRole("button", { name: /重试任务.*已完成/ }).click();
  await page.getByLabel("任务视频结果", { exact: true }).waitFor();
  report.checks.push(
    "Real render cancellation, retry with immutable original input, parent/child links, completed video preview and download",
  );
  // Multi-output preview uses a real offline Mock generation, never a cloud model.
  const variants = await call("post", `${root}/shots/${shots[1].id}/generate`, {
    provider: "mock",
    count: 2,
    prompt_override: "离线任务界面验收",
    use_references: false,
  });
  await page.goto(`${tasks}?job=${variants.id}`);
  await page.getByAltText("任务图片结果", { exact: true }).waitFor();
  const src = await page
    .getByAltText("任务图片结果", { exact: true })
    .getAttribute("src");
  await page.getByRole("button", { name: "下一个结果", exact: true }).click();
  assert.notEqual(
    await page
      .getByAltText("任务图片结果", { exact: true })
      .getAttribute("src"),
    src,
  );
  await page.goBack();
  await page.getByLabel("任务视频结果", { exact: true }).waitFor();
  await page.locator("main").evaluate((e) => e.scrollTo(0, 0));
  await page.screenshot({ path: `${out}/tasks-dark.png`, fullPage: true });
  await toggleAccountTheme(page);
  await page.screenshot({ path: `${out}/tasks-light.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.locator("main").evaluate((e) => e.scrollWidth <= e.clientWidth),
  );
  await page.locator(".task-detail").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${out}/tasks-mobile.png`, fullPage: true });
  await page.getByRole("button", { name: "关闭任务详情", exact: true }).click();
  assert.equal(new URL(page.url()).searchParams.has("job"), false);
  report.checks.push(
    "Multiple real Mock outputs, browser-back restores selection, dark/light and 390px detail close",
  );
  // Explicit network failure and delayed responses exercise UI boundaries only.
  await page.setViewportSize({ width: 1512, height: 1080 });
  await page.route(`**/api/v1${root}/jobs/catalog?**`, (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: { message: "验收模拟：任务列表暂不可用" },
      }),
    }),
  );
  await page.reload();
  await page
    .getByText("验收模拟：任务列表暂不可用", { exact: false })
    .waitFor();
  assert.equal(
    await page
      .getByRole("heading", { name: "从一次创作开始", exact: true })
      .count(),
    0,
  );
  await page.unroute(`**/api/v1${root}/jobs/catalog?**`);
  await page.getByRole("button", { name: "重新读取", exact: true }).click();
  await page.locator(".task-row").first().waitFor();
  await page.route(
    `**/api/v1${root}/jobs/${first.job_id}/workspace`,
    async (route) => {
      await new Promise((r) => setTimeout(r, 900));
      await route.continue();
    },
  );
  await page.goto(`${tasks}?job=${first.job_id}`);
  await page.locator(`[data-task-id="${retry}"]`).click();
  await page.getByLabel("任务视频结果", { exact: true }).waitFor();
  await page.waitForTimeout(1000);
  assert.ok(page.url().includes(retry));
  assert.equal(
    await page.getByAltText("任务图片结果", { exact: true }).count(),
    0,
  );
  report.checks.push(
    "Controlled list failure is not an empty state; retry recovers; late detail response cannot replace another selected task",
  );
  assert.deepEqual(errors, []);
  Object.assign(report, { passed: true, errors });
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (e) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  throw e;
} finally {
  await browser.close();
}
