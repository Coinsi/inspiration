import { toggleAccountTheme } from "./helpers/account.mjs";
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR,
  fixture = JSON.parse(await readFile(`${out}/report.json`, "utf8"));
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1512, height: 1080 },
  }),
  page = await context.newPage(),
  errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  const r = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(r.ok());
  const token = (await r.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  const root = `/projects/${fixture.project}`;
  assert.equal((await call("get", root)).name, "任务中心验收 · 创作记录");
  const timeline = (await call("get", `${root}/jobs/${fixture.retry_job}`))
    .target_id;
  const sound = await context.request.post(
    `${base}/api/v1${root}/timelines/${timeline}/audio`,
    {
      headers,
      multipart: {
        file: {
          name: "task-music.mp3",
          mimeType: "audio/mpeg",
          buffer: await readFile(`${out}/task-music.mp3`),
        },
      },
    },
  );
  assert.ok(sound.ok(), await sound.text());
  const generation = await call(
    "get",
    `${root}/generations/${(await sound.json()).id}`,
  );
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(`${base}${root}/tasks?job=${generation.job_id}`);
  await page.getByLabel("音轨试听", { exact: true }).waitFor();
  await page.waitForFunction(
    () => document.querySelector(".task-output audio")?.readyState >= 2,
  );
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载结果", exact: true }).click();
  assert.match((await download).suggestedFilename(), /\.mp3$/);
  const shots = await call("get", `${root}/shots`);
  const image = await context.request.post(
    `${base}/api/v1${root}/media/shot/${shots[0].id}/upload`,
    {
      headers,
      multipart: {
        file: {
          name: "size-limit.png",
          mimeType: "image/png",
          buffer: await readFile(`${out}/size-limit.png`),
        },
      },
    },
  );
  assert.ok(image.ok(), await image.text());
  const job = await call(
    "post",
    `${root}/generations/${(await image.json()).id}/refine`,
    { scale: 4 },
  );
  for (let n = 0; n < 30; n++) {
    const current = await call("get", `${root}/jobs/${job.id}`);
    if (current.status === "failed") break;
    await page.waitForTimeout(250);
  }
  assert.equal((await call("get", `${root}/jobs/${job.id}`)).status, "failed");
  await page.goto(`${base}${root}/tasks?job=${job.id}&status=failed`);
  await page
    .getByRole("button", { name: "按原始输入重试", exact: true })
    .waitFor();
  assert.ok(
    (
      await page
        .getByRole("complementary", { name: "任务详情", exact: true })
        .innerText()
    ).includes("输出尺寸过大"),
  );
  await page
    .getByRole("button", { name: "按原始输入重试", exact: true })
    .click();
  await page.waitForURL((u) => u.searchParams.get("job") !== job.id);
  const retry = new URL(page.url()).searchParams.get("job");
  await page.getByRole("button", { name: /查看原任务/ }).waitFor();
  await page
    .getByRole("button", { name: "按原始输入重试", exact: true })
    .waitFor();
  assert.equal((await call("get", `${root}/jobs/${retry}`)).status, "failed");
  const empty = await call("post", "/projects", {
    name: "任务中心验收 · 空项目隔离",
  });
  await page.goto(
    `${base}/projects/${empty.id}/tasks?job=${fixture.retry_job}`,
  );
  await page
    .getByRole("heading", { name: "从一次创作开始", exact: true })
    .waitFor();
  await page.getByText("任务不存在", { exact: false }).waitFor();
  assert.equal(await page.locator(".task-row").count(), 0);
  assert.equal(await page.locator(".task-output").count(), 0);
  await page.getByRole("button", { name: "关闭任务详情", exact: true }).click();
  await page.goto(`${base}${root}/tasks?job=${fixture.retry_job}`);
  await page.getByLabel("任务视频结果", { exact: true }).waitFor();
  await page.waitForFunction(
    () => document.querySelector(".task-output video")?.readyState >= 2,
  );
  await toggleAccountTheme(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".task-detail").scrollIntoViewIfNeeded();
  await page.locator(".task-output video").evaluate(async (video) => {
    video.muted = true;
    await video.play();
    await new Promise((resolve) => video.requestVideoFrameCallback(resolve));
    video.pause();
  });
  await page.waitForFunction(
    () => document.querySelector(".task-output video")?.readyState >= 2,
  );
  await page.screenshot({ path: `${out}/tasks-mobile.png`, fullPage: true });
  assert.deepEqual(errors, []);
  const report = {
    passed: true,
    checks: [
      "Actual MP3 upload, preview and correct download extension",
      "Actual oversized image processing fails; original-input retry and error remain traceable",
      "Project switch isolates list, detail, results and empty state",
    ],
    errors,
  };
  await writeFile(
    `${out}/boundaries-report.json`,
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report));
} catch (e) {
  await page.screenshot({
    path: `${out}/boundaries-failure.png`,
    fullPage: true,
  });
  throw e;
} finally {
  await browser.close();
}
