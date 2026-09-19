import { toggleAccountTheme } from "./helpers/account.mjs";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
assert.ok(base && out);
await mkdir(out, { recursive: true });
const { chromium } = await import(
    pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
  ),
  browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1560, height: 1080 },
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
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("shots.wide-panel", "true");
  }, token);
  const project = await call("post", "/projects", {
      code: "CREATIVE-UI-" + Date.now(),
      name: "创作工作区验收 · 镜头与画布",
    }),
    root = "/projects/" + project.id;
  const script = await call("post", root + "/scripts", { title: "雨夜重逢" });
  await call("post", root + `/scripts/${script.id}/apply-scenes`, {
    scenes: [
      {
        title: "雨夜街头",
        shots: [{ title: "城市远景" }, { title: "雨伞下的重逢" }],
      },
    ],
  });
  const shots = await call("get", root + "/shots");
  await page.goto(base + root + "/shots?shot=" + shots[0].id);
  await page.getByRole("tab", { name: "生成出图", exact: true }).click();
  const prompt = page.getByLabel("本次生成指令", { exact: true });
  await prompt.fill("第一个镜头的本机草稿");
  await page.getByRole("button", { name: "下一个镜头", exact: true }).click();
  assert.equal(await prompt.inputValue(), "");
  await prompt.fill("第二个镜头的草稿");
  await page.getByRole("button", { name: "上一个镜头", exact: true }).click();
  assert.equal(await prompt.inputValue(), "第一个镜头的本机草稿");
  await page.getByLabel("生成数量", { exact: true }).selectOption("1");
  const response = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/generate"),
  );
  await page.getByRole("button", { name: "生成", exact: true }).click();
  const job = await (await response).json();
  let status;
  for (let i = 0; i < 90; i++) {
    status = await call("get", root + "/jobs/" + job.id);
    if (["succeeded", "failed"].includes(status.status)) break;
    await page.waitForTimeout(500);
  }
  assert.equal(status.status, "succeeded");
  await page.locator("[data-generation-id]").first().waitFor();
  await page.getByRole("button", { name: "钦定", exact: true }).click();
  await page.getByRole("button", { name: "已钦定", exact: true }).waitFor();
  await page.screenshot({ path: out + "/shot-generation-live.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.locator("main").evaluate((e) => e.scrollWidth <= e.clientWidth),
  );
  await page.screenshot({ path: out + "/shot-generation-mobile.png" });
  await page.setViewportSize({ width: 1560, height: 1080 });
  let releaseResponse, pendingReady;
  const pendingResponse = new Promise((resolve) => (pendingReady = resolve));
  await page.route("**/generate", async (route) => {
    const response = await route.fetch();
    pendingReady(await response.json());
    await new Promise((resolve) => (releaseResponse = resolve));
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "生成", exact: true }).click();
  const lateJob = await pendingResponse;
  await page.getByRole("button", { name: "下一个镜头", exact: true }).click();
  releaseResponse();
  const jobKey = `gen.job.${project.id}.shot.${shots[0].id}`;
  await page.waitForFunction(
    ({ key, id }) => JSON.parse(localStorage.getItem(key) || "null") === id,
    { key: jobKey, id: lateJob.id },
  );
  await page.getByRole("button", { name: "上一个镜头", exact: true }).click();
  await page.getByRole("link", { name: /查看任务/ }).waitFor();
  assert.ok(
    (
      await page.getByRole("link", { name: /查看任务/ }).getAttribute("href")
    ).includes(lateJob.id),
  );
  await page.unroute("**/generate");
  const canvas = await call("post", root + "/canvases", {
      name: "雨夜创作 · 分支尝试",
    }),
    path = root + "/canvases/" + canvas.id;
  const make = (id, kind, x) => ({
    id,
    position: { x, y: 120 },
    data: {
      kind,
      label: kind === "text" ? "雨夜氛围" : "远景生成",
      text: "潮湿的街道，安静的灯光。",
      target_type: "shot",
      target_id: kind === "generate" ? shots[0].id : null,
      generation_id: null,
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
  await call("put", path, {
    name: canvas.name,
    revision: 0,
    document: {
      nodes: [make("text", "text", 40), make("gen", "generate", 390)],
      edges: [
        {
          id: "edge",
          source: "text",
          target: "gen",
          sourceHandle: "text",
          targetHandle: "prompt",
        },
      ],
      viewport: { x: 0, y: 0, zoom: 0.9 },
    },
  });
  await page.setViewportSize({ width: 1560, height: 1080 });
  await page.goto(base + root + "/canvas?canvas=" + canvas.id);
  await page
    .locator('.react-flow__node[data-id="gen"] .canvas-node-title')
    .click();
  await page.getByRole("button", { name: "复制选中节点", exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByText(/已保存 · 版本 2/).waitFor();
  let doc = await call("get", path);
  assert.equal(doc.document.nodes.length, 3);
  assert.equal(doc.document.edges.length, 2);
  const copy = doc.document.nodes.find((n) => !["text", "gen"].includes(n.id));
  assert.equal(copy.data.target_id, shots[0].id);
  assert.ok(
    doc.document.edges.some((e) => e.source === "text" && e.target === copy.id),
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByText(/已保存 · 版本 3/).waitFor();
  assert.equal((await call("get", path)).document.nodes.length, 2);
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByText(/已保存 · 版本 4/).waitFor();
  await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
  await page.getByRole("button", { name: "收起属性面板", exact: true }).click();
  assert.equal(await page.locator(".canvas-inspector").isVisible(), false);
  await page.screenshot({ path: out + "/canvas-focus.png" });
  await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
  await page.reload();
  await page.getByLabel("画布名称", { exact: true }).waitFor();
  await page.locator('.react-flow__node[data-id="gen"]').waitFor();
  assert.equal((await call("get", path)).document.nodes.length, 3);
  await page.screenshot({ path: out + "/canvas-branch.png" });
  const edgeChecks = await page.evaluate(async () => {
    const { newNode, duplicateSelection } = await import("/src/lib/canvas.ts");
    const group = {
      ...newNode("group", 100, 100),
      id: "group",
      selected: true,
    };
    const child = {
      ...newNode("text", 25, 30),
      id: "child",
      parentId: "group",
    };
    child.data.label = "x".repeat(120);
    const graph = {
        nodes: [group, child],
        edges: [],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
      copy = duplicateSelection(graph),
      parent = copy.nodes[2],
      kid = copy.nodes[3];
    let capped = false;
    try {
      duplicateSelection({
        nodes: Array.from({ length: 500 }, (_, i) => ({
          ...newNode("text", i, 0),
          selected: i === 0,
        })),
        edges: [],
        viewport: graph.viewport,
      });
    } catch {
      capped = true;
    }
    return {
      group:
        copy.nodes.length === 4 &&
        kid.parentId === parent.id &&
        kid.position.x === 25 &&
        parent.position.x === 148,
      label: kid.data.label.length <= 120,
      capped,
      original: graph.nodes.length === 2 && graph.nodes[0].selected,
    };
  });
  assert.ok(Object.values(edgeChecks).every(Boolean));
  const theme = await page
    .locator(".canvas-card")
    .first()
    .evaluate((e) => ({
      card: getComputedStyle(e).backgroundColor,
      flow: e.closest(".react-flow").className,
    }));
  assert.ok(theme.flow.includes("dark"));
  assert.equal(theme.card, "rgb(26, 26, 26)");
  await toggleAccountTheme(page);
  await page.waitForFunction(() =>
    document.querySelector(".react-flow")?.classList.contains("light"),
  );
  await page.screenshot({ path: out + "/canvas-light.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.locator("main").evaluate((e) => e.scrollWidth <= e.clientWidth),
  );
  await page.screenshot({ path: out + "/canvas-mobile.png", fullPage: true });
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/live-report.json",
    JSON.stringify(
      {
        passed: true,
        project: project.id,
        shots: shots.map((s) => s.id),
        job: job.id,
        lateJob: lateJob.id,
        canvas: canvas.id,
        edgeChecks,
        checks: [
          "real mock generation and explicit selection",
          "next/previous shot preserves separate drafts and current tab",
          "390px shot workspace",
          "canvas branch copy keeps original input edges and target",
          "undo/redo and persisted reload",
          "focus inspector hide/show",
          "group copying and graph limit",
          "390px canvas",
          "late generation response after shot switch",
          "React Flow dark/light theme",
        ],
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Creative workspace live acceptance passed");
} catch (e) {
  await page.screenshot({ path: out + "/live-failure.png", fullPage: true });
  throw e;
} finally {
  await browser.close();
}
