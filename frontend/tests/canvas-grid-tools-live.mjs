import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR,
  media = process.env.TEST_MEDIA_DIR;
assert.ok(base && out && media && process.env.CANVAS_SOURCE_FIXTURE);
await mkdir(out, { recursive: true });
const source = JSON.parse(
  await readFile(process.env.CANVAS_SOURCE_FIXTURE, "utf8"),
);
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1560, height: 1040 },
});
const page = await context.newPage(),
  errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
try {
  const auth = await context.request.post(base + "/api/v1/auth/login", {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(auth.ok());
  const token = (await auth.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const response = await context.request[method](base + "/api/v1" + path, {
      headers,
      data,
    });
    assert.ok(response.ok(), await response.text());
    return response.json();
  };
  const root = "/projects/" + source.project;
  assert.equal((await call("get", root)).name, "画布体验验收 · 媒体创作");
  let fixture;
  try {
    fixture = JSON.parse(await readFile(out + "/fixture.json", "utf8"));
  } catch {}
  if (!fixture) {
    const canvas = await call("post", root + "/canvases", {
      name: "创作工具验收 · 图片拆分与导入",
    });
    fixture = { project: source.project, canvas: canvas.id };
    await writeFile(out + "/fixture.json", JSON.stringify(fixture, null, 2));
  }
  const path = root + "/canvases/" + fixture.canvas;
  const existing = await call("get", path);
  assert.equal(existing.name, "创作工具验收 · 图片拆分与导入");
  await call("put", path, {
    name: existing.name,
    revision: existing.revision,
    document: {
      nodes: [
        {
          id: "source",
          position: { x: 120, y: 170 },
          width: 580,
          height: 390,
          data: {
            kind: "shot",
            label: "光影参考 · 原始画面",
            text: "来自本地参考视频，用于拆分与引用验收。",
            target_type: "shot",
            target_id: source.shots[0],
            generation_id: source.a.id,
            generate: {
              provider: "mock",
              request_type: "image",
              count: 1,
              params: {},
              provider_params: {},
              use_references: false,
            },
          },
        },
      ],
      edges: [],
      viewport: { x: 30, y: 30, zoom: 1 },
    },
  });
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  const url = base + root + "/canvas?canvas=" + fixture.canvas;
  await page.goto(url);
  const card = (id) => page.locator(`.react-flow__node[data-id="${id}"]`);
  await card("source").locator(".canvas-node-title").click();
  await page.getByRole("button", { name: "拆分图片", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector(".canvas-grid-stage img")?.naturalWidth > 0,
  );
  await page.getByRole("button", { name: "取消全选", exact: true }).click();
  assert.ok(
    await page
      .getByRole("button", { name: "拆分并放入画布 · 0 张", exact: true })
      .isDisabled(),
  );
  await page.getByRole("button", { name: "3 × 3", exact: true }).click();
  await page
    .getByRole("button", { name: "第 1 行第 2 列", exact: true })
    .click();
  await page
    .getByRole("button", { name: "第 3 行第 3 列", exact: true })
    .click();
  await page.screenshot({ path: out + "/grid-dark.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .getByRole("dialog")
      .evaluate((e) => e.scrollWidth <= e.clientWidth),
  );
  await page.screenshot({ path: out + "/grid-mobile.png" });
  await page.setViewportSize({ width: 1560, height: 1040 });
  await page.evaluate(() => {
    document.documentElement.classList.replace("dark", "light");
  });
  await page.waitForTimeout(350);
  await page.screenshot({ path: out + "/grid-light.png" });
  await page.evaluate(() => {
    document.documentElement.classList.replace("light", "dark");
  });
  let first = [],
    requests = [];
  // The first request really commits, but its successful response is lost. Retry must reuse it.
  await page.route("**/grid-split", async (route) => {
    requests.push(route.request().postDataJSON());
    const response = await route.fetch();
    assert.ok(response.ok(), await response.text());
    if (requests.length === 1) {
      first = await response.json();
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { message: "拆分响应暂时丢失，请重试" },
        }),
      });
    } else await route.fulfill({ response });
  });
  await page
    .getByRole("button", { name: "拆分并放入画布 · 7 张", exact: true })
    .click();
  await page.getByText("拆分响应暂时丢失，请重试", { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "拆分并放入画布 · 7 张", exact: true })
    .click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.equal(requests.length, 2);
  assert.equal(requests[0].request_key, requests[1].request_key);
  const save = async () => {
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await page.getByText(/已保存 · 版本/).waitFor();
    return call("get", path);
  };
  let doc = await save();
  assert.equal(doc.document.nodes.length, 8);
  assert.deepEqual(
    doc.document.nodes
      .filter((n) => n.id !== "source")
      .map((n) => n.data.generation_id)
      .sort(),
    first.map((g) => g.id).sort(),
  );
  const versions = await call(
    "get",
    root + `/generations?target_type=shot&target_id=${source.shots[0]}`,
  );
  assert.equal(versions.filter((g) => g.job_id === first[0].job_id).length, 7);
  assert.ok(
    versions
      .filter((g) => g.job_id === first[0].job_id)
      .every((g) => !g.is_selected),
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  assert.equal((await save()).document.nodes.length, 1);
  await page.getByRole("button", { name: "重做", exact: true }).click();
  doc = await save();
  assert.equal(doc.document.nodes.length, 8);
  await page.reload();
  await page.getByLabel("画布名称").waitFor();
  const crop = doc.document.nodes.find(
    (n) => n.data.generation_id === first[0].id,
  );
  await card(crop.id).locator(".canvas-node-title").click();
  await page.getByRole("button", { name: "来源原图", exact: true }).click();
  await page
    .getByRole("dialog", { name: "拆分来源原图", exact: true })
    .waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "参考创作", exact: true }).click();
  await page
    .getByLabel("节点提示词", { exact: true })
    .fill("以这一格为参考，延伸一个镜头。");
  doc = await save();
  assert.ok(
    doc.document.edges.some(
      (e) => e.source === crop.id && e.sourceHandle === "image",
    ),
  );
  assert.equal(
    doc.document.nodes.find((n) => n.id === crop.id).data.generation_id,
    first[0].id,
  );
  await page.screenshot({ path: out + "/split-reference-workspace.png" });
  async function picker() {
    await page.getByRole("button", { name: "项目素材", exact: true }).click();
    await page.getByRole("button", { name: "镜头作品", exact: true }).click();
    await page.getByRole("button", { name: /竹林 · 动作参考/ }).click();
  }
  await picker();
  await page
    .getByLabel("本地素材文件", { exact: true })
    .setInputFiles({
      name: "wrong.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("invalid"),
    });
  await page
    .getByText("请选择 PNG、JPG、WebP 图片或 MP4 视频", { exact: true })
    .waitFor();
  await page
    .getByLabel("本地素材文件", { exact: true })
    .setInputFiles(media + "/reference-b.jpg");
  await page.screenshot({ path: out + "/media-import.png" });
  await page.route("**/media/shot/*/upload", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "测试上传暂不可用" } }),
    }),
  );
  await page
    .getByRole("button", { name: "导入并放入画布", exact: true })
    .click();
  await page.getByText("测试上传暂不可用", { exact: true }).waitFor();
  await page.unroute("**/media/shot/*/upload");
  await page
    .getByRole("button", { name: "导入并放入画布", exact: true })
    .click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  doc = await save();
  assert.equal(doc.document.nodes.length, 10);
  await picker();
  // Exercise real File/DataTransfer handling in the visible import drop zone.
  const bytes = [...(await readFile(media + "/reference-video.mp4"))];
  await page.locator(".canvas-import").evaluate((element, bytes) => {
    const transfer = new DataTransfer();
    transfer.items.add(
      new File([new Uint8Array(bytes)], "reference-video.mp4", {
        type: "video/mp4",
      }),
    );
    element.dispatchEvent(
      new DragEvent("drop", {
        bubbles: true,
        cancelable: true,
        dataTransfer: transfer,
      }),
    );
  }, bytes);
  await page
    .getByRole("button", { name: "导入并放入画布", exact: true })
    .click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  doc = await save();
  assert.equal(doc.document.nodes.length, 11);
  await page.reload();
  await page.locator(".canvas-card video").waitFor();
  await page.locator(".canvas-card video").evaluate(async (v) => {
    v.muted = true;
    await v.play();
  });
  await page.waitForFunction(
    () => document.querySelector(".canvas-card video")?.currentTime > 0.2,
  );
  await page.locator(".canvas-card video").evaluate((v) => v.pause());
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify(
      {
        passed: true,
        url,
        checks: [
          "grid preview and selection",
          "390px and light theme",
          "committed response loss and idempotent retry",
          "fixed cropped versions",
          "undo redo and reload",
          "view original source",
          "reference edge from split image",
          "invalid file rejection",
          "upload failure retry",
          "image file import",
          "MP4 drag and drop import and actual playback",
        ],
        nodeCount: doc.document.nodes.length,
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Canvas grid and media import checks passed");
} catch (e) {
  await page.screenshot({ path: out + "/failure.png" }).catch(() => {});
  throw e;
} finally {
  await browser.close();
}
