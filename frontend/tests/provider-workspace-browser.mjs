// The same provider workspace in platform, project and personal scopes.
import assert from "node:assert/strict";
import { build } from "esbuild";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const out = resolve("../test-results/provider-workspace");
await mkdir(out, { recursive: true });
const css = (await readdir(resolve("dist/assets"))).find(
  (n) => n.startsWith("index-") && n.endsWith(".css"),
);
await writeFile(
  resolve(out, "production.css"),
  await readFile(resolve("dist/assets", css)),
);
await build({
  stdin: {
    contents: `
import React from 'react'; import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query'; import {BrowserRouter,Route,Routes} from 'react-router-dom';
import {AuthProvider} from './src/lib/auth'; import {I18nProvider} from './src/lib/i18n'; import {ConfirmProvider} from './src/components/ui/confirm';
import ModelAdmin from './src/pages/ModelAdmin';
import Settings from './src/pages/Settings';
const q=new QueryClient({defaultOptions:{queries:{retry:false}}});
createRoot(document.getElementById('root')).render(
  <I18nProvider><QueryClientProvider client={q}><AuthProvider><BrowserRouter><ConfirmProvider>
    <Routes>
      <Route path="/admin/models" element={<ModelAdmin/>}/>
      <Route path="/projects/:projectId/settings" element={<Settings/>}/>
    </Routes>
  </ConfirmProvider></BrowserRouter></AuthProvider></QueryClientProvider></I18nProvider>
);
`,
    resolveDir: process.cwd(),
    loader: "tsx",
  },
  bundle: true,
  loader: { ".svg": "dataurl", ".png": "dataurl" },
  outfile: resolve(out, "fixture.js"),
  format: "esm",
  jsx: "automatic",
  alias: { "@": resolve("src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
await writeFile(
  resolve(out, "index.html"),
  '<!doctype html><html class="light"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/production.css"><link rel="stylesheet" href="/fixture.css"><div id="root"></div><script type="module" src="/fixture.js"></script></html>',
);
const server = createServer(async (req, res) => {
  const name =
    {
      "/fixture.js": "fixture.js",
      "/fixture.css": "fixture.css",
      "/production.css": "production.css",
    }[req.url] || "index.html";
  res.setHeader(
    "Content-Type",
    name.endsWith(".js")
      ? "text/javascript"
      : name.endsWith(".css")
        ? "text/css"
        : "text/html",
  );
  res.end(await readFile(resolve(out, name)));
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const browser = await chromium.launch({ headless: true, channel: "chrome" });

const errors = [];
let templates = [
  {
    id: "openai",
    name: "OpenAI",
    icon: "openai",
    endpoint: "https://api.openai.com/v1",
    key_url: "https://platform.openai.com/api-keys",
    note: "使用供应商 API Key。",
    enabled: true,
    revision: 0,
    routes: [
      { modality: "text", protocol: "cloud_llm", sdk_provider: "openai" },
      { modality: "image", protocol: "gpt_image", sdk_provider: "openai" },
      { modality: "video", protocol: "openai_video", sdk_provider: "openai" },
    ],
  },
];
if (process.env.PROVIDER_FIXTURE_CATALOG) {
  templates = JSON.parse(
    await readFile(process.env.PROVIDER_FIXTURE_CATALOG, "utf8"),
  );
}
const picture = await readFile(
  resolve("public/media/home-reference-updream.jpg"),
);
let attachment;
let generations = 0;
let testMedia = "image";
let personal = false;
let personalChannels = [];
let channels = [],
  models = [
    {
      id: "default",
      name: "文字模型",
      modality: "text",
      enabled: true,
      is_default: true,
      available_routes: 1,
      routes: [],
    },
  ];
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() =>
    localStorage.setItem("inspiration_token", "fixture-only"),
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", ""),
      method = route.request().method();
    const ok = (json) => route.fulfill({ json });
    if (path === "/projects/qa/attachments/uploads") {
      const input = route.request().postDataJSON();
      attachment = {
        id: "reference",
        filename: input.filename,
        size_bytes: input.size_bytes,
        kind: "image",
        status: "uploading",
        blob_hash: null,
        chunk_size: 1024 * 1024,
        uploaded_bytes: 0,
        fingerprint: input.fingerprint,
      };
      return ok(attachment);
    }
    if (path === "/projects/qa/attachments/reference/chunk") {
      attachment.uploaded_bytes = attachment.size_bytes;
      return ok(attachment);
    }
    if (path === "/projects/qa/attachments/reference/complete") {
      attachment.status = "ready";
      attachment.blob_hash = "reference";
      return ok(attachment);
    }
    if (path === "/projects/qa/attachments/reference") return ok(attachment);
    if (path === "/projects") return ok([{ id: "qa", name: "测试项目 A" }]);
    if (path === "/projects/qa") return ok({ id: "qa", name: "测试项目 A" });
    const testJob = {
      id: "test-" + testMedia,
      target_id: "creation-" + testMedia,
      status: "running",
    };
    if (path === "/projects/qa/workbench/generate") {
      const payload = route.request().postDataJSON();
      assert.equal(
        payload.provider,
        (personal ? "personal_fixture_" : "fixture-") + testMedia,
      );
      assert.equal(payload.request_type, testMedia);
      assert.equal(payload.prompt_override, "窗边的绿植");
      generations++;
      if (testMedia === "image" && !personal)
        assert.deepEqual(payload.references, [
          { attachment_id: "reference", role: "reference" },
        ]);
      return ok(testJob);
    }
    if (path === "/projects/qa/jobs/" + testJob.id)
      return ok({ ...testJob, status: "succeeded" });
    if (path === "/projects/qa/generations")
      return ok([
        {
          id: "output",
          job_id: testJob.id,
          output_type: testMedia,
          output_blob_hash: "fixture",
        },
      ]);
    if (path === "/projects/qa/blobs/fixture" && testMedia === "video")
      return route.fulfill({
        path: resolve("public/media/home-reference-updream.mp4"),
        contentType: "video/mp4",
      });
    if (
      path === "/projects/qa/blobs/fixture" ||
      path === "/projects/qa/blobs/reference"
    )
      return route.fulfill({ body: picture, contentType: "image/jpeg" });
    if (path === "/me")
      return ok({
        user: {
          id: personal ? "artist" : "admin",
          is_platform_admin: !personal,
        },
        memberships: [
          { project_id: "qa", role: personal ? "artist" : "admin" },
        ],
        projects: [],
      });
    if (path === "/admin/models/creative-models/clear-default") {
      models[0].is_default = false;
      return ok({});
    }
    if (path === "/admin/models/creative-models") return ok(models);
    if (
      path === "/admin/model-provider-templates" ||
      path === "/model-provider-templates"
    )
      return ok(templates);
    if (
      path.startsWith("/admin/model-provider-templates/") &&
      method === "PUT"
    ) {
      const b = route.request().postDataJSON();
      templates.push({ ...b, id: path.split("/").at(-1), revision: 1 });
      return ok(templates.at(-1));
    }
    if (path === "/admin/models/model-channels/check")
      return ok({ message: "连接通过；尚未验证生成。" });
    if (path === "/admin/models/model-channels" && method === "POST") {
      const b = route.request().postDataJSON();
      channels.push({
        ...b,
        token: undefined,
        id: "connection",
        enabled: true,
        revision: 1,
        has_token: true,
        models: [],
      });
      return ok({ id: "connection" });
    }
    if (
      [
        "/admin/models/model-channels",
        "/projects/qa/model-channels",
        "/me/model-channels",
      ].includes(path)
    )
      return ok({
        channels: path === "/me/model-channels" ? personalChannels : channels,
        legacy_count: 0,
        protocols: {
          cloud_llm: {
            name: "文字兼容协议",
            types: ["text"],
            sdk_providers: { text: ["openai", "anthropic"] },
          },
          gpt_image: { name: "OpenAI Images", types: ["image"] },
          openai_video: { name: "OpenAI Videos", types: ["video"] },
        },
      });
    if (path.endsWith("/discover"))
      return ok({
        models: ["my-text-model", "studio/artist", "studio/vision"],
        details: [
          { id: "my-text-model", name: "My Text Model" },
          { id: "studio/artist", name: "Studio Artist" },
          { id: "studio/vision", name: "Studio Vision" },
        ],
        message: "已读取模型列表",
      });
    if (path.endsWith("/models") && method === "POST") {
      const b = route.request().postDataJSON();
      channels[0].models.push({ ...b, id: "profile", revision: 1 });
      return ok(channels[0].models[0]);
    }
    return route.fulfill({
      status: 404,
      json: { error: { message: "Unexpected fixture " + path } },
    });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/admin/models`);
  await page.getByRole("button", { name: "取消默认", exact: true }).click();
  await page.getByText("已取消默认选择", { exact: false }).waitFor();
  assert.equal(models[0].is_default, false);
  await page.getByRole("button", { name: "全局模型服务", exact: true }).click();
  await page.getByRole("button", { name: "OpenAI", exact: true }).click();
  await page.getByLabel("API Key", { exact: true }).fill("fixture-secret");
  await page
    .getByRole("button", { name: "接入自己的服务", exact: true })
    .click();
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  assert.equal(
    await page.getByLabel("API Key", { exact: true }).inputValue(),
    "fixture-secret",
  );
  await page.getByRole("button", { name: "保存连接", exact: true }).click();
  await page.getByRole("button", { name: "读取模型列表", exact: true }).click();
  const picker = page.getByRole("combobox", { name: "模型 ID", exact: true });
  await page.getByRole("listbox", { name: "可选模型" }).waitFor();
  await page.screenshot({ path: resolve(out, "model-menu.png") });
  await picker.fill("my-text");
  await page.getByRole("option", { name: "my-text-model" }).waitFor();
  await picker.press("ArrowDown");
  await picker.press("Enter");
  assert.equal(await picker.inputValue(), "my-text-model");
  await picker.fill("custom-model");
  await page.getByText("没有匹配项，可直接添加输入的 ID。").waitFor();
  await picker.fill("my-text-model");
  await page
    .getByRole("button", { name: "添加到文字与理解", exact: true })
    .click();
  await page.getByRole("dialog").waitFor();
  await page.screenshot({
    path: resolve(out, "model-editor.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "保存模型", exact: true }).click();
  await page.getByText("已配置 · 编辑能力与参数").waitFor();
  assert.equal(channels[0].models[0].model, "my-text-model");
  const directory = page.locator(".provider-directory");
  await page.getByRole("button", { name: "视频生成", exact: true }).click();
  assert.equal(new URL(page.url()).searchParams.get("purpose"), "video");
  await page.reload();
  await page.getByRole("button", { name: "视频生成", pressed: true }).waitFor();
  assert.equal(
    await directory.getByText("Anthropic", { exact: true }).count(),
    0,
  );
  if (process.env.PROVIDER_FIXTURE_CATALOG) {
    for (const name of [
      "OpenRouter",
      "可灵 / Kling",
      "Vidu / 生数",
      "HeyGen",
      "OpenAI 兼容视频",
    ])
      await directory.getByRole("button", { name, exact: true }).waitFor();
    await directory
      .getByRole("button", { name: "Vidu / 生数", exact: true })
      .click();
    await page.getByRole("heading", { name: "测试视频生成" }).waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "测试生成", exact: true })
        .isDisabled(),
      true,
    );
    await page.screenshot({ path: resolve(out, "video-directory.png") });
  }
  await page.getByRole("button", { name: "图片生成", exact: true }).click();
  assert.equal(
    await directory.getByText("Vidu / 生数", { exact: true }).count(),
    0,
  );
  if (process.env.PROVIDER_FIXTURE_CATALOG) {
    await directory
      .getByRole("button", { name: "WaveSpeed", exact: true })
      .waitFor();
    await directory
      .getByRole("button", { name: "OpenRouter", exact: true })
      .click();
    await page.screenshot({ path: resolve(out, "image-directory.png") });
  }
  await page.getByRole("button", { name: "文字与理解", exact: true }).click();
  await directory.getByRole("button", { name: "OpenAI", exact: true }).click();
  await page.screenshot({
    path: resolve(out, "providers-light.png"),
    fullPage: true,
  });
  await page.evaluate(() => (document.documentElement.className = "dark"));
  await page.locator(".dark .provider-choice[aria-pressed=true]").waitFor();
  await page.screenshot({
    path: resolve(out, "providers-dark.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "新增供应商模板", exact: true })
    .click();
  await page.getByLabel("模板标识", { exact: true }).fill("gateway");
  await page.getByLabel("显示名称", { exact: true }).fill("团队网关");
  await page
    .getByLabel("默认 Base URL", { exact: true })
    .fill("https://gateway.example/v1");
  await page.getByRole("button", { name: "保存模板", exact: true }).click();
  await page.getByRole("button", { name: "团队网关", exact: true }).waitFor();
  await page.reload();
  await page.getByRole("button", { name: "全局模型服务", exact: true }).click();
  await page.getByRole("button", { name: "团队网关", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "团队网关", exact: true }).click();
  await page.screenshot({
    path: resolve(out, "providers-mobile.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
  );
  channels[0].models.push({
    id: "image",
    name: "图片测试模型",
    model: "demo",
    provider_name: "fixture-image",
    modality: "image",
    enabled: true,
    parameters: { size: ["1024x1024", "1536x1024"] },
    max_reference_images: 1,
  });
  await page.reload();
  await page.getByRole("button", { name: "全局模型服务", exact: true }).click();
  await page.getByRole("button", { name: "图片生成", exact: true }).click();
  await page.getByRole("button", { name: "OpenAI", exact: true }).click();
  await page.getByRole("button", { name: "测试项目", exact: true }).click();
  await page.getByRole("option", { name: "测试项目 A" }).click();
  await page.setViewportSize({ width: 1440, height: 900 });
  const input = page.getByLabel("参考图（可选）", { exact: true });
  await input.setInputFiles({
    name: "景观参考.jpg",
    mimeType: "image/jpeg",
    buffer: picture,
  });
  await page.getByText("已就绪", { exact: true }).waitFor();
  await page.getByRole("button", { name: "预览 景观参考.jpg" }).click();
  await page.getByRole("dialog", { name: "景观参考.jpg" }).waitFor();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  const chooseReplacement = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "替换 景观参考.jpg" }).click();
  await (
    await chooseReplacement
  ).setFiles({ name: "替换参考.jpg", mimeType: "image/jpeg", buffer: picture });
  await page.getByText("已就绪", { exact: true }).waitFor();
  await page.getByRole("button", { name: "移除 替换参考.jpg" }).click();
  await input.setInputFiles({
    name: "海岸晨光.jpg",
    mimeType: "image/jpeg",
    buffer: picture,
  });
  await page.getByText("已就绪", { exact: true }).waitFor();
  await page.screenshot({ path: resolve(out, "reference-selected.png") });
  await page.getByLabel("描述想生成的内容").fill("窗边的绿植");
  await page.getByRole("button", { name: "测试生成", exact: true }).click();
  await page.getByAltText("测试生成结果").waitFor();
  await page.getByRole("button", { name: "放大测试图片" }).click();
  await page.getByAltText("放大的测试生成结果").waitFor();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.screenshot({ path: resolve(out, "result-image.png") });
  testMedia = "video";
  channels[0].models.push({
    id: "video",
    name: "视频测试模型",
    model: "demo-video",
    provider_name: "fixture-video",
    modality: "video",
    enabled: true,
    parameters: { duration: [5, 10] },
    max_reference_images: 1,
  });
  await page.reload();
  await page.getByRole("button", { name: "全局模型服务", exact: true }).click();
  await page.getByRole("button", { name: "视频生成", exact: true }).click();
  await page.getByRole("button", { name: "OpenAI", exact: true }).click();
  await page.getByRole("button", { name: "测试项目", exact: true }).click();
  await page.getByRole("option", { name: "测试项目 A" }).click();
  await page.getByRole("button", { name: /添加参考图/ }).waitFor();
  await page.getByRole("button", { name: "时长（秒）", exact: true }).waitFor();
  await page.getByLabel("描述想生成的内容").fill("窗边的绿植");
  await page.getByRole("button", { name: "测试生成", exact: true }).click();
  await page.locator(".provider-test-column video[controls]").waitFor();
  await page.locator(".provider-result video").evaluate(
    (video) =>
      video.readyState >= 2 ||
      new Promise((resolve, reject) => {
        video.addEventListener("loadeddata", resolve, { once: true });
        video.addEventListener("error", reject, { once: true });
      }),
  );
  await page.screenshot({ path: resolve(out, "result-video.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(out, "media-mobile.png"),
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
  );
  assert.equal(generations, 2);
  const settingsUrl = new URL(
    "/projects/qa/settings?section=project&tab=services&purpose=image",
    page.url(),
  );
  await page.goto(settingsUrl.href);
  const settingsNav = page.getByRole("navigation", { name: "设置分类" });
  const navPlacement = () =>
    settingsNav.evaluate((nav) => {
      const [first, second] = [...nav.querySelectorAll("button")].map((item) =>
        item.getBoundingClientRect(),
      );
      return {
        vertical: second.top >= first.bottom,
        left: nav.getBoundingClientRect().left,
        width: nav.clientWidth,
      };
    });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await settingsNav.getByRole("button", { name: /项目资料/ }).click();
    const original = await navPlacement();
    assert.equal(original.vertical, width === 1440);
    await settingsNav.getByRole("button", { name: /模型服务/ }).click();
    await page.getByRole("navigation", { name: "模型用途" }).waitFor();
    assert.deepEqual(await navPlacement(), original);
    await page.reload();
    await page.getByRole("navigation", { name: "模型用途" }).waitFor();
    assert.deepEqual(await navPlacement(), original);
  }
  // A project artist gets their account connections without requiring project management rights.
  personal = true;
  testMedia = "image";
  personalChannels = [
    {
      ...channels[0],
      name: "私人图片服务",
      id: "personal-connection",
      models: [
        {
          ...channels[0].models.find((m) => m.modality === "image"),
          provider_name: "personal_fixture_image",
        },
      ],
    },
  ];
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(
    new URL(
      "/projects/qa/settings?section=channels&tab=personal&purpose=image",
      page.url(),
    ).href,
  );
  const mine = page.getByRole("region", { name: "我的模型服务" });
  await mine.getByRole("button", { name: "私人图片服务", exact: true }).click();
  assert.equal(
    await page
      .getByRole("button", { name: "全局默认模型", exact: true })
      .count(),
    0,
  );
  await mine.getByText("本次生成使用你的个人密钥和供应商额度。").waitFor();
  await mine.getByLabel("描述想生成的内容").fill("窗边的绿植");
  await mine.getByRole("button", { name: "测试生成", exact: true }).click();
  await mine.getByAltText("测试生成结果").waitFor();
  assert.equal(generations, 3);
  await page.screenshot({ path: resolve(out, "personal-image.png") });
  await mine.getByRole("button", { name: "视频生成", exact: true }).click();
  if (process.env.PROVIDER_FIXTURE_CATALOG)
    await mine
      .getByRole("button", { name: "Vidu / 生数", exact: true })
      .waitFor();
  assert.deepEqual(errors, []);
  console.log("Provider workspace browser checks passed");
} finally {
  await browser.close();
  server.close();
}
