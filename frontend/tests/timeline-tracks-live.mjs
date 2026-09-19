// All mutations are confined to a newly created project in an opted-in isolated preview.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR,
  media = process.env.TEST_MEDIA_DIR;
assert.ok(base && out && media);
await mkdir(out, { recursive: true });
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  }),
  page = await context.newPage();
const errors = [],
  checks = [],
  report = {};
page.on("pageerror", (e) => errors.push(e.message));
const check = (name) => {
  checks.push(name);
  console.log(name);
};
try {
  const auth = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: "demo", password: "demo1234" },
  });
  assert.equal(auth.status(), 200);
  const token = (await auth.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      headers,
      data,
    });
    assert.ok(r.ok(), `${path} ${r.status()} ${await r.text()}`);
    return r.json();
  };
  await context.addInitScript((token) => {
    localStorage.setItem("inspiration_token", token);
    localStorage.setItem("inspiration_lang", "zh");
  }, token);
  const p = await call("post", "/projects", {
    code: `QA-TRACKS-${Date.now()}`,
    name: "剪辑验收 · 字幕与声音",
    description: "隔离预览的公开图像与合成音频验收。",
  });
  report.project = p.id;
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  const project = `/projects/${p.id}`,
    script = await call("post", `${project}/scripts`, { title: "字幕与声音" });
  await call("post", `${project}/scripts/${script.id}/apply-scenes`, {
    scenes: [
      {
        title: "公开样本",
        shots: [{ title: "海边" }, { title: "星空" }, { title: "咖啡" }],
      },
    ],
  });
  const shots = await call("get", `${project}/shots`),
    clips = [];
  for (const [i, name] of ["beach", "astronaut", "coffee"].entries()) {
    const r = await context.request.post(
      `${base}/api/v1${project}/media/shot/${shots[i].id}/upload`,
      {
        headers,
        multipart: {
          file: {
            name: `${name}.png`,
            mimeType: "image/png",
            buffer: await readFile(`${media}/${name}.png`),
          },
        },
      },
    );
    assert.ok(r.ok(), await r.text());
    clips.push({
      shot_id: shots[i].id,
      generation_id: (await r.json()).id,
      duration_ms: 4000,
    });
  }
  const timeline = await call("post", `${project}/timelines`, {
      name: "字幕与配乐工作线",
    }),
    path = `${project}/timelines/${timeline.id}`;
  report.timeline = timeline.id;
  await call("put", `${path}/items`, { items: clips });
  await page.goto(`${base}${project}/cuts`);
  await page.getByText("剪辑与成片", { exact: true }).waitFor();
  await page.getByRole("tab", { name: "声音与字幕", exact: true }).click();
  await page
    .getByLabel("上传音频文件", { exact: true })
    .setInputFiles(`${out}/music.wav`);
  await page.getByRole("button", { name: "添加音轨", exact: true }).click();
  const audio = page.getByRole("article", { name: "音轨 1", exact: true });
  await audio.waitFor();
  await audio.getByLabel("音轨时长 (s)", { exact: true }).fill("8");
  await audio.getByLabel("时间线位置 (s)", { exact: true }).fill("2");
  await audio.getByLabel("音量 (dB)", { exact: true }).fill("-6");
  await audio.getByLabel("淡入 (s)", { exact: true }).fill("1");
  await audio.getByLabel("淡出 (s)", { exact: true }).fill("1");
  await page.getByRole("tab", { name: /^字幕/ }).click();
  await page
    .getByLabel("导入时间线字幕", { exact: true })
    .setInputFiles(`${out}/captions.srt`);
  await page.getByLabel("字幕文字 2", { exact: true }).waitFor();
  await page
    .getByLabel("字幕文字 2", { exact: true })
    .fill("再去看看星空与咖啡");
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  await page
    .getByRole("button", { name: "保存时间线", exact: true })
    .waitFor({ state: "visible" });
  await page.waitForFunction(() =>
    [...document.querySelectorAll("button")].some(
      (b) => b.textContent === "保存时间线" && b.disabled,
    ),
  );
  let doc = await call("get", `${path}/document`);
  assert.equal(doc.audio[0].start_ms, 2000);
  assert.equal(doc.audio[0].gain_db, -6);
  assert.equal(doc.subtitles.length, 2);
  const savedRevision = doc.revision;
  await page.reload();
  await page.getByRole("tab", { name: "声音与字幕", exact: true }).click();
  await page.getByRole("article", { name: "音轨 1", exact: true }).waitFor();
  assert.equal(
    await page.getByLabel("音轨时长 (s)", { exact: true }).inputValue(),
    "8",
  );
  check(
    "Audio upload, placement, gain/fades and imported captions survive a real save and page reload",
  );
  await page.getByLabel("音量 (dB)", { exact: true }).fill("-12");
  await page.getByRole("button", { name: "撤销剪辑", exact: true }).click();
  assert.equal(
    await page.getByLabel("音量 (dB)", { exact: true }).inputValue(),
    "-6",
  );
  await page.getByRole("button", { name: "重做剪辑", exact: true }).click();
  assert.equal(
    await page.getByLabel("音量 (dB)", { exact: true }).inputValue(),
    "-12",
  );
  await page.getByRole("button", { name: "保存时间线", exact: true }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll("button")].some(
      (b) => b.textContent === "保存时间线" && b.disabled,
    ),
  );
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
      document.querySelector('input[aria-label="音量 (dB)"]')?.value === "-6",
  );
  doc = await call("get", `${path}/document`);
  assert.ok(doc.revision > savedRevision);
  assert.equal(doc.audio[0].gain_db, -6);
  check(
    "Undo/redo includes audio; restoring saved history creates a new revision and retains prior revisions",
  );
  await page.getByLabel("字幕导出方式", { exact: true }).selectOption("burn");
  await page
    .getByRole("button", { name: "保存并导出 MP4", exact: true })
    .click();
  let gens = [];
  for (let i = 0; i < 90; i++) {
    gens = await call(
      "get",
      `${project}/generations?target_type=timeline&target_id=${timeline.id}`,
    );
    if (gens.some((g) => g.output_type === "video" && g.output_blob_hash))
      break;
    await page.waitForTimeout(1000);
  }
  const film = gens.find(
    (g) => g.output_type === "video" && g.output_blob_hash,
  );
  assert.ok(film, "Real MP4 export must succeed");
  report.generation = film.id;
  const download = await context.request.get(
    `${base}/api/v1${project}/blobs/${film.output_blob_hash}`,
    { headers },
  );
  assert.ok(download.ok());
  await writeFile(`${out}/timeline-film.mp4`, await download.body());
  await page.reload();
  await page.getByRole("tab", { name: "声音与字幕", exact: true }).click();
  await page.getByRole("button", { name: "下载 MP4", exact: true }).waitFor();
  await page.getByRole("tab", { name: /^字幕/ }).click();
  assert.equal(
    await page.getByLabel("字幕文字 2", { exact: true }).inputValue(),
    "再去看看星空与咖啡",
  );
  check(
    "A real MP4 with the saved music and captions is generated and remains previewable after reload",
  );
  await page.screenshot({
    path: `${out}/timeline-desktop.png`,
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .locator("main")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  );
  await page.screenshot({ path: `${out}/timeline-mobile.png`, fullPage: true });
  assert.deepEqual(errors, []);
  Object.assign(report, { passed: true, checks, errors });
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (e) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  throw e;
} finally {
  await browser.close();
}
