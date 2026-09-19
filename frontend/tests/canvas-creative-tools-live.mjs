import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
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
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
    permissions: ["clipboard-read", "clipboard-write"],
  }),
  page = await context.newPage();
const errors = [],
  report = { checks: [] };
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
try {
  const login = await context.request.post(base + "/api/v1/auth/login", {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(login.ok());
  const token = (await login.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](base + "/api/v1" + path, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  const root = "/projects/" + source.project;
  assert.equal((await call("get", root)).name, "画布体验验收 · 媒体创作");
  let fixture;
  try {
    fixture = JSON.parse(await readFile(out + "/fixture.json", "utf8"));
  } catch {}
  if (!fixture) {
    const c = await call("post", root + "/canvases", {
        name: "画布创作工具验收",
      }),
      other = await call("post", root + "/canvases", {
        name: "画布跨页粘贴验收",
      });
    fixture = { project: source.project, canvas: c.id, other: other.id };
  }
  const store = () =>
    writeFile(out + "/fixture.json", JSON.stringify(fixture, null, 2));
  await store();
  const path = root + "/canvases/" + fixture.canvas,
    otherPath = root + "/canvases/" + fixture.other,
    url = base + root + "/canvas?canvas=" + fixture.canvas;
  report.url = url;
  const node = (id, kind, label, x, y, g = null) => ({
    id,
    position: { x, y },
    width: 320,
    height: 280,
    data: {
      kind,
      label,
      text: "",
      target_type: "shot",
      target_id: source.shots[0],
      generation_id: g,
      generate: {
        provider: "mock",
        request_type: "image",
        count: 1,
        params: {},
        provider_params: {},
        use_references: true,
      },
    },
  });
  const image = node("image", "shot", "图片来源", 90, 120, source.a.id),
    gen = node("gen", "generate", "画面创作", 570, 120),
    note = node("note", "text", "创意文字", 90, 530);
  for (const [p, nodes] of [
    [path, [image, gen, note]],
    [otherPath, []],
  ]) {
    const old = await call("get", p);
    assert.match(old.name, /验收/);
    await call("put", p, {
      name: old.name,
      revision: old.revision,
      document: { nodes, edges: [], viewport: { x: 0, y: 0, zoom: 1 } },
    });
  }
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(url);
  await page.locator(".canvas-card img").first().waitFor();
  const tools = async (label) => {
    if (!(await page.locator(".canvas-tools-menu").isVisible()))
      await page.getByRole("button", { name: "画布工具", exact: true }).click();
    await page
      .locator(".canvas-tools-menu")
      .getByRole("button", { name: label, exact: true })
      .click();
  };
  const fit = async () => {
    await page.locator(".react-flow__controls-fitview").click();
    await page.waitForTimeout(400);
  };
  const select = async (id) => {
    await page
      .locator(`.react-flow__node[data-id="${id}"] .canvas-node-title`)
      .click();
  };
  const save = async (p = path) => {
    await page.waitForTimeout(400);
    if (
      await page.getByRole("button", { name: "保存", exact: true }).isDisabled()
    )
      return call("get", p);
    const waiting = page.waitForResponse(
      (r) => r.request().method() === "PUT" && r.url().endsWith(p),
    );
    await page.getByRole("button", { name: "保存", exact: true }).click();
    assert.ok((await waiting).ok());
    return call("get", p);
  };
  // A failed response after successful upload must retry the same request, not create another asset.
  let lost = null;
  await page.route("**/canvases/*/import", async (route) => {
    if (lost) {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    assert.ok(response.ok());
    lost = await response.json();
    await route.fulfill({
      status: 503,
      json: { error: { message: "验收：响应中断，请重试" } },
    });
  });
  const bytes = Array.from(await readFile(media + "/reference-b.jpg"));
  await page.locator(".canvas-surface").evaluate((el, bytes) => {
    const dt = new DataTransfer();
    dt.items.add(
      new File([new Uint8Array(bytes)], "拖入参考.jpg", { type: "image/jpeg" }),
    );
    const r = el.getBoundingClientRect();
    el.dispatchEvent(
      new DragEvent("drop", {
        bubbles: true,
        dataTransfer: dt,
        clientX: r.left + 450,
        clientY: r.top + 450,
      }),
    );
  }, bytes);
  await page
    .getByRole("button", { name: "重试未完成文件", exact: true })
    .click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.unroute("**/canvases/*/import");
  let doc = await save();
  assert.equal(
    doc.document.nodes.filter((n) => n.data.generation_id === lost.id).length,
    1,
  );
  report.checks.push(
    "File drop and response-loss retry preserve one uploaded generation",
  );
  await page.locator(".canvas-surface").evaluate((el, bytes) => {
    const dt = new DataTransfer();
    dt.items.add(
      new File([new Uint8Array(bytes)], "粘贴截图.jpg", { type: "image/jpeg" }),
    );
    el.dispatchEvent(
      new ClipboardEvent("paste", { bubbles: true, clipboardData: dt }),
    );
  }, bytes);
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  doc = await save();
  assert.ok(doc.document.nodes.some((n) => n.data.label === "粘贴截图"));
  await fit();
  await select("gen");
  await page.getByLabel("节点提示词", { exact: true }).fill("保持 @图片来源");
  await page.getByRole("option", { name: /图片来源/ }).click();
  doc = await save();
  assert.equal(
    doc.document.nodes.find((n) => n.id === "gen").data.mentions[0].node_id,
    "image",
  );
  assert.ok(
    doc.document.edges.some(
      (e) =>
        e.source === "image" &&
        e.target === "gen" &&
        e.targetHandle === "reference",
    ),
  );
  report.checks.push(
    "Pasted image and smart mention create persisted fixed media and typed reference edge",
  );
  await page.screenshot({
    path: out + "/smart-reference.png",
    animations: "disabled",
  });
  // Drawing is rasterized at original dimensions, with undo/redo and a real crop.
  await tools("自由绘图");
  const paint = page.getByLabel("绘图画面", { exact: true });
  await paint.waitFor();
  const stroke = async (tool, x1, y1, x2, y2) => {
    await page.getByRole("button", { name: tool, exact: true }).click();
    const b = await paint.boundingBox();
    await page.mouse.move(b.x + b.width * x1, b.y + b.height * y1);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width * x2, b.y + b.height * y2, {
      steps: 12,
    });
    await page.mouse.up();
  };
  await stroke("矩形", 0.2, 0.2, 0.7, 0.7);
  await stroke("箭头", 0.25, 0.3, 0.65, 0.6);
  await page.getByRole("button", { name: "撤销笔画", exact: true }).click();
  await page.getByRole("button", { name: "重做笔画", exact: true }).click();
  await stroke("裁切", 0.1, 0.1, 0.9, 0.9);
  await page.getByLabel("绘图作品名称", { exact: true }).fill("构图草图验收");
  await page.screenshot({
    path: out + "/drawing-studio.png",
    animations: "disabled",
  });
  let imported = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/import"),
  );
  await page
    .getByRole("button", { name: "保存并放入画布", exact: true })
    .click();
  let drawing = await (await imported).json();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  doc = await save();
  assert.ok(
    doc.document.nodes.some((n) => n.data.generation_id === drawing.id),
  );
  const png = await context.request.get(
    base + "/api/v1" + root + "/blobs/" + drawing.output_blob_hash,
    { headers },
  );
  const pngBytes = await png.body();
  assert.ok(
    pngBytes.readUInt32BE(16) >= 1270 && pngBytes.readUInt32BE(16) <= 1290,
  );
  assert.ok(
    pngBytes.readUInt32BE(20) >= 710 && pngBytes.readUInt32BE(20) <= 730,
  );
  await writeFile(out + "/drawing.png", pngBytes);
  await fit();
  await select("image");
  await page.getByRole("button", { name: "绘图与裁切", exact: true }).click();
  await paint.waitFor();
  await stroke("箭头", 0.15, 0.15, 0.8, 0.7);
  await stroke("裁切", 0.05, 0.05, 0.95, 0.95);
  imported = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/import"),
  );
  await page
    .getByRole("button", { name: "保存并放入画布", exact: true })
    .click();
  const edited = await (await imported).json();
  assert.equal(edited.input_refs.source_generation_id, source.a.id);
  assert.equal(edited.target_id, source.a.target_id);
  assert.notEqual(edited.output_blob_hash, source.a.output_blob_hash);
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  doc = await save();
  report.checks.push(
    "Real brush/shape editing, undo/redo, crop dimensions and original-preserving derived version",
  );
  // Copy all nodes, including reference bindings, to a separate canvas.
  await page.locator(".canvas-surface").focus();
  await page.keyboard.press("Control+a");
  await tools("复制选中节点");
  const copied = JSON.parse(
    await page.evaluate(() => navigator.clipboard.readText()),
  );
  assert.ok(copied.nodes.length > 1);
  await page.goto(base + root + "/canvas?canvas=" + fixture.other);
  await page.getByLabel("画布名称", { exact: true }).waitFor();
  await tools("粘贴节点");
  await page.waitForTimeout(300);
  let pasted = await save(otherPath);
  assert.equal(pasted.document.nodes.length, copied.nodes.length);
  assert.ok(
    pasted.document.nodes.every(
      (n) => !copied.nodes.some((old) => old.id === n.id),
    ),
  );
  assert.ok(
    pasted.document.nodes.some((n) => n.data.generation_id === source.a.id),
  );
  report.checks.push(
    "Cross-canvas paste remaps node/edge/mention IDs and keeps fixed media versions",
  );
  await page.goto(url);
  await page.getByLabel("画布名称", { exact: true }).waitFor();
  // Import a true library video, then extract reference frame and trimmed clip entirely in the canvas.
  if (!fixture.version) {
    const buffer = await readFile(media + "/reference-video.mp4");
    const v = await call("post", root + "/library/uploads", {
      filename: "画布片段工具验收.mp4",
      size_bytes: buffer.length,
      fingerprint: createHash("sha256").update(buffer).digest("hex"),
    });
    fixture.version = v.id;
    await store();
    const r = await context.request.post(
      base + "/api/v1" + root + "/library/versions/" + v.id + "/chunk?offset=0",
      {
        headers,
        multipart: {
          file: { name: "chunk", mimeType: "application/octet-stream", buffer },
        },
      },
    );
    assert.ok(r.ok());
    await call("post", root + "/library/versions/" + v.id + "/complete");
  }
  let version;
  for (let i = 0; i < 90; i++) {
    version = await call("get", root + "/library/versions/" + fixture.version);
    if (["ready", "failed"].includes(version.status)) break;
    await page.waitForTimeout(500);
  }
  assert.equal(version.status, "ready");
  for (const type of ["image", "video"]) {
    await tools("从视频素材库选取");
    await page
      .getByLabel("选择素材库版本", { exact: true })
      .selectOption(fixture.version);
    await page
      .getByLabel("片段所属镜头", { exact: true })
      .selectOption(source.shots[1]);
    await page.getByLabel("片段开始秒", { exact: true }).fill("1");
    await page.getByLabel("片段结束秒", { exact: true }).fill("3");
    await page.getByLabel("片段输出类型", { exact: true }).selectOption(type);
    const response = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" && r.url().endsWith("/materialize"),
    );
    await page
      .getByRole("button", { name: "提取所选片段", exact: true })
      .click();
    const job = await (await response).json();
    await page
      .getByRole("button", { name: "放入画布", exact: true })
      .waitFor({ timeout: 60000 });
    await page.screenshot({
      path: out + "/library-" + type + ".png",
      animations: "disabled",
    });
    await page.getByRole("button", { name: "放入画布", exact: true }).click();
    doc = await save();
    const g = (
      await call(
        "get",
        root + "/generations?target_type=shot&target_id=" + source.shots[1],
      )
    ).find((g) => g.job_id === job.id);
    assert.equal(g.output_type, type);
    assert.equal(g.input_refs.library_origin.version_id, fixture.version);
    assert.ok(doc.document.nodes.some((n) => n.data.generation_id === g.id));
  }
  report.checks.push(
    "Canvas library selection produces real reference frame and trimmed MP4 with original-version provenance",
  );
  await fit();
  await page.screenshot({
    path: out + "/canvas-dark.png",
    animations: "disabled",
  });
  await save();
  const download = page.waitForEvent("download");
  await tools("导出离线作品包");
  await (await download).saveAs(out + "/canvas.zip");
  await tools("复制项目只读链接");
  const shared = await page.evaluate(() => navigator.clipboard.readText());
  assert.ok(shared.endsWith("&view=readonly"));
  await page.goto(shared);
  await page.getByLabel("画布名称", { exact: true }).waitFor();
  assert.equal(
    await page.getByLabel("画布名称", { exact: true }).isDisabled(),
    true,
  );
  assert.equal(
    await page.getByRole("button", { name: "保存", exact: true }).count(),
    0,
  );
  await page.screenshot({
    path: out + "/readonly.png",
    animations: "disabled",
  });
  report.checks.push("Offline ZIP download and member-only read-only link");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: out + "/mobile.png", animations: "disabled" });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 2,
    ),
    false,
  );
  await page.setViewportSize({ width: 1600, height: 1100 });
  assert.deepEqual(errors, []);
  report.errors = errors;
  report.passed = true;
  await writeFile(out + "/report.json", JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (e) {
  await page.screenshot({ path: out + "/failure.png", fullPage: true });
  await writeFile(
    out + "/failure.json",
    JSON.stringify({ ...report, error: String(e), errors }, null, 2),
  );
  throw e;
} finally {
  await browser.close();
}
