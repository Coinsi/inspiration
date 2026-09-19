import { toggleAccountTheme } from "./helpers/account.mjs";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
await mkdir(out, { recursive: true });
const picture = await readFile("frontend/public/images/studio-scenes.webp");
const video = await readFile(process.env.TEST_VIDEO);
const calls = [];
const upstream = createServer(async (req, res) => {
  let body = "";
  for await (const chunk of req) body += chunk.toString();
  calls.push({ path: req.url, method: req.method, body });
  res.setHeader("Content-Type", "application/json");
  if (req.headers.authorization !== "Bearer qa-local-key") {
    res.statusCode = 401;
    return res.end("{}");
  }
  if (req.url === "/v1/models")
    return res.end(
      JSON.stringify({
        data: [
          { id: "qa-image-a" },
          { id: "qa-image-b" },
          { id: "qa-video" },
          { id: "qa-text" },
        ],
      }),
    );
  if (req.url === "/v1/images/generations")
    return res.end(
      JSON.stringify({ data: [{ b64_json: picture.toString("base64") }] }),
    );
  if (req.url === "/v1/videos" && req.method === "POST")
    return res.end(JSON.stringify({ id: "qa-video-task" }));
  if (req.url === "/v1/videos/qa-video-task")
    return res.end(JSON.stringify({ status: "completed" }));
  if (req.url === "/v1/videos/qa-video-task/content") {
    res.setHeader("Content-Type", "video/mp4");
    return res.end(video);
  }
  res.statusCode = 404;
  res.end("{}");
});
await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
const endpoint = `http://127.0.0.1:${upstream.address().port}/v1`;
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1512, height: 1080 },
  }),
  page = await context.newPage(),
  errors = [];
