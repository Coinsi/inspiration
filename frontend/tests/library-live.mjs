import { toggleAccountTheme } from "./helpers/account.mjs";
// Opt-in real uploads, confined to a newly created QA project in the isolated preview.
import assert from "node:assert/strict";
import { mkdir, writeFile, stat, open } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(
  process.env.TEST_ISOLATED_PREVIEW,
  "1",
  "Explicit isolated preview opt-in required",
);
const base = process.env.APP_URL;
assert.ok(base && process.env.TEST_SOURCE && process.env.ARTIFACT_DIR);
const out = process.env.ARTIFACT_DIR;
await mkdir(out, { recursive: true });
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
const report = {
    bytes: (await stat(process.env.TEST_SOURCE)).size,
    checks: [],
  },
  errors = [],
  failures = [],
  networkFailures = [];
page.on("requestfailed", (r) => {
  if (
    r.url().includes("/api/") &&
    !r.failure()?.errorText.includes("ERR_ABORTED")
  )
    networkFailures.push({
      path: new URL(r.url()).pathname,
      error: r.failure()?.errorText,
    });
});
page.on("pageerror", (e) => errors.push(e.message));
page.on("response", (r) => {
  if (r.url().includes("/api/") && r.status() >= 400)
    failures.push({ path: new URL(r.url()).pathname, status: r.status() });
});
const check = (name) => {
  report.checks.push(name);
  console.log(name);
};
try {
  const auth = await context.request.post(`${base}/api/v1/auth/login`, {
    data: {
      username: process.env.TEST_USERNAME || "demo",
      password: process.env.TEST_PASSWORD || "demo1234",
    },
  });
  assert.equal(auth.status(), 200);
  const { access_token } = await auth.json();
  const headers = { Authorization: `Bearer ${access_token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      headers,
      data,
    });
    assert.ok(r.ok(), `${path}: ${r.status()} ${await r.text()}`);
    return r.status() === 204 ? null : r.json();
  };
  await context.addInitScript((token) => {
    localStorage.setItem("inspiration_token", token);
    localStorage.setItem("inspiration_lang", "zh");
  }, access_token);
  const p = process.env.TEST_PROJECT_ID
    ? await call("get", `/projects/${process.env.TEST_PROJECT_ID}`)
    : await call("post", "/projects", {
        code: `QA-LIB-${Date.now()}`,
        name: "视频素材验收 · 分块与片段",
        description: "隔离预览环境的合成视频验收，不含真实项目素材。",
      });
  assert.equal(
    p.name,
    "视频素材验收 · 分块与片段",
    "Only this QA project may be changed",
  );
  report.project = p.id;
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  const project = `/projects/${p.id}`;
  if (!process.env.TEST_PROJECT_ID) {
    const script = await call("post", `${project}/scripts`, {
      title: "视频引用验收",
    });
    await call("post", `${project}/scripts/${script.id}/apply-scenes`, {
      scenes: [
        {
          title: "合成视频验收",
          shots: [
            {
              title: "测试片段引用",
              description: "用于验证引用关系和视频播放",
            },
          ],
        },
      ],
    });
  }
  const shots = await call("get", `${project}/shots`);
  assert.equal(shots.length, 1);
  const shot = shots[0];
  let chunks = 0;
  await page.route("**/library/versions/*/chunk?*", async (route) => {
    chunks++;
    if (chunks <= 2) await new Promise((r) => setTimeout(r, 700));
    await route.continue();
  });
  await page.goto(`${base}${project}/library`);
  let items, version;
  if (!process.env.TEST_PROJECT_ID) {
    await page.getByRole("button", { name: "导入视频", exact: true }).click();
    await page
      .getByLabel("选择视频文件")
      .setInputFiles(process.env.TEST_SOURCE);
    await page
      .getByRole("button", { name: "开始 / 继续上传", exact: true })
      .click();
    await page.waitForResponse(
      (r) => r.url().includes("/chunk?offset=0") && r.status() === 200,
    );
    await page.getByRole("button", { name: "暂停上传", exact: true }).click();
    await page
      .getByText("已暂停，已完成的分块会保留。", { exact: true })
      .waitFor();
    items = await call("get", `${project}/library`);
    version = items[0].versions[0];
    assert.ok(
      version.uploaded_bytes > 0 && version.uploaded_bytes < report.bytes,
    );
    report.paused_bytes = version.uploaded_bytes;
    report.media = items[0].id;
    check("Real chunk upload pauses after a committed chunk");
    await page.reload();
    await page.unroute("**/library/versions/*/chunk?*");
    await page.getByRole("button", { name: "继续上传", exact: true }).click();
    await page
      .getByLabel("选择视频文件")
      .setInputFiles(process.env.TEST_SOURCE);
    await page
      .getByRole("button", { name: "开始 / 继续上传", exact: true })
      .click();
    await page
      .getByText("上传完成，后台正在制作预览，可以关闭此窗口。", {
        exact: true,
      })
      .waitFor({ timeout: 600000 });
    await page.getByRole("dialog").getByTitle("关闭", { exact: true }).click();
    check("Reload + file reselection resumes with chunk verification");
  }
  if (process.env.TEST_PROJECT_ID && process.env.TEST_RESUME_UPLOAD === "1") {
    await page.unroute("**/library/versions/*/chunk?*");
    items = await call("get", `${project}/library`);
    report.resumed_from_bytes = items[0].versions[0].uploaded_bytes;
    await page.getByRole("button", { name: "继续上传", exact: true }).click();
    await page
      .getByLabel("选择视频文件")
      .setInputFiles(process.env.TEST_SOURCE);
    await page
      .getByRole("button", { name: "开始 / 继续上传", exact: true })
      .click();
    await page
      .getByText("上传完成，后台正在制作预览，可以关闭此窗口。", {
        exact: true,
      })
      .waitFor({ timeout: 600000 });
    await page.getByRole("dialog").getByTitle("关闭", { exact: true }).click();
    check("Resumed interrupted real upload from verified committed prefix");
  }
  await page
    .getByRole("button", { name: "预览与引用", exact: true })
    .waitFor({ timeout: 300000 });
  items = await call("get", `${project}/library`);
  version = items[0].versions[0];
  assert.equal(version.status, "ready");
  assert.equal(version.width, 3840);
  const sourceFile = await open(process.env.TEST_SOURCE, "r");
  const sourceRange = Buffer.alloc(8192);
  try {
    await sourceFile.read(sourceRange, 0, 8192, 65537);
  } finally {
    await sourceFile.close();
  }
  const range = await context.request.get(
    `${base}/api/v1${project}/blobs/${version.original_hash}`,
    {
      headers: { ...headers, Range: "bytes=65537-73728" },
    },
  );
  assert.equal(range.status(), 206);
  assert.deepEqual(await range.body(), sourceRange);
  report.source_range_verified = true;
  report.version = version.id;
  report.duration_ms = version.duration_ms;
  const uploadState = await call(
    "get",
    `${project}/library/versions/${version.id}`,
  );
  assert.equal(uploadState.uploaded_bytes, report.bytes);
  assert.equal(
    uploadState.chunk_hashes.length,
    Math.ceil(report.bytes / uploadState.chunk_size),
  );
  report.chunks = uploadState.chunk_hashes.length;
  await page.screenshot({ path: `${out}/library-dark.png` });
  await toggleAccountTheme(page);
  await page.screenshot({ path: `${out}/library-light.png` });
  await page.getByRole("button", { name: "预览与引用", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector("video")?.readyState >= 1,
  );
  assert.equal(await page.locator("video").evaluate((v) => v.videoWidth), 1280);
  await page.getByLabel("入点（秒）").fill("1.5");
  await page.getByLabel("出点（秒）").fill("3");
  await page.getByRole("button", { name: "播放所选片段", exact: true }).click();
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return v.paused && v.currentTime >= 3 && v.currentTime < 3.7;
  });
  check(
    "Actual 4K source becomes a playable 720p preview; segment playback stops at out point",
  );
  await page.getByLabel("目标镜头").selectOption(shot.id);
  await page
    .getByLabel("片段备注")
    .fill("参考 1.5–3 秒的移动画面（合成测试素材）");
  await page.getByRole("button", { name: "引用到镜头", exact: true }).click();
  await page.getByText("引用已保存。", { exact: false }).waitFor();
  await page.screenshot({ path: `${out}/segment-reference.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .getByRole("dialog")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  );
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({ path: `${out}/segment-mobile.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("link", { name: "打开镜头", exact: false }).click();
  await page
    .getByRole("heading", { name: "视频片段引用", exact: true })
    .waitFor();
  await page
    .getByText("参考 1.5–3 秒的移动画面（合成测试素材）", { exact: true })
    .waitFor();
  check(
    "Saved reference appears in the shot with its exact version and time range",
  );
  await page.goto(`${base}${project}/library`);
  await page.getByRole("button", { name: "引用与删除", exact: true }).click();
  await page
    .getByText("1 处引用 · 0 个上传/处理任务", { exact: true })
    .waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "移入回收站", exact: true })
      .isDisabled(),
    true,
  );
  await page.screenshot({ path: `${out}/deletion-impact.png` });
  await page.getByRole("button", { name: "解除这处引用", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await page
    .getByText("0 处引用 · 0 个上传/处理任务", { exact: true })
    .waitFor();
  await page.getByRole("button", { name: "移入回收站", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "回收站", exact: true }).click();
  await page.getByRole("button", { name: "恢复视频", exact: true }).click();
  await page.getByRole("button", { name: "全部视频", exact: true }).click();
  await page.getByRole("button", { name: "预览与引用", exact: true }).waitFor();
  check("Delete blocked while referenced; release → trash → restore works");
  // Leave one useful example reference in the QA project for manual review.
  await call("post", `${project}/library/usages`, {
    version_id: version.id,
    shot_id: shot.id,
    start_ms: 1500,
    end_ms: 3000,
    purpose: "visual_reference",
    note: "已验收的合成视频片段示例",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .locator("main")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  );
  await page.screenshot({ path: `${out}/library-mobile.png` });
  assert.deepEqual(errors, []);
  assert.deepEqual(failures, []);
  assert.deepEqual(networkFailures, []);
  report.passed = true;
} catch (e) {
  report.error = e.stack;
  await page.screenshot({ path: `${out}/failure.png` }).catch(() => {});
  throw e;
} finally {
  report.errors = errors;
  report.failures = failures;
  report.networkFailures = networkFailures;
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  await browser.close();
}
