import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR,
  fixture = JSON.parse(await readFile(process.env.TIMELINE_FIXTURE, "utf8"));
await mkdir(out, { recursive: true });
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1600, height: 1200 },
  }),
  page = await context.newPage(),
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
    assert.ok(r.ok(), `${method} ${path} ${r.status()}`);
    return r.json();
  };
  const root = `/projects/${fixture.project}`;
  assert.equal((await call("get", root)).name, "剪辑验收 · 字幕与声音");
  const original = await call(
    "get",
    `${root}/timelines/${fixture.timeline}/document`,
  );
  const timeline = await call("post", root + "/timelines", {
      name: "拖拽与实时预览验收 " + Date.now(),
    }),
    path = `${root}/timelines/${timeline.id}`;
  const input = {
    revision: 0,
    items: original.items.map((c) => ({ ...c, transition: null })),
    audio: [
      {
        ...original.audio[0],
        start_ms: 0,
        duration_ms: 2500,
        fade_in_ms: 200,
        fade_out_ms: 200,
      },
    ],
    subtitles: [{ start_ms: 500, end_ms: 1200, text: "海边来信" }],
    visuals: [
      {
        ...original.visuals[2],
        id: crypto.randomUUID(),
        track: 1,
        start_ms: 1000,
        duration_ms: 1000,
        name: "视频叠加",
      },
      {
        ...original.visuals[0],
        id: crypto.randomUUID(),
        track: 1,
        start_ms: 5000,
        duration_ms: 1000,
        name: "图片叠加",
      },
    ],
  };
  await call("put", path + "/document", input);
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
    const Native = window.AudioContext;
    window.__qaGains = [];
    window.AudioContext = class extends Native {
      createGain() {
        const node = super.createGain();
        window.__qaGains.push(node);
        return node;
      }
    };
  }, token);
  await page.goto(`${base}${root}/cuts?timeline=${timeline.id}`);
  const overview = page.getByRole("region", {
    name: "时间线概览",
    exact: true,
  });
  await overview.waitFor();
  const drag = async (locator, dx, dy = 0, cancel = false) => {
    await locator.scrollIntoViewIfNeeded();
    const b = await locator.boundingBox();
    assert.ok(b);
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 + dx, b.y + b.height / 2 + dy, {
      steps: 12,
    });
    if (cancel) await page.keyboard.press("Escape");
    await page.mouse.up();
  };
  const lane = overview.locator(".timeline-overview-lane").first();
  const width = (await lane.boundingBox()).width;
  await drag(
    overview.getByRole("button", { name: "选择片段 1", exact: true }),
    width * 0.72,
  );
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  await page.waitForFunction(() =>
    document.querySelector(".timeline-edit-bar").textContent.includes("已保存"),
  );
  assert.equal(
    (await call("get", path + "/document")).items[2].shot_id,
    input.items[0].shot_id,
  );
  await page.getByRole("button", { name: "撤销剪辑", exact: true }).click();
  await drag(
    overview.getByRole("button", { name: "裁切终点 items 1", exact: true }),
    -width / 12,
  );
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  await page.waitForFunction(() =>
    document.querySelector(".timeline-edit-bar").textContent.includes("已保存"),
  );
  assert.equal(
    (await call("get", path + "/document")).items[0].duration_ms,
    3000,
  );
  await page.getByRole("button", { name: "撤销剪辑", exact: true }).click();
  await overview
    .getByRole("button", { name: "选择片段 1", exact: true })
    .click();
  assert.equal(
    await page.getByLabel("时长 1", { exact: true }).inputValue(),
    "4",
  );
  await page.getByRole("button", { name: "重做剪辑", exact: true }).click();
  assert.equal(
    await page.getByLabel("时长 1", { exact: true }).inputValue(),
    "3",
  );
  const unit = (await lane.boundingBox()).width / 11;
  const visualBlock = overview.getByRole("button", {
    name: "叠加画面 视频叠加",
    exact: true,
  });
  const currentTrack = overview.locator('[data-visual-track="1"]'),
    targetTrack = overview.locator('[data-visual-track="2"]');
  const dy =
    (await targetTrack.boundingBox()).y - (await currentTrack.boundingBox()).y;
  await drag(visualBlock, 0, dy);
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  await page.waitForFunction(() =>
    document.querySelector(".timeline-edit-bar").textContent.includes("已保存"),
  );
  assert.equal((await call("get", path + "/document")).visuals[0].track, 2);
  await page.getByRole("button", { name: "撤销剪辑", exact: true }).click();

  await drag(
    overview.getByRole("button", { name: "声音片段 1", exact: true }),
    unit,
  );
  await drag(
    overview.getByRole("button", { name: "叠加画面 视频叠加", exact: true }),
    unit,
  );
  await drag(
    overview.getByRole("button", { name: "叠加画面 视频叠加", exact: true }),
    unit * 3,
  );
  await overview.getByRole("alert").filter({ hasText: "重叠" }).waitFor();
  await overview
    .getByRole("button", { name: "裁切起点 subtitles 1", exact: true })
    .focus();
  await page.keyboard.press("ArrowRight");
  await drag(
    overview.getByRole("button", { name: "声音片段 1", exact: true }),
    unit,
    0,
    true,
  );
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  await page.waitForFunction(() =>
    document.querySelector(".timeline-edit-bar").textContent.includes("已保存"),
  );
  let stored = await call("get", path + "/document");
  assert.equal(stored.audio[0].start_ms, 1000);
  assert.equal(stored.visuals[0].start_ms, 2000);
  assert.equal(stored.subtitles[0].start_ms, 600);
  await page.reload();
  await overview.waitFor();
  await overview
    .getByRole("button", { name: "选择片段 1", exact: true })
    .click();
  await page.getByLabel("转场 1", { exact: true }).selectOption("dissolve");
  await page.getByRole("button", { name: "合成布局", exact: true }).click();
  const scrub = page.getByLabel("合成预览位置", { exact: true });
  await scrub.fill("2750");
  await page.waitForFunction(
    () => document.querySelectorAll("[data-preview-base]").length === 2,
  );
  assert.ok(
    Math.abs(
      (await page
        .locator('[data-preview-base="1"]')
        .evaluate((el) => Number(el.style.opacity))) - 0.5,
    ) < 0.01,
  );
  await page.locator("[data-layer-id] video").waitFor();
  await page.waitForFunction(() => {
    const video = document.querySelector("[data-layer-id] video");
    return video?.readyState > 1 && Math.abs(video.currentTime - 1.75) < 0.12;
  });
  await page.screenshot({
    path: `${out}/transition-dark.png`,
    animations: "disabled",
  });
  await scrub.fill("800");
  await page.getByText("海边来信", { exact: true }).first().waitFor();
  await page.getByRole("button", { name: "播放时间线", exact: true }).click();
  await page.waitForFunction(
    () =>
      Number(document.querySelector('input[aria-label="合成预览位置"]').value) >
      1800,
  );
  assert.ok(
    await page
      .locator(".timeline-composition audio")
      .evaluate((el) => !el.paused && el.currentTime > 0),
  );
  assert.ok(
    await page.evaluate(() =>
      window.__qaGains.some((g) => g.gain.value > 0 && g.gain.value < 1),
    ),
  );
  await page.getByRole("button", { name: "暂停时间线", exact: true }).click();
  const paused = Number(await scrub.inputValue());
  await page.waitForTimeout(350);
  assert.equal(Number(await scrub.inputValue()), paused);
  assert.ok(
    await page
      .locator(".timeline-composition audio")
      .evaluate((el) => el.paused),
  );
  await scrub.fill("2400");
  await page.getByRole("button", { name: "播放时间线", exact: true }).click();
  await page.waitForFunction(
    () =>
      Number(document.querySelector('input[aria-label="合成预览位置"]').value) >
      5700,
  );
  await page.getByRole("button", { name: "暂停时间线", exact: true }).click();
  assert.equal(
    await page.locator('.timeline-composition [role="alert"]').count(),
    0,
  );
  await scrub.fill("10450");
  await page.getByRole("button", { name: "播放时间线", exact: true }).click();
  await page.getByRole("button", { name: "播放时间线", exact: true }).waitFor();
  assert.ok(Number(await scrub.inputValue()) >= 10499);
  await page.getByRole("button", { name: "回到开头", exact: true }).click();
  assert.equal(await scrub.inputValue(), "0");
  await page.evaluate(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  });
  await page.screenshot({
    path: `${out}/timeline-light.png`,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await overview.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `${out}/timeline-mobile.png`,
    animations: "disabled",
  });
  assert.ok(
    await page
      .locator("#main-content")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  );
  await page.setViewportSize({ width: 1600, height: 1200 });
  await page
    .getByRole("button", { name: "保存并导出 MP4", exact: true })
    .click();
  let film;
  for (let i = 0; i < 90; i++) {
    film = (
      await call(
        "get",
        root + `/generations?target_type=timeline&target_id=${timeline.id}`,
      )
    ).find((g) => g.output_type === "video" && g.output_blob_hash);
    if (film) break;
    await page.waitForTimeout(1000);
  }
  assert.ok(film, "MP4 export must complete");
  const download = await context.request.get(
    `${base}/api/v1${root}/blobs/${film.output_blob_hash}`,
    { headers },
  );
  assert.ok(download.ok());
  await writeFile(`${out}/drag-preview-film.mp4`, await download.body());
  stored = await call("get", path + "/document");
  assert.equal(stored.items[0].duration_ms, 3000);
  assert.equal(stored.items[0].transition.type, "dissolve");
  assert.deepEqual(
    await call("get", `${root}/timelines/${fixture.timeline}/document`),
    original,
  );
  assert.deepEqual(errors, []);
  await writeFile(
    `${out}/report.json`,
    JSON.stringify(
      {
        passed: true,
        project: fixture.project,
        timeline: timeline.id,
        generation: film.id,
        checks: [
          "Pointer reorder and trim with one-step undo/redo",
          "Audio/visual/subtitle edits, overlap rejection and Escape cancellation",
          "Real save/reload and original fixture untouched",
          "Transition midpoint, video source seek, captions",
          "Playback, audio gain, pause/end/restart",
          "Dark/light/mobile and actual MP4 export",
        ],
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Timeline interaction and playback live checks passed");
} catch (e) {
  console.log(
    await page.evaluate(() => ({
      time: document.querySelector('input[aria-label="合成预览位置"]')?.value,
      buttons: [
        ...document.querySelectorAll(".timeline-live-controls button"),
      ].map((b) => b.getAttribute("aria-label")),
      alerts: [...document.querySelectorAll('[role="alert"]')].map(
        (e) => e.textContent,
      ),
      media: [
        ...document.querySelectorAll(
          ".timeline-composition video,.timeline-composition audio",
        ),
      ].map((v) => ({
        type: v.tagName,
        ready: v.readyState,
        paused: v.paused,
        time: v.currentTime,
      })),
      visible: document.visibilityState,
    })),
  );
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  throw e;
} finally {
  await browser.close();
}
