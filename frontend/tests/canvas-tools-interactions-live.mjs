import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
const fixture = JSON.parse(await readFile(out + "/fixture.json", "utf8")),
  source = JSON.parse(
    await readFile(process.env.CANVAS_SOURCE_FIXTURE, "utf8"),
  );
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
  }),
  page = await context.newPage(),
  errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
try {
  const auth = await context.request.post(base + "/api/v1/auth/login", {
      data: { username: "demo", password: "demo1234" },
    }),
    token = (await auth.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](base + "/api/v1" + path, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  const root = "/projects/" + fixture.project,
    path = root + "/canvases/" + fixture.other,
    url = base + root + "/canvas?canvas=" + fixture.other;
  assert.equal((await call("get", root)).name, "画布体验验收 · 媒体创作");
  const node = (id, kind, x, y) => ({
    id,
    position: { x, y },
    width: 280,
    height: 230,
    data: {
      kind,
      label: id,
      text: kind === "generate" ? "保持 @【image】" : "光影",
      target_type: "shot",
      target_id: source.shots[0],
      generation_id: kind === "shot" ? source.a.id : null,
      mentions: id === "gen1" ? [{ node_id: "image", alias: "image" }] : [],
      generate: {
        provider: "mock",
        request_type: "image",
        count: 1,
        params: {},
        provider_params: {},
        use_references: false,
      },
    },
  });
  const reset = async (document) => {
    const current = await call("get", path);
    assert.equal(current.name, "画布跨页粘贴验收");
    await call("put", path, {
      name: current.name,
      revision: current.revision,
      document,
    });
    await page.goto(url);
    await page.getByLabel("画布名称").waitFor();
    await page.waitForTimeout(350);
  };
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    if (!localStorage.getItem("inspiration_theme"))
      localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await reset({
    nodes: [
      node("image", "shot", 90, 110),
      node("gen1", "generate", 620, 110),
      node("gen2", "generate", 620, 550),
      node("note", "text", 120, 550),
    ],
    edges: [
      {
        id: "edge",
        source: "image",
        target: "gen1",
        sourceHandle: "image",
        targetHandle: "reference",
      },
    ],
    viewport: { x: 0, y: 0, zoom: 1 },
  });
  const save = async () => {
    await page.waitForTimeout(400);
    if (
      !(await page
        .getByRole("button", { name: "保存", exact: true })
        .isDisabled())
    ) {
      const waiting = page.waitForResponse(
        (r) => r.request().method() === "PUT" && r.url().endsWith(path),
      );
      await page.getByRole("button", { name: "保存", exact: true }).click();
      assert.ok((await waiting).ok());
    }
    return call("get", path);
  };
  const drag = async (locator, target) => {
    const b = await locator.boundingBox();
    assert.ok(b);
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
    await page.mouse.down();
    await page.mouse.move(target.x, target.y, { steps: 18 });
    await page.mouse.up();
  };
  const target = await page
    .locator(
      '.react-flow__node[data-id="gen2"] .react-flow__handle[data-handleid="reference"]',
    )
    .boundingBox();
  await drag(
    page.locator(
      '.react-flow__edge[data-id="edge"] .react-flow__edgeupdater-target',
    ),
    { x: target.x + target.width / 2, y: target.y + target.height / 2 },
  );
  let doc = await save();
  assert.equal(doc.document.edges[0].target, "gen2");
  assert.equal(
    doc.document.nodes.find((n) => n.id === "gen1").data.mentions.length,
    0,
  );
  assert.equal(
    doc.document.nodes.find((n) => n.id === "gen1").data.text,
    "保持 image",
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  doc = await save();
  assert.equal(doc.document.edges[0].target, "gen1");
  assert.equal(
    doc.document.nodes.find((n) => n.id === "gen1").data.mentions.length,
    1,
  );
  checks.push(
    "Real edge-endpoint reconnection updates binding, and undo restores edge plus mention",
  );
  await page.locator(".canvas-surface").focus();
  await page.keyboard.press("Control+a");
  await page.getByLabel("对齐与分布", { exact: true }).selectOption("right");
  doc = await save();
  assert.equal(
    new Set(doc.document.nodes.map((n) => n.position.x + n.width)).size,
    1,
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await page.locator(".canvas-surface").focus();
  await page.keyboard.press("Control+a");
  await page.getByLabel("对齐与分布", { exact: true }).selectOption("vertical");
  doc = await save();
  const sorted = doc.document.nodes.toSorted(
      (a, b) => a.position.y - b.position.y,
    ),
    gaps = sorted
      .slice(1)
      .map((n, i) => n.position.y - sorted[i].position.y - sorted[i].height);
  assert.ok(Math.max(...gaps) - Math.min(...gaps) < 0.01);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await save();
  checks.push(
    "Full selection, right alignment, equal vertical gaps and undo persist",
  );
  await page.getByRole("button", { name: "画布工具", exact: true }).click();
  await page.getByRole("button", { name: "开启网格吸附", exact: true }).click();
  await page.getByRole("button", { name: "画布工具", exact: true }).click();
  await page
    .locator('.react-flow__node[data-id="note"] .canvas-node-title')
    .click();
  const noteBox = await page
    .locator('.react-flow__node[data-id="note"] .canvas-node-title')
    .boundingBox();
  await drag(
    page.locator('.react-flow__node[data-id="note"] .canvas-node-title'),
    {
      x: noteBox.x + noteBox.width / 2 + 71,
      y: noteBox.y + noteBox.height / 2 + 37,
    },
  );
  doc = await save();
  const note = doc.document.nodes.find((n) => n.id === "note");
  assert.equal(note.position.x % 24, 0);
  assert.equal(note.position.y % 24, 0);
  await page
    .locator('.react-flow__node[data-id="note"] .canvas-node-title')
    .click({ button: "right" });
  await page.locator(".canvas-tools-menu").waitFor();
  await page.getByRole("button", { name: "画布工具", exact: true }).click();
  checks.push("Real node dragging snaps to 24px grid; node context menu opens");
  // Live opt-in request and status, using the offline provider only.
  const a = node("a", "generate", 80, 100),
    b = node("b", "generate", 620, 100);
  a.data.text = "第一次";
  b.data.text = "下一镜";
  await reset({
    nodes: [a, b],
    edges: [
      {
        id: "ab",
        source: "a",
        target: "b",
        sourceHandle: "text",
        targetHandle: "prompt",
      },
    ],
    viewport: { x: 0, y: 0, zoom: 1 },
  });
  const run = async () => {
    const waiting = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" && r.url().endsWith(path + "/runs"),
    );
    await page.getByRole("button", { name: "运行画布", exact: true }).click();
    const response = await waiting;
    assert.ok(response.ok(), await response.text());
    const initial = await response.json();
    let result;
    for (let i = 0; i < 100; i++) {
      result = (await call("get", path + "/runs")).find(
        (r) => r.id === initial.id,
      );
      if (result.status === "succeeded" || result.status === "failed") break;
      await page.waitForTimeout(300);
    }
    assert.equal(result.status, "succeeded", JSON.stringify(result));
    await page.waitForTimeout(1800);
    return { result, request: response.request().postDataJSON() };
  };
  await run();
  await page
    .getByRole("checkbox", { name: "复用未变化的结果", exact: true })
    .check();
  const cached = await run();
  assert.equal(cached.request.reuse_unchanged, true);
  assert.equal(cached.result.steps.a.reused, true);
  assert.equal(cached.result.steps.b.reused, true);
  await page.getByText("已复用", { exact: true }).first().waitFor();
  await page.screenshot({
    path: out + "/reused-results.png",
    animations: "disabled",
  });
  checks.push(
    "Live opt-in execution sends reuse flag and displays reused output status",
  );
  // Read-only visits may not erase a separate editing draft or write on zoom/export.
  const me = await call("get", "/me"),
    testKey = `canvas-draft:${me.user.id}:${fixture.project}:${fixture.other}`;
  await page.evaluate(
    (k) => localStorage.setItem(k, "preserve this draft"),
    testKey,
  );
  await page.goto(url + "&view=readonly");
  await page.getByLabel("画布名称").waitFor();
  await page.locator(".react-flow__controls-fitview").click();
  assert.equal(
    await page.evaluate((k) => localStorage.getItem(k), testKey),
    "preserve this draft",
  );
  assert.equal(
    await page.getByRole("button", { name: "保存", exact: true }).count(),
    0,
  );
  assert.equal(
    await page.getByRole("button", { name: "运行画布", exact: true }).count(),
    0,
  );
  await page.screenshot({
    path: out + "/readonly-clean.png",
    animations: "disabled",
  });
  await page.evaluate(() => localStorage.setItem("inspiration_theme", "light"));
  await page.reload();
  await page.getByLabel("画布名称").waitFor();
  assert.ok(
    await page.locator("html").evaluate((el) => el.classList.contains("light")),
  );
  await page.screenshot({
    path: out + "/canvas-light.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: out + "/readonly-mobile.png",
    animations: "disabled",
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 2,
    ),
    false,
  );
  checks.push(
    "Read-only controls, draft preservation, light theme and narrow viewport",
  );
  // Open actual exported package without any network authorization or server.
  const offline = await context.newPage();
  await offline.goto(pathToFileURL(out + "/offline/index.html").href);
  await offline.waitForFunction(() =>
    [...document.images].every((i) => i.complete && i.naturalWidth > 0),
  );
  const video = offline.locator("video");
  await video.evaluate(async (v) => {
    v.muted = true;
    await v.play();
  });
  await offline.waitForFunction(
    () => document.querySelector("video").currentTime > 0.2,
  );
  assert.ok((await offline.locator("img").count()) >= 4);
  await offline.screenshot({
    path: out + "/offline-preview.png",
    animations: "disabled",
  });
  checks.push(
    "Unzipped local HTML loads images and plays the trimmed video without network access",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/interaction-report.json",
    JSON.stringify({ passed: true, checks, errors }, null, 2),
  );
  console.log(JSON.stringify({ passed: true, checks }));
} catch (e) {
  await page.screenshot({
    path: out + "/interaction-failure.png",
    fullPage: true,
  });
  await writeFile(
    out + "/interaction-failure.json",
    JSON.stringify({ error: String(e), checks, errors }, null, 2),
  );
  throw e;
} finally {
  await browser.close();
}
