import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
const fixture = JSON.parse(
  await readFile(process.env.TIMELINE_FIXTURE, "utf8"),
);
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1512, height: 1080 },
});
const page = await context.newPage(),
  errors = [],
  report = { checks: [] };
page.on("pageerror", (e) => errors.push(e.message));
page.on("response", async (r) => {
  if (r.status() >= 400 && r.url().includes("/api/v1"))
    console.log("API failure", r.url(), r.status(), await r.text());
});
page.on("dialog", (d) => d.accept());
try {
  const login = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(login.ok());
  const token = (await login.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      data,
      headers,
    });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  const root = `/projects/${fixture.project}`;
  assert.equal((await call("get", root)).name, "剪辑验收 · 字幕与声音");
  const original = await call(
    "get",
    `${root}/timelines/${fixture.timeline}/document`,
  );
  const timeline = await call("post", `${root}/timelines`, {
    name: `多画面轨验收 ${Date.now()}`,
  });
  const path = `${root}/timelines/${timeline.id}`;
  await call("put", `${path}/document`, {
    ...original,
    revision: 0,
    visuals: [],
  });
  report.project = fixture.project;
  report.timeline = timeline.id;
  const shotId = original.items[1].shot_id;
  const sources = await call(
    "get",
    `${root}/generations?target_type=shot&target_id=${shotId}`,
  );
  const source = sources.find(
    (g) => g.output_type === "image" && g.output_blob_hash,
  );
  assert.ok(source);
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(`${base}${root}/cuts?timeline=${timeline.id}`);
  await page.getByRole("tab", { name: "叠加画面", exact: true }).click();
  await page.getByLabel("叠加素材镜头", { exact: true }).selectOption(shotId);
  await page
    .getByLabel("叠加素材版本", { exact: true })
    .selectOption(source.id);
  await page.getByRole("button", { name: "添加叠加画面", exact: true }).click();
  await page.getByLabel("画面名称", { exact: true }).fill("星空画中画");
  await page.getByLabel("出现时间（秒）", { exact: true }).fill("1");
  await page.getByLabel("显示时长（秒）", { exact: true }).fill("2");
  await page.getByLabel("不透明度（%）", { exact: true }).fill("65");
  await page.getByRole("button", { name: "撤销剪辑", exact: true }).click();
  assert.equal(
    await page.getByLabel("不透明度（%）", { exact: true }).inputValue(),
    "100",
  );
  await page.getByRole("button", { name: "重做剪辑", exact: true }).click();
  assert.equal(
    await page.getByLabel("不透明度（%）", { exact: true }).inputValue(),
    "65",
  );
  const scrub = async (time) => {
    await page.getByLabel("合成预览位置", { exact: true }).fill(String(time));
  };
  await scrub(1500);
  await page.locator("[data-layer-id] img").waitFor();
  assert.equal(await page.locator("[data-layer-id]").count(), 1);
  await page.getByLabel("隐藏画面", { exact: true }).check();
  assert.equal(await page.locator("[data-layer-id]").count(), 0);
  await page.getByLabel("隐藏画面", { exact: true }).uncheck();
  await scrub(3500);
  assert.equal(await page.locator("[data-layer-id]").count(), 0);
  await scrub(1500);
  report.checks.push(
    "Image layer editing, undo/redo, time scrub and hide/unhide match visibility",
  );
  // Add a second layer; overlapping time on the same track must block save.
  await page.getByRole("button", { name: "添加叠加画面", exact: true }).click();
  await page.getByLabel("画面名称", { exact: true }).fill("左侧分屏");
  await page.getByLabel("出现时间（秒）", { exact: true }).fill("1");
  await page.getByLabel("叠加轨道", { exact: true }).selectOption("1");
  assert.equal(
    await page
      .getByRole("button", { name: "保存时间线", exact: true })
      .isDisabled(),
    true,
  );
  await page.getByLabel("叠加轨道", { exact: true }).selectOption("2");
  await page.getByLabel("显示时长（秒）", { exact: true }).fill("2");
  await page.getByRole("button", { name: "左半屏", exact: true }).click();
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  await page.getByText("已保存", { exact: true }).waitFor();
  let doc = await call("get", `${path}/document`);
  assert.equal(doc.visuals.length, 2);
  assert.equal(doc.visuals[0].opacity, 0.65);
  assert.equal(doc.visuals[1].fit, "cover");
  const savedRevision = doc.revision;
  await page.reload();
  await page.getByRole("tab", { name: "叠加画面", exact: true }).click();
  assert.equal(await page.locator(".timeline-visual-list button").count(), 2);
  await page
    .locator(".timeline-visual-list button")
    .filter({ hasText: "星空画中画" })
    .click();
  assert.equal(
    await page.getByLabel("不透明度（%）", { exact: true }).inputValue(),
    "65",
  );
  await scrub(1500);
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".timeline-composition-frame img")].every(
      (i) => i.complete && i.naturalWidth > 0,
    ),
  );
  await page.locator("main").evaluate((e) => e.scrollTo(0, 0));
  await page.screenshot({ path: `${out}/visuals-desktop.png`, fullPage: true });
  await page.evaluate(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  });
  await page.screenshot({ path: `${out}/visuals-light.png`, fullPage: true });
  await page.evaluate(() => {
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.locator("main").evaluate((e) => e.scrollWidth <= e.clientWidth),
  );
  await page.locator("main").evaluate((e) => e.scrollTo(0, 0));
  await page.screenshot({ path: `${out}/visuals-mobile.png`, fullPage: true });
  await page.locator(".timeline-visual-editor").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `${out}/visuals-mobile-editor.png`,
    fullPage: true,
  });
  await page.setViewportSize({ width: 1512, height: 1080 });
  report.checks.push(
    "Same-track overlap blocked; save/reload retains layers; desktop and mobile layout checked",
  );
  await page.getByRole("button", { name: "移除画面", exact: true }).click();
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  await page.getByText("已保存", { exact: true }).waitFor();
  await page.getByRole("tab", { name: "保存历史", exact: true }).click();
  await page.getByRole("button", { name: /保存历史/ }).click();
  const row = page
    .locator("div.rounded-lg")
    .filter({
      has: page.getByText(`v${savedRevision} · 保存剪辑`, { exact: true }),
    });
  await row.getByRole("button", { name: "恢复", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelectorAll(".timeline-visual-list button").length === 2,
  );
  doc = await call("get", `${path}/document`);
  assert.equal(doc.visuals.length, 2);
  assert.deepEqual(doc.audio, original.audio);
  assert.deepEqual(doc.subtitles, original.subtitles);
  // Upload a real existing QA film as a shot version, then use it as a video overlay.
  const sourceFilm = await call(
    "get",
    `${root}/generations/${fixture.generation}`,
  );
  const sourceBytes = await context.request.get(
    `${base}/api/v1${root}/blobs/${sourceFilm.output_blob_hash}`,
    { headers },
  );
  assert.ok(sourceBytes.ok());
  const uploaded = await context.request.post(
    `${base}/api/v1${root}/media/shot/${shotId}/upload`,
    {
      headers,
      multipart: {
        file: {
          name: "overlay-video.mp4",
          mimeType: "video/mp4",
          buffer: await sourceBytes.body(),
        },
      },
    },
  );
  assert.ok(uploaded.ok(), await uploaded.text());
  const video = await uploaded.json();
  await page.reload();
  await page.getByRole("tab", { name: "叠加画面", exact: true }).click();
  await page.getByLabel("叠加素材镜头", { exact: true }).selectOption(shotId);
  await page.getByLabel("叠加素材版本", { exact: true }).selectOption(video.id);
  await page.getByRole("button", { name: "添加叠加画面", exact: true }).click();
  await page.getByLabel("画面名称", { exact: true }).fill("视频叠加");
  await page.getByLabel("叠加轨道", { exact: true }).selectOption("3");
  await page.getByLabel("出现时间（秒）", { exact: true }).fill("1");
  await page.getByLabel("显示时长（秒）", { exact: true }).fill("2");
  await page.getByLabel("源视频起点（秒）", { exact: true }).fill("1");
  await page.getByRole("button", { name: "右半屏", exact: true }).click();
  await scrub(1500);
  await page.waitForFunction(() => {
    const v = document.querySelector("[data-layer-id] video");
    return v && v.readyState >= 2 && Math.abs(v.currentTime - 1.5) < 0.1;
  });
  // Server source-duration validation retains the unsaved draft on failure.
  await page.getByLabel("源视频起点（秒）", { exact: true }).fill("100");
  const failedSave = page.waitForResponse(
    (r) =>
      r.url().endsWith(`${path}/document`) && r.request().method() === "PUT",
  );
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  const rejected = await failedSave;
  console.log(
    "Source bounds response",
    rejected.status(),
    await rejected.text(),
  );
  assert.equal(rejected.status(), 422);
  await page.getByText(/叠加片段超出源视频时长/).waitFor();
  assert.equal(
    await page.getByLabel("源视频起点（秒）", { exact: true }).inputValue(),
    "100",
  );
  await page.getByLabel("源视频起点（秒）", { exact: true }).fill("1");
  const exportSave = page.waitForResponse(
    (r) =>
      r.url().endsWith(`${path}/document`) && r.request().method() === "PUT",
  );
  await page
    .getByRole("button", { name: "保存并导出 MP4", exact: true })
    .click();
  const exportSaved = await exportSave;
  assert.ok(exportSaved.ok(), await exportSaved.text());
  report.checks.push(
    "Video version import, source in-point seeking and out-of-source save rejection with draft retention",
  );
  let film;
  for (let i = 0; i < 90; i++) {
    const gens = await call(
      "get",
      `${root}/generations?target_type=timeline&target_id=${timeline.id}`,
    );
    film = gens.find((g) => g.output_type === "video" && g.output_blob_hash);
    if (film) break;
    await page.waitForTimeout(1000);
  }
  assert.ok(film, "Real overlay export must succeed");
  report.generation = film.id;
  const download = await context.request.get(
    `${base}/api/v1${root}/blobs/${film.output_blob_hash}`,
    { headers },
  );
  assert.ok(download.ok());
  await writeFile(`${out}/visuals-film.mp4`, await download.body());
  report.checks.push(
    "Removing and restoring history retains original music/captions and visual layers; real MP4 exported",
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(
    await call("get", `${root}/timelines/${fixture.timeline}/document`),
    original,
    "Original fixture untouched",
  );
  Object.assign(report, { passed: true, errors });
  await writeFile(
    `${out}/visuals-report.json`,
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report));
} catch (e) {
  await page.screenshot({ path: `${out}/visuals-failure.png`, fullPage: true });
  throw e;
} finally {
  await browser.close();
}
