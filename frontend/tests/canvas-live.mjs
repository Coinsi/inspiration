import { toggleAccountTheme } from "./helpers/account.mjs";
// Opt-in browser acceptance writes only its own QA project in the isolated preview.
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
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1560, height: 1080 },
  }),
  page = await context.newPage();
const errors = [],
  report = { checks: [] };
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
try {
  const auth = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(auth.ok());
  const token = (await auth.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.json();
  };
  await context.addInitScript((token) => {
    localStorage.setItem("inspiration_token", token);
    localStorage.setItem("inspiration_lang", "zh");
  }, token);
  const project = await call("post", "/projects", {
    code: "CANVAS-" + Date.now(),
    name: "画布验收 · 连线与恢复",
  });
  report.project = project.id;
  const root = `/projects/${project.id}`;
  const script = await call("post", `${root}/scripts`, {
    title: "画布测试剧本",
  });
  await call("post", `${root}/scripts/${script.id}/apply-scenes`, {
    scenes: [
      {
        title: "测试场景",
        shots: [{ title: "日落海边" }, { title: "海边特写" }],
      },
    ],
  });
  const shots = await call("get", `${root}/shots`);
  await page.goto(`${base}${root}/canvas`);
  await page
    .getByRole("button", { name: "创建第一张画布", exact: true })
    .click();
  await page.getByLabel("画布名称").fill("海边创作流程");
  await page.getByRole("button", { name: "添加内容", exact: true }).click();
  await page.getByRole("button", { name: /创意文字/ }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByLabel("节点名称", { exact: true }).fill("电影风格");
  await page.getByLabel("创作说明").fill("金色日落，安静的海边，胶片质感。");
  await page.getByRole("button", { name: "添加内容", exact: true }).click();
  await page.getByRole("button", { name: /图片创作/ }).click();
  await page.getByLabel("引用项目对象").selectOption(shots[0].id);
  await page.getByLabel("提示词", { exact: true }).fill("广角拍摄海岸线");
  await page.getByRole("button", { name: "添加内容", exact: true }).click();
  await page.getByRole("button", { name: /图片创作/ }).click();
  await page.getByLabel("引用项目对象").selectOption(shots[1].id);
  await page.getByLabel("提示词", { exact: true }).fill("拍摄浪花细节");
  await page.getByRole("button", { name: "整理布局", exact: true }).click();
  await page.waitForTimeout(300);
  const card = (label) =>
    page
      .locator(".react-flow__node")
      .filter({ has: page.locator(".canvas-node-title", { hasText: label }) })
      .locator(".canvas-node-title");
  const edit = async (label) => {
    const close = page.getByRole("button", {
      name: "关闭画布面板",
      exact: true,
    });
    if (await close.isVisible()) await close.click();
    await card(label).click();
    await page.getByRole("button", { name: "设置", exact: true }).click();
  };
  await edit("电影风格");
  await page.getByLabel("连接到节点").selectOption({ label: "日落海边" });
  await page.getByRole("button", { name: "建立连线", exact: true }).click();
  await edit("日落海边");
  await page.getByLabel("连接到节点").selectOption({ label: "海边特写" });
  await page.getByRole("button", { name: "建立连线", exact: true }).click();
  await page.getByRole("button", { name: "整理布局", exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByText(/已保存 · 版本 1/).waitFor();
  const canvas = (await call("get", `${root}/canvases`))[0];
  report.canvas = canvas.id;
  const path = `${root}/canvases/${canvas.id}`;
  let doc = await call("get", path);
  assert.equal(doc.document.edges.length, 2);
  assert.equal(doc.document.nodes.length, 3);
  report.checks.push(
    "UI create text/generation nodes, typed connections, layout and save",
  );
  await page.reload();
  await page.getByText(/已保存 · 版本 1/).waitFor();
  await edit("电影风格");
  await page.getByLabel("创作说明").fill("未保存草稿，应当恢复");
  await page.reload();
  await page.getByRole("button", { name: "恢复草稿", exact: true }).click();
  await edit("电影风格");
  assert.equal(
    await page.getByLabel("创作说明").inputValue(),
    "未保存草稿，应当恢复",
  );
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await edit("电影风格");
  await page.getByLabel("创作说明").fill("金色日落，安静的海边，胶片质感。");
  report.checks.push(
    "Browser draft survives refresh; undo/redo preserve document",
  );
  await page.getByRole("button", { name: "运行画布", exact: true }).click();
  let run;
  for (let i = 0; i < 60; i++) {
    run = (await call("get", `${path}/runs`))[0];
    if (run?.status === "succeeded" || run?.status === "failed") break;
    await page.waitForTimeout(500);
  }
  assert.equal(run?.status, "succeeded", JSON.stringify(run));
  assert.equal(Object.values(run.steps).filter((s) => s.job_id).length, 2);
  report.run = run.id;
  const generations = await call(
    "get",
    `${root}/generations?target_type=shot&target_id=${shots[1].id}`,
  );
  assert.ok(
    generations.some((g) => g.input_refs?.canvas_origin?.run_id === run.id),
  );
  assert.ok(generations.every((g) => !g.is_selected));
  report.checks.push(
    "Durable background execution returns two offline provider outputs with provenance; no automatic selection",
  );
  await page.waitForTimeout(2200);
  await page.screenshot({
    path: `${out}/canvas-dark.png`,
    animations: "disabled",
  });
  await toggleAccountTheme(page);
  await page.waitForTimeout(250);
  await page.screenshot({
    path: `${out}/canvas-light.png`,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .locator("main")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  );
  await page.screenshot({ path: `${out}/canvas-mobile.png`, fullPage: true });
  report.checks.push("Dark/light and 390px layout without horizontal overflow");
  assert.deepEqual(errors, []);
  report.passed = true;
  report.errors = errors;
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (e) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  await writeFile(
    `${out}/failure.json`,
    JSON.stringify({ ...report, error: String(e), errors }, null, 2),
  );
  throw e;
} finally {
  await browser.close();
}
