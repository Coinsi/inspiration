// Network fixtures only: cannot mutate real projects or run media jobs.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.APP_URL || "http://127.0.0.1:5187",
  out = process.env.ARTIFACT_DIR || "./test-results/library-regression";
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext(),
  page = await context.newPage();
const hash = (b) => createHash("sha256").update(b).digest("hex");
const original = Buffer.alloc(200000, 7),
  different = Buffer.from(original);
different[90000] = 8;
const v = {
  id: "v1",
  media_id: "m1",
  ordinal: 1,
  filename: "sample.mp4",
  size_bytes: 200000,
  uploaded_bytes: 131072,
  chunk_size: 131072,
  fingerprint: hash(
    Buffer.concat([
      Buffer.from("200000"),
      original.subarray(0, 65536),
      original.subarray(-65536),
    ]),
  ),
  chunk_hashes: [hash(original.subarray(0, 131072))],
  status: "uploading",
  original_hash: null,
  proxy_hash: null,
  poster_hash: null,
  error: null,
};
let brokenList = true,
  brokenChunk = "disconnect",
  chunks = 0;
const errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
await context.addInitScript(() => {
  localStorage.setItem("inspiration_token", "fixture");
  localStorage.setItem("inspiration_lang", "zh");
});
await context.route("**/api/**", async (route) => {
  const req = route.request(),
    p = new URL(req.url()).pathname.replace("/api/v1", "");
  const respond = (body, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  if (p === "/me")
    return respond({
      user: { id: "u", username: "fixture", display_name: "Test" },
      memberships: [],
    });
  const project = { id: "p", name: "视频边界验收", code: "P", settings: {} };
  if (p === "/projects") return respond([project]);
  if (p === "/projects/p") return respond(project);
  if (p.endsWith("/library/catalog"))
    return brokenList
      ? respond({ error: { message: "读取暂时失败" } }, 503)
      : respond({total:1,items:[{id:"m1",name:"sample.mp4",versions:[v],usage_count:0,deleted_at:null}]});
  if (p.endsWith("/versions/v1") && req.method() === "GET") return respond(v);
  if (p.endsWith("/chunk")) {
    chunks++;
    if (brokenChunk === "disconnect") return route.abort("connectionreset");
    if (brokenChunk)
      return respond({ error: { message: "网络中断，请继续上传" } }, 503);
    v.uploaded_bytes = 200000;
    v.chunk_hashes.push(hash(original.subarray(131072)));
    return respond(v);
  }
  if (p.endsWith("/complete")) {
    v.status = "queued";
    return respond(v);
  }
  if (p.endsWith("/deletion-impact"))
    return respond({
      references: [],
      active_versions: ["v1"],
      can_trash: false,
      physical_delete: false,
    });
  if (req.method() !== "GET") throw new Error(`Unexpected fixture write ${p}`);
  return respond([]);
});
try {
  await page.goto(`${base}/projects/p/library`);
  await page.getByText("视频素材加载失败", { exact: true }).waitFor();
  assert.equal(
    await page.getByText("把第一段视频放进来", { exact: true }).count(),
    0,
  );
  brokenList = false;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await page.getByRole("button", { name: "继续上传", exact: true }).click();
  await page.getByLabel("选择视频文件").setInputFiles({
    name: "sample.mp4",
    mimeType: "video/mp4",
    buffer: different,
  });
  await page
    .getByRole("button", { name: "开始 / 继续上传", exact: true })
    .click();
  await page
    .getByText("已上传内容与所选文件不同，已停止续传。", { exact: true })
    .waitFor();
  assert.equal(chunks, 0);
  checks.push(
    "File with identical size, first and last bytes but changed uploaded interior cannot resume",
  );
  await page.getByLabel("选择视频文件").setInputFiles({
    name: "sample.mp4",
    mimeType: "video/mp4",
    buffer: original,
  });
  await page
    .getByRole("button", { name: "开始 / 继续上传", exact: true })
    .click();
  await page
    .getByText("网络连接中断，请检查连接后点击继续上传。", { exact: true })
    .waitFor();
  await page
    .getByText("上传已暂停，已完成的分块会保留。", { exact: true })
    .waitFor();
  assert.equal(chunks, 1);
  brokenChunk = true;
  await page
    .getByRole("button", { name: "开始 / 继续上传", exact: true })
    .click();
  await page.getByText("网络中断，请继续上传", { exact: true }).waitFor();
  assert.equal(chunks, 2);
  assert.equal(
    await page.getByRole("progressbar").getAttribute("value"),
    "131072",
  );
  brokenChunk = false;
  await page
    .getByRole("button", { name: "开始 / 继续上传", exact: true })
    .click();
  await page
    .getByText("上传完成，后台正在制作预览，可以关闭此窗口。", { exact: true })
    .waitFor();
  assert.equal(chunks, 3);
  assert.equal(v.status, "queued");
  checks.push(
    "Network failure preserves server progress and retry completes the same version",
  );
  await page.getByRole("dialog").getByTitle("关闭", { exact: true }).click();
  await page.getByRole("button", { name: "引用与删除", exact: true }).click();
  await page
    .getByText("0 处引用 · 1 个上传/处理任务", { exact: true })
    .waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "移入回收站", exact: true })
      .isDisabled(),
    true,
  );
  checks.push(
    "List failure is distinct from empty library; active processing prevents deletion",
  );
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, checks }));
  await writeFile(
    `${out}/report.json`,
    JSON.stringify({ passed: true, checks }, null, 2),
  );
} finally {
  await browser.close();
}