page.on("pageerror", (e) => errors.push(e.message));
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
  const p = await call("post", "/projects", {
      name: "渠道模型验收 · 多模型创作",
    }),
    root = `/projects/${p.id}`;
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(`${base}${root}/settings`);
  await page.getByRole("button", { name: "添加渠道", exact: true }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("渠道名称", { exact: true }).fill("创作测试服务");
  await dialog.getByLabel("服务地址", { exact: false }).fill(endpoint);
  await dialog.getByLabel("API Key", { exact: true }).fill("qa-local-key");
  await dialog.getByRole("button", { name: "保存渠道", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  await page.getByRole("button", { name: "读取模型目录", exact: true }).click();
  await page.getByText("目录已读取", { exact: false }).waitFor();
  const addModel = async (name, code, protocol, modality, params = {}) => {
    await page.getByRole("button", { name: "添加模型", exact: true }).click();
    const d = page.getByRole("dialog");
    await d.getByLabel("显示名称", { exact: true }).fill(name);
    await d.getByLabel("模型编号", { exact: true }).fill(code);
    await d.getByLabel("接口协议", { exact: true }).selectOption(protocol);
    await d.getByLabel("模型类型", { exact: true }).selectOption(modality);
    for (const [key, value] of Object.entries(params))
      await d.getByLabel(key, { exact: true }).fill(value);
    await d.getByRole("button", { name: "保存模型", exact: true }).click();
    await d.waitFor({ state: "detached" });
  };
  await addModel("分镜草图", "qa-image-a", "gpt_image", "image", {
    画面尺寸: "1024x1024, 1536x1024",
    图片质量: "low, high",
  });
  await addModel("角色定稿", "qa-image-b", "gpt_image", "image");
  await addModel("镜头视频", "qa-video", "openai_video", "video", {
    "时长（秒）": "4, 8",
    画面尺寸: "1280x720",
  });
  await addModel("创作助手", "qa-text", "cloud_llm", "text");
  await page
    .getByRole("button", { name: "编辑模型 创作助手", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("设为项目默认文字模型", { exact: false }).check();
  await dialog.getByRole("button", { name: "保存模型", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  const catalog = await call("get", root + "/model-channels"),
    channel = catalog.channels[0];
  assert.equal(channel.models.length, 4);
  assert.ok(!JSON.stringify(catalog).includes("qa-local-key"));
  assert.ok(channel.models.find((m) => m.model === "qa-text").is_default);
  await page.getByRole("button", { name: "编辑渠道", exact: true }).click();
  dialog = page.getByRole("dialog");
  assert.equal(
    await dialog.getByLabel("API Key", { exact: true }).inputValue(),
    "",
  );
  await dialog
    .getByLabel("渠道名称", { exact: true })
    .fill("创作测试服务 · 已保存");
  await dialog.getByRole("button", { name: "保存渠道", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  await page.getByRole("button", { name: "读取模型目录", exact: true }).click();
  await page.getByText("目录已读取", { exact: false }).waitFor();
  await page
    .getByRole("button", { name: "读取模型目录", exact: true })
    .waitFor();
  await page.screenshot({ path: `${out}/channels-dark.png`, fullPage: true });
  const script = await call("post", `${root}/scripts`, {
    title: "模型选择验收",
  });
  await call("post", `${root}/scripts/${script.id}/apply-scenes`, {
    scenes: [{ title: "海边", shots: [{ title: "开场镜头" }] }],
  });
  const shot = (await call("get", root + "/shots"))[0];
  const imageModel = channel.models.find((m) => m.model === "qa-image-a"),
    videoModel = channel.models.find((m) => m.model === "qa-video");
  await page.goto(`${base}${root}/shots?shot=${shot.id}`);
  await page.getByRole("tab", { name: "生成出图", exact: true }).click();
  await page
    .getByLabel("生成供应商", { exact: true })
    .selectOption(imageModel.provider_name);
  await page.getByLabel("生成数量", { exact: true }).selectOption("1");
  await page.getByLabel("画面尺寸", { exact: true }).selectOption("1024x1024");
  await page.getByLabel("图片质量", { exact: true }).selectOption("high");
  await page.getByLabel("本次生成指令", { exact: true }).fill("海边镜头测试");
  const generate = async () => {
    const result = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/shots/${shot.id}/generate`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "生成", exact: true }).click();
    const r = await result;
    assert.ok(r.ok(), await r.text());
    const job = await r.json();
    for (let i = 0; i < 80; i++) {
      const current = await call("get", `${root}/jobs/${job.id}`);
      if (current.status === "succeeded") return job;
      assert.notEqual(current.status, "failed", current.error);
      await page.waitForTimeout(250);
    }
    throw new Error("Job timeout");
  };
  const imageJob = await generate();
  const imageCall = calls.find((r) => r.path === "/v1/images/generations");
  assert.ok(imageCall);
  const imageBody = JSON.parse(imageCall.body);
  assert.equal(imageBody.model, "qa-image-a");
  assert.equal(imageBody.size, "1024x1024");
  assert.equal(imageBody.quality, "high");
  await page
    .getByLabel("生成供应商", { exact: true })
    .selectOption(videoModel.provider_name);
  await page.getByLabel("时长（秒）", { exact: true }).selectOption("4");
  await page.getByLabel("画面尺寸", { exact: true }).selectOption("1280x720");
  assert.equal(
    await page.getByLabel("生成类型", { exact: true }).inputValue(),
    "video",
  );
  assert.equal(
    await page.getByLabel("生成数量", { exact: true }).inputValue(),
    "1",
  );
  const videoJob = await generate();
  assert.ok(
    calls.some((r) => r.path === "/v1/videos" && r.body.includes("qa-video")),
  );
  await page.goto(`${base}${root}/tasks?job=${videoJob.id}`);
  await page.getByLabel("任务视频结果", { exact: true }).waitFor();
  await page.locator(".task-output video").evaluate(async (v) => {
    v.muted = true;
    await v.play();
    await new Promise((resolve) => v.requestVideoFrameCallback(resolve));
    v.pause();
  });
  await page.screenshot({ path: `${out}/video-result.png`, fullPage: true });
  const canvas = await call("post", root + "/canvases", {
    name: "渠道模型画布",
  });
  await call("put", `${root}/canvases/${canvas.id}`, {
    name: canvas.name,
    revision: canvas.revision,
    document: {
      nodes: [
        {
          id: "generate",
          position: { x: 100, y: 100 },
          width: 350,
          height: 280,
          data: {
            kind: "generate",
            label: "模型节点",
            text: "海边镜头",
            target_type: "shot",
            target_id: shot.id,
            generation_id: null,
            generate: {
              provider: "mock",
              request_type: "image",
              count: 3,
              params: {},
              provider_params: {},
              use_references: false,
            },
          },
        },
      ],
      edges: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    },
  });
  await page.goto(`${base}${root}/canvas?canvas=${canvas.id}`);
  await page
    .locator('.react-flow__node[data-id="generate"]')
    .getByText("模型节点", { exact: true })
    .click();
  await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
  await page
    .getByLabel("供应商", { exact: false })
    .selectOption(videoModel.provider_name);
  await page.getByLabel("画面尺寸", { exact: false }).selectOption("1280x720");
  await page.getByLabel("时长（秒）", { exact: false }).selectOption("4");
  assert.equal(
    await page.getByLabel("候选数量", { exact: false }).inputValue(),
    "1",
  );
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByText(/已保存 · 版本/).waitFor();
  const savedCanvas = await call("get", `${root}/canvases/${canvas.id}`);
  assert.equal(
    savedCanvas.document.nodes[0].data.generate.provider,
    videoModel.provider_name,
  );
  assert.equal(
    savedCanvas.document.nodes[0].data.generate.provider_params.duration,
    4,
  );
  await page.goto(`${base}${root}/settings`);
  await page.getByRole("button", { name: "编辑渠道", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("启用渠道", { exact: true }).uncheck();
  await dialog.getByRole("button", { name: "保存渠道", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  await page.getByRole("button", { name: "编辑渠道", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("渠道名称", { exact: true }).fill("并发编辑中的草稿");
  const fresh = (await call("get", root + "/model-channels")).channels[0];
  await call("put", `${root}/model-channels/${fresh.id}`, {
    name: "外部更新",
    endpoint: fresh.endpoint,
    enabled: fresh.enabled,
    revision: fresh.revision,
  });
  await dialog.getByRole("button", { name: "保存渠道", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  assert.equal(
    await dialog.getByLabel("渠道名称", { exact: true }).inputValue(),
    "并发编辑中的草稿",
  );
  page.once("dialog", (d) => d.accept());
  await dialog.getByRole("button", { name: "关闭编辑", exact: true }).click();
  await page.getByRole("button", { name: "编辑渠道", exact: true }).click();
  dialog = page.getByRole("dialog");
  assert.equal(
    await dialog.getByLabel("渠道名称", { exact: true }).inputValue(),
    "外部更新",
  );
  await dialog.getByRole("button", { name: "关闭编辑", exact: true }).click();
  const options = await call("get", root + "/providers");
  assert.ok(options.every((p) => !p.enabled));
  await page.getByRole("button", { name: "编辑渠道", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("启用渠道", { exact: true }).check();
  await dialog.getByRole("button", { name: "保存渠道", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  await toggleAccountTheme(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: `${out}/channels-mobile.png`, fullPage: true });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  );
  await page
    .getByRole("button", { name: "编辑模型 分镜草图", exact: true })
    .click();
  await page.screenshot({ path: `${out}/model-mobile.png`, fullPage: true });
  await page.getByRole("button", { name: "关闭编辑", exact: true }).click();
  assert.deepEqual(errors, []);
  const report = {
    passed: true,
    project: p.id,
    image_job: imageJob.id,
    video_job: videoJob.id,
    checks: [
      "Real UI channel creation and shared-key preservation",
      "Directory loading and four independently configured models",
      "Default text selection and secret-free response",
      "Image model, size and quality delivered to real local HTTP upstream",
      "Video submit/poll/download returned playable MP4",
      "Canvas model selection resets type/count and persists supported parameters",
      "Concurrent edit rejected with draft retained; reopen loads current data",
      "Channel disable affects every model; re-enable restores options",
      "Dark/light and mobile editor; no console errors",
    ],
    errors,
  };
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} catch (e) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  throw e;
} finally {
  await browser.close();
  upstream.close();
}
