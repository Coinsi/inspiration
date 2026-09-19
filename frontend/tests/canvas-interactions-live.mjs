import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
assert.ok(base && out);
const saved = JSON.parse(await readFile(`${out}/report.json`, "utf8"));
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1560, height: 1080 },
  }),
  page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
try {
  const a = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(a.ok());
  const token = (await a.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  const root = `/projects/${saved.project}`,
    path = `${root}/canvases/${saved.canvas}`;
  assert.equal((await call("get", root)).name, "画布验收 · 连线与恢复");
  await context.addInitScript((token) => {
    localStorage.setItem("inspiration_token", token);
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(`${base}${root}/canvas?canvas=${saved.canvas}`);
  const card = (label) =>
    page
      .locator(".react-flow__node")
      .filter({ has: page.locator(".canvas-node-title", { hasText: label }) })
      .locator(".canvas-node-title");
  await card("电影风格").click();
  await page.keyboard.down("Shift");
  await card("日落海边").click();
  await page.keyboard.up("Shift");
  await page.getByRole("button", { name: "选中节点分组", exact: true }).click();
  await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === "PUT" && r.url().includes("/canvases/"),
    ),
    page.getByRole("button", { name: "保存", exact: true }).click(),
  ]);
  await page.getByText(/已保存 · 版本/).waitFor();
  let doc = await call("get", path);
  const group = doc.document.nodes.find((n) => n.data.kind === "group");
  assert.ok(group);
  assert.equal(
    doc.document.nodes.filter((n) => n.parentId === group.id).length,
    2,
  );
  const handle = page.locator(".canvas-group-label");
  const bounds = await handle.boundingBox();
  assert.ok(bounds);
  await page.mouse.move(bounds.x + 30, bounds.y + 15);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 80, bounds.y + 65, { steps: 12 });
  await page.mouse.up();
  await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === "PUT" && r.url().includes("/canvases/"),
    ),
    page.getByRole("button", { name: "保存", exact: true }).click(),
  ]);
  await page.getByText(/已保存 · 版本/).waitFor();
  doc = await call("get", path);
  const moved = doc.document.nodes.find((n) => n.id === group.id);
  assert.notDeepEqual(moved.position, group.position);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === "PUT" && r.url().includes("/canvases/"),
    ),
    page.getByRole("button", { name: "保存", exact: true }).click(),
  ]);
  await page.getByText(/已保存 · 版本/).waitFor();
  doc = await call("get", path);
  assert.deepEqual(
    doc.document.nodes.find((n) => n.id === group.id).position,
    group.position,
  );
  await page.screenshot({
    path: `${out}/canvas-grouped.png`,
    animations: "disabled",
  });
  const large = await call("post", `${root}/canvases`, {
    name: "500 节点交互验收",
  });
  const nodes = Array.from({ length: 500 }, (_, i) => ({
    id: `text-${i}`,
    position: { x: (i % 25) * 300, y: Math.floor(i / 25) * 250 },
    data: { kind: "text", label: `素材 ${i}`, text: "大画布交互样本" },
  }));
  await call("put", `${root}/canvases/${large.id}`, {
    name: large.name,
    revision: 0,
    document: { nodes, edges: [], viewport: { x: 30, y: 80, zoom: 1 } },
  });
  const start = performance.now();
  await page.goto(`${base}${root}/canvas?canvas=${large.id}`);
  await card("素材 0").click();
  await page.getByLabel("节点正文").fill("大画布中的编辑仍然可保存");
  await Promise.all([
    page.waitForResponse(
      (r) => r.request().method() === "PUT" && r.url().includes("/canvases/"),
    ),
    page.getByRole("button", { name: "保存", exact: true }).click(),
  ]);
  await page.getByText(/已保存 · 版本 2/).waitFor();
  const elapsed = performance.now() - start;
  const largeDoc = await call("get", `${root}/canvases/${large.id}`);
  assert.equal(largeDoc.document.nodes.length, 500);
  assert.equal(
    largeDoc.document.nodes[0].data.text,
    "大画布中的编辑仍然可保存",
  );
  const rendered = await page.locator(".react-flow__node").count();
  assert.ok(rendered < 500);
  await page.screenshot({
    path: `${out}/canvas-500-nodes.png`,
    animations: "disabled",
  });
  assert.deepEqual(errors, []);
  const report = {
    passed: true,
    project: saved.project,
    canvas: saved.canvas,
    large_canvas: large.id,
    load_edit_save_500_ms: Math.round(elapsed),
    rendered_nodes: rendered,
    checks: [
      "Group selection and persisted parent references",
      "Dragging whole group and undo restore positions",
      "500 nodes load/edit/save; only visible nodes rendered",
    ],
    errors,
  };
  await writeFile(
    `${out}/interaction-report.json`,
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report));
} catch (e) {
  await page.screenshot({
    path: `${out}/interactions-failure.png`,
    fullPage: true,
  });
  throw e;
} finally {
  await browser.close();
}
