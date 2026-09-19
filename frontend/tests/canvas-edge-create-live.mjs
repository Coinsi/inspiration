import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
assert.ok(base && out && process.env.CANVAS_SOURCE_FIXTURE);
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
  }),
  page = await context.newPage(),
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
      name: "画布交互验收 · 拖线创建",
    });
    fixture = { project: source.project, canvas: c.id };
    await writeFile(out + "/fixture.json", JSON.stringify(fixture, null, 2));
  }
  const path = root + "/canvases/" + fixture.canvas,
    existing = await call("get", path);
  assert.equal(existing.name, "画布交互验收 · 拖线创建");
  const image = {
    id: "image",
    position: { x: 120, y: 120 },
    width: 320,
    height: 260,
    data: {
      kind: "shot",
      label: "图片来源",
      text: "参考光影",
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
  };
  const note = {
    ...image,
    id: "note",
    position: { x: 120, y: 520 },
    data: {
      ...image.data,
      kind: "text",
      label: "创意文字",
      text: "镜头方向",
      generation_id: null,
    },
  };
  await call("put", path, {
    name: existing.name,
    revision: existing.revision,
    document: {
      nodes: [image, note],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    },
  });
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  const url = base + root + "/canvas?canvas=" + fixture.canvas;
  await page.goto(url);
  await page.locator(".canvas-card img").waitFor();
  const handle = (id, name, type) =>
    page.locator(
      `.react-flow__node[data-id="${id}"] .react-flow__handle-${type}[data-handleid="${name}"]`,
    );
  async function drag(locator, end, escape = false) {
    const box = await locator.boundingBox();
    assert.ok(box);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(end.x, end.y, { steps: 14 });
    if (escape) await page.keyboard.press("Escape");
    await page.mouse.up();
  }
  const surface = await page.locator(".canvas-surface").boundingBox();
  const drop = { x: surface.x + 700, y: surface.y + 420 };
  const menu = page.getByRole("dialog", { name: "拖线新增节点", exact: true });
  await drag(handle("image", "image", "right"), drop);
  await menu.waitFor();
  assert.equal(await menu.getByRole("button").count(), 2);
  await page.screenshot({ path: out + "/drop-menu.png" });
  await page.keyboard.press("Escape");
  assert.equal(await menu.count(), 0);
  assert.equal(await page.locator(".react-flow__node").count(), 2);
  await drag(handle("image", "image", "right"), drop, true);
  assert.equal(await menu.count(), 0);
  await drag(handle("image", "image", "right"), { x: 30, y: 30 });
  assert.equal(await menu.count(), 0);
  const noteBox = await page
    .locator('.react-flow__node[data-id="note"]')
    .boundingBox();
  await drag(handle("image", "image", "right"), {
    x: noteBox.x + 100,
    y: noteBox.y + 100,
  });
  assert.equal(await menu.count(), 0);
  const originBox = await page
    .locator('.react-flow__node[data-id="image"]')
    .boundingBox();
  const expectedX = 120 + (drop.x - originBox.x) / (originBox.width / 320);
  await drag(handle("image", "image", "right"), drop);
  await menu.getByRole("button", { name: "图片创作", exact: true }).click();
  const save = async () => {
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await page.getByText(/已保存 · 版本/).waitFor();
    return call("get", path);
  };
  let doc = await save();
  assert.equal(doc.document.nodes.length, 3);
  assert.equal(doc.document.edges.length, 1);
  const next = doc.document.nodes.find(
    (n) => !["image", "note"].includes(n.id),
  );
  assert.equal(next.data.target_id, source.shots[0]);
  assert.equal(next.data.generate.request_type, "image");
  assert.equal(doc.document.edges[0].sourceHandle, "image");
  assert.equal(doc.document.edges[0].targetHandle, "reference");
  assert.ok(Math.abs(next.position.x - expectedX) < 3);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  doc = await save();
  assert.equal(doc.document.nodes.length, 2);
  assert.equal(doc.document.edges.length, 0);
  await page.getByRole("button", { name: "重做", exact: true }).click();
  doc = await save();
  assert.equal(doc.document.nodes.length, 3);
  assert.equal(doc.document.edges.length, 1);
  await page.reload();
  await handle(next.id, "prompt", "left").waitFor();
  let input = await handle(next.id, "prompt", "left").boundingBox();
  await drag(handle("image", "image", "right"), {
    x: input.x + input.width / 2,
    y: input.y + input.height / 2,
  });
  assert.equal(await menu.count(), 0);
  assert.equal((await call("get", path)).document.edges.length, 1);
  input = await handle(next.id, "prompt", "left").boundingBox();
  await drag(handle("note", "text", "right"), {
    x: input.x + input.width / 2,
    y: input.y + input.height / 2,
  });
  doc = await save();
  assert.equal(doc.document.nodes.length, 3);
  assert.equal(doc.document.edges.length, 2);
  // Reverse drag from a prompt creates an upstream text node and correct edge direction.
  await drag(handle(next.id, "prompt", "left"), {
    x: surface.x + 680,
    y: surface.y + 720,
  });
  await menu.getByRole("button", { name: "创意文字", exact: true }).click();
  doc = await save();
  const upstream = doc.document.nodes.find(
    (n) => !["image", "note", next.id].includes(n.id),
  );
  assert.equal(upstream.data.kind, "text");
  assert.ok(
    doc.document.edges.some(
      (e) =>
        e.source === upstream.id &&
        e.target === next.id &&
        e.targetHandle === "prompt",
    ),
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await save();
  await drag(handle(next.id, "reference", "left"), {
    x: surface.x + 680,
    y: surface.y + 720,
  });
  await menu.getByRole("button", { name: "图片创作", exact: true }).click();
  doc = await save();
  const upstreamImage = doc.document.nodes.find(
    (n) => !["image", "note", next.id].includes(n.id),
  );
  assert.equal(upstreamImage.data.kind, "generate");
  assert.equal(upstreamImage.data.generate.request_type, "image");
  assert.ok(
    doc.document.edges.some(
      (e) =>
        e.source === upstreamImage.id &&
        e.target === next.id &&
        e.sourceHandle === "image" &&
        e.targetHandle === "reference",
    ),
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await save();
  // Text output can create a video via the keyboard; the original text port is retained.
  await drag(handle("note", "text", "right"), {
    x: surface.x + 1150,
    y: surface.y + 690,
  });
  await menu.waitFor();
  assert.equal(await menu.getByRole("button").count(), 3);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  doc = await save();
  const video = doc.document.nodes.find(
    (n) => n.data.generate.request_type === "video",
  );
  assert.ok(video);
  assert.ok(
    doc.document.edges.some(
      (e) =>
        e.source === "note" &&
        e.target === video.id &&
        e.sourceHandle === "text" &&
        e.targetHandle === "prompt",
    ),
  );
  await page.locator(".react-flow__controls-zoomout").click();
  await page.locator(".react-flow__controls-zoomout").click();
  await page.waitForTimeout(350);
  const zoomedNote = await page
    .locator('.react-flow__node[data-id="note"]')
    .boundingBox();
  const audioDrop = await page.locator('.canvas-surface').evaluate(element => {
    const rect = element.getBoundingClientRect();
    for (let y = rect.bottom - 90; y > rect.top + 200; y -= 90)
      for (let x = rect.right - 90; x > rect.left + 600; x -= 90)
        if (document.elementFromPoint(x,y)?.classList.contains('react-flow__pane')) return {x,y};
    throw new Error('No visible blank drop point');
  });
  const audioX = 120 + (audioDrop.x - zoomedNote.x) / (zoomedNote.width / 320);
  await drag(handle("note", "text", "right"), audioDrop);
  await menu.waitFor();
  const menuBox = await menu.boundingBox();
  assert.ok(
    menuBox.x >= surface.x &&
      menuBox.x + menuBox.width <= surface.x + surface.width,
  );
  assert.ok(
    menuBox.y >= surface.y &&
      menuBox.y + menuBox.height <= surface.y + surface.height,
  );
  await menu.getByRole("button", { name: "声音创作", exact: true }).click();
  doc = await save();
  const audio = doc.document.nodes.find(
    (n) => n.data.generate.request_type === "audio",
  );
  assert.ok(audio);
  assert.ok(Math.abs(audio.position.x - audioX) < 4);
  assert.ok(
    doc.document.edges.some(
      (e) => e.source === "note" && e.target === audio.id,
    ),
  );
  await page.reload();
  await page.getByLabel("画布名称").waitFor();
  await page.locator(".react-flow__controls-fitview").click();
  await page.waitForTimeout(350);
  await page.screenshot({ path: out + "/connected-nodes.png" });
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify(
      {
        passed: true,
        url,
        checks: [
          "image drag to blank creates correct edge and target",
          "drop position",
          "cancel menu and active drag",
          "drop outside canvas or onto node does not create",
          "atomic undo redo",
          "save reload",
          "invalid port rejected without menu",
          "existing node connection",
          "reverse prompt creates upstream text",
          "reverse image input creates upstream image generation",
          "zoomed drop coordinates and menu boundary",
          "text output creates video via keyboard",
        ],
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Drag-to-create canvas checks passed");
} catch (e) {
  await page.screenshot({ path: out + "/failure.png" }).catch(() => {});
  throw e;
} finally {
  await browser.close();
}
