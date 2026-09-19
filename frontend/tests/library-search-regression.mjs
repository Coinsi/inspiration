// All APIs are intercepted. Tests query scope and result UX without running a model.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.APP_URL || "http://127.0.0.1:5187",
  out = process.env.ARTIFACT_DIR || "./test-results/library-search";
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  }),
  page = await context.newPage();
const errors = [],
  queries = [],
  checks = [];
let broken = false;
page.on("pageerror", (e) => errors.push(e.message));
await context.addInitScript(() => {
  localStorage.setItem("inspiration_token", "fixture");
  localStorage.setItem("inspiration_lang", "zh");
});
const coverage = {
  versions: 4,
  indexed: 2,
  unindexed: 2,
  runs: {
    v: { id: "i", status: "ready", total: 2, completed: 2, error: null },
  },
};
await context.route("**/api/**", (route) => {
  const req = route.request(),
    path = new URL(req.url()).pathname.replace("/api/v1", "");
  const response = (data, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(data),
    });
  const project = { id: "p", name: "画面检索验收", code: "P", settings: {} };
  if (path === "/me")
    return response({ user: { id: "u", username: "Test" }, memberships: [] });
  if (path === "/projects") return response([project]);
  if (path === "/projects/p") return response(project);
  if (path.endsWith("/index-status")) return response(coverage);
  const video={id:"m",name:"场景素材.mp4",usage_count:0,versions:[{id:"v",media_id:"m",ordinal:1,status:"ready",filename:"场景素材.mp4",size_bytes:100000,duration_ms:12000,proxy_hash:null,poster_hash:null}]};
  if(path.endsWith("/library/catalog"))return response({total:1,items:[video]});
  if(path.endsWith("/library/items/m"))return response(video);
  if (path.endsWith("/search")) {
    const body = req.postDataJSON();
    queries.push(body);
    if (broken)
      return response({ error: { message: "本地检索暂不可用" } }, 502);
    return response({
      coverage,
      total: body.mode === "annotated" ? 30 : undefined,
      has_more: !body.offset,
      items: [
        {
          id: `s${body.offset}`,
          media_id: "m",
          version_id: "v",
          ordinal: 1,
          name: "场景素材.mp4",
          start_ms: 2200,
          end_ms: 4400,
          thumbnail_hash: "poster",
          score: body.mode === "semantic" ? 0.42 : null,
          description: "雨中道路",
          kind: body.mode === "semantic" ? "sampled_video" : "speech",
        },
      ],
    });
  }
  if (path.endsWith("/shots"))
    return response([
      { id: "shot", code: "SH01", title: "窗边", description: "" },
    ]);
  if (path.endsWith("/annotations")) return response({ items: [], total: 0 });
  if (path.includes("/blobs/"))
    return route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#667788"/></svg>',
    });
  if (req.method() !== "GET") throw new Error("Unexpected write " + path);
  return response([]);
});
try {
  await page.goto(`${base}/projects/p/library`);
  await page.getByRole("button", { name: "查找画面", exact: true }).click();
  await page
    .getByText("2 / 4 个视频版本已有索引 · 2 个尚未完成", { exact: true })
    .waitFor();
  await page.getByLabel("搜索画面描述").fill("雨中道路");
  await page.getByRole("button", { name: "查找片段", exact: true }).click();
  await page.getByText("相似度 0.420", { exact: false }).waitFor();
  await page.getByText("播放片段并引用", { exact: false }).click();
  assert.equal(await page.getByLabel("入点（秒）").inputValue(), "2.2");
  assert.equal(await page.getByLabel("出点（秒）").inputValue(), "4.4");
  await page.getByRole("dialog").getByTitle("关闭", { exact: true }).click();
  checks.push(
    "Coverage includes unindexed versions; search opens the exact version and range",
  );
  await page.getByRole("button", { name: "明确标注", exact: true }).click();
  assert.equal(
    await page.getByText("播放片段并引用", { exact: false }).count(),
    0,
  );
  await page.getByLabel("标注来源").selectOption("speech");
  await page.getByRole("button", { name: "查找片段", exact: true }).click();
  await page.getByText("第 1 页 · 30 处标注", { exact: true }).waitFor();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.getByText("第 2 页 · 30 处标注", { exact: true }).waitFor();
  assert.equal(queries.at(-1).offset, 24);
  assert.equal(queries.at(-1).kind, "speech");
  assert.equal(
    await page
      .getByRole("button", { name: "下一页", exact: true })
      .isDisabled(),
    true,
  );
  checks.push(
    "Mode changes clear stale results; annotation source and all-result pagination are explicit",
  );
  broken = true;
  await page.getByRole("button", { name: "相似画面", exact: true }).click();
  await page.getByRole("button", { name: "查找片段", exact: true }).click();
  await page.getByText("本地检索暂不可用", { exact: true }).waitFor();
  assert.equal(
    await page.getByText("当前检索范围没有匹配片段", { exact: true }).count(),
    0,
  );
  broken = false;
  await page.getByRole("button", { name: "重试搜索", exact: true }).click();
  await page.getByText("相似度 0.420", { exact: false }).waitFor();
  await page
    .getByLabel("检索参考图片")
    .setInputFiles({
      name: "big.png",
      mimeType: "image/png",
      buffer: Buffer.alloc(2 * 1024 * 1024 + 1),
    });
  await page
    .getByRole("alert")
    .getByText("请选择2 MB以内的PNG、JPEG或WebP图片", { exact: true })
    .waitFor();
  checks.push(
    "Service failure is not a negative result; oversized image is rejected before upload",
  );
  await page.screenshot({ path: `${out}/search-dark.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .locator("main")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  );
  await page.screenshot({ path: `${out}/search-mobile.png` });
  assert.deepEqual(errors, []);
  await writeFile(
    `${out}/report.json`,
    JSON.stringify({ passed: true, checks }, null, 2),
  );
  console.log(JSON.stringify({ passed: true, checks }));
} finally {
  await browser.close();
}
