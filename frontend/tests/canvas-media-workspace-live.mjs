import { toggleAccountTheme } from "./helpers/account.mjs";
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR,
  media = process.env.TEST_MEDIA_DIR;
assert.ok(base && out && media);
await mkdir(out, { recursive: true });
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1560, height: 1080 },
  }),
  page = await context.newPage(),
  errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
let fixture;
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
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  try {
    fixture = JSON.parse(await readFile(out + "/fixture.json", "utf8"));
  } catch {}
  if (!fixture) {
    const project = await call("post", "/projects", {
        code: "CANVAS-MEDIA-" + Date.now(),
        name: "画布体验验收 · 媒体创作",
      }),
      root = "/projects/" + project.id;
    const script = await call("post", root + "/scripts", {
      title: "光影与动作",
    });
    await call("post", root + `/scripts/${script.id}/apply-scenes`, {
      scenes: [
        {
          title: "场景参考",
          shots: [{ title: "竹林 · 动作参考" }, { title: "光影 · 氛围参考" }],
        },
      ],
    });
    const shots = await call("get", root + "/shots");
    const upload = async (file, mime, shot) => {
      const r = await context.request.post(
        base + "/api/v1" + root + `/media/shot/${shot}/upload`,
        {
          headers,
          multipart: {
            file: {
              name: file,
              mimeType: mime,
              buffer: await readFile(media + "/" + file),
            },
          },
        },
      );
      assert.ok(r.ok(), await r.text());
      return r.json();
    };
    const a = await upload("reference-a.jpg", "image/jpeg", shots[0].id),
      b = await upload("reference-b.jpg", "image/jpeg", shots[0].id),
      video = await upload("reference-video.mp4", "video/mp4", shots[1].id),
      canvas = await call("post", root + "/canvases", {
        name: "光影试验室 · 从参考到镜头",
      });
    fixture = {
      project: project.id,
      shots: shots.map((s) => s.id),
      a,
      b,
      video,
      canvas: canvas.id,
    };
    await writeFile(out + "/fixture.json", JSON.stringify(fixture, null, 2));
  }
  if (!fixture.extraVideo) {
    const r = await context.request.post(
      base +
        "/api/v1/projects/" +
        fixture.project +
        "/media/shot/" +
        fixture.shots[0] +
        "/upload",
      {
        headers,
        multipart: {
          file: {
            name: "reference-video.mp4",
            mimeType: "video/mp4",
            buffer: await readFile(media + "/reference-video.mp4"),
          },
        },
      },
    );
    assert.ok(r.ok(), await r.text());
    fixture.extraVideo = await r.json();
    await writeFile(out + "/fixture.json", JSON.stringify(fixture, null, 2));
  }
  const root = "/projects/" + fixture.project,
    path = root + "/canvases/" + fixture.canvas;
  assert.equal((await call("get", root)).name, "画布体验验收 · 媒体创作");
  const make = (id, kind, label, x, y, width = 350, height = 280) => ({
    id,
    position: { x, y },
    width,
    height,
    data: {
      kind,
      label,
      text: "",
      target_type: "shot",
      target_id: null,
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
  const note = make("note", "text", "01 / 创作方向", 40, 120, 300, 280);
  note.data.text =
    "光穿过竹叶，人物在明暗之间移动。\n\n参考动作的方向与画面的层次，尝试一组连贯镜头。";
  const a = make("image-a", "shot", "02 / 动作与构图", 430, 100, 400, 320);
  a.data.target_id = fixture.shots[0];
  a.data.generation_id = fixture.a.id;
  const b = make("image-b", "shot", "03 / 光线与氛围", 430, 480, 400, 320);
  b.data.target_id = fixture.shots[0];
  b.data.generation_id = fixture.b.id;
  const video = make("video", "shot", "04 / 动态参考", 950, 100, 400, 320);
  video.data.target_id = fixture.shots[1];
  video.data.generation_id = fixture.video.id;
  const gen = make("generate", "generate", "05 / 下一镜", 950, 510, 350, 280);
  gen.data.target_id = fixture.shots[0];
  gen.data.text = "沿着竹林的光线，拍摄下一镜。";
  const current = await call("get", path);
  await call("put", path, {
    name: current.name,
    revision: current.revision,
    document: {
      nodes: [note, a, b, video, gen],
      edges: [
        {
          id: "prompt-link",
          source: "note",
          target: "generate",
          sourceHandle: "text",
          targetHandle: "prompt",
        },
      ],
      viewport: { x: 10, y: 0, zoom: 0.9 },
    },
  });
  await page.goto(base + root + "/canvas?canvas=" + fixture.canvas);
  await page.getByLabel("画布名称").waitFor();
  await page.locator(".canvas-card img").first().waitFor();
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".canvas-card img")].every(
      (i) => i.complete && i.naturalWidth,
    ),
  );
  const surface = await page.locator(".canvas-surface").boundingBox();
  assert.ok(
    surface.width > 1400 && surface.height > 850,
    JSON.stringify(surface),
  );
  assert.equal(await page.locator(".canvas-inspector").isVisible(), false);
  const vid = page.locator(".canvas-card video");
  await vid.evaluate((v) => {
    v.muted = true;
    return v.play();
  });
  await page.waitForFunction(
    () => document.querySelector(".canvas-card video")?.currentTime > 0.3,
  );
  await vid.evaluate((v) => v.pause());
  await page.screenshot({ path: out + "/canvas-media-dark.png" });
  const card = (id) => page.locator(`.react-flow__node[data-id="${id}"]`);
  await card("image-a").locator(".canvas-node-title").click();
  await page
    .getByRole("button", { name: "放大 02 / 动作与构图", exact: true })
    .click();
  await page.getByRole("dialog").waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "版本与编辑", exact: true }).click();
  await page.getByRole("dialog").waitFor();
  await page
    .locator(`[data-generation-id="${fixture.b.id}"]`)
    .getByRole("button", { name: "固定到此节点", exact: true })
    .click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  const save = async () => {
    // Finish the 250ms focus animation before persisting the final viewport.
    await page.waitForTimeout(350);
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await page.getByText(/已保存 · 版本/).waitFor();
    return call("get", path);
  };
  let doc = await save();
  assert.equal(
    doc.document.nodes.find((n) => n.id === "image-a").data.generation_id,
    fixture.b.id,
  );
  assert.ok(
    (
      await call(
        "get",
        root + `/generations?target_type=shot&target_id=${fixture.shots[0]}`,
      )
    ).every((g) => !g.is_selected),
  );
  await card("image-a").locator(".canvas-node-title").click();
  await page.getByRole("button", { name: "参考创作", exact: true }).click();
  await page
    .getByLabel("节点提示词", { exact: true })
    .fill("以这幅参考图的光影重新构图。");
  doc = await save();
  const branch = doc.document.nodes.find(
    (n) => !["note", "image-a", "image-b", "video", "generate"].includes(n.id),
  );
  assert.ok(branch);
  assert.equal(branch.data.text, "以这幅参考图的光影重新构图。");
  assert.ok(
    doc.document.edges.some(
      (e) =>
        e.source === "image-a" &&
        e.target === branch.id &&
        e.targetHandle === "reference",
    ),
  );
  const referenceEdge = doc.document.edges.find(
    (e) => e.source === "image-a" && e.target === branch.id,
  );
  await page
    .locator(
      `[data-testid="rf__edge-${referenceEdge.id}"] .react-flow__edge-path`,
    )
    .waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "生成这一节点", exact: true })
      .isDisabled(),
    true,
  );
  await page.screenshot({ path: out + "/canvas-reference-composer.png" });
  await card("image-a").locator(".canvas-node-title").click();
  await page.getByRole("button", { name: "版本与编辑", exact: true }).click();
  assert.equal(
    await page
      .locator(`[data-generation-id="${fixture.extraVideo.id}"]`)
      .getByRole("button", { name: "固定到此节点", exact: true })
      .isDisabled(),
    true,
  );
  await page.keyboard.press("Escape");
  await card(branch.id).locator(".canvas-node-title").click();
  // Remove only the newly-created test branch; the offline provider deliberately has no image-reference capability.
  await page.getByRole("button", { name: "删除选中项", exact: true }).click();
  doc = await save();
  assert.equal(doc.document.nodes.length, 5);
  await page.getByRole("button", { name: "整理布局", exact: true }).click();
  await save();
  await card("note").locator(".canvas-node-title").click();
  await page
    .getByLabel("节点正文", { exact: true })
    .fill("真实画布内编辑：光影、方向、运动。");
  await save();
  await card("note").locator(".canvas-node-title").click();
  await page.keyboard.press("Control+d");
  doc = await save();
  assert.equal(doc.document.nodes.length, 6);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  doc = await save();
  assert.equal(doc.document.nodes.length, 5);
  await page.getByRole("button", { name: "项目素材", exact: true }).click();
  await page.getByRole("button", { name: "镜头作品", exact: true }).click();
  await page.getByRole("button", { name: /光影 · 氛围参考/ }).click();
  await page.getByRole("button", { name: "视频", exact: true }).click();
  await page
    .locator(`[data-generation-id="${fixture.video.id}"]`)
    .getByRole("button", { name: "放入画布", exact: true })
    .click();
  doc = await save();
  assert.equal(doc.document.nodes.length, 6);
  const added = doc.document.nodes.find(
    (n) => !["note", "image-a", "image-b", "video", "generate"].includes(n.id),
  );
  assert.equal(added.data.generation_id, fixture.video.id);
  await page.getByRole("button", { name: "删除选中项", exact: true }).click();
  await save();
  await page.reload();
  await page.getByLabel("画布名称").waitFor();
  doc = await call("get", path);
  assert.equal(
    doc.document.nodes.find((n) => n.id === "image-a").data.generation_id,
    fixture.b.id,
  );
  assert.equal(
    doc.document.nodes.find((n) => n.id === "note").data.text,
    "真实画布内编辑：光影、方向、运动。",
  );
  // Imported media now receives focus; return to the whole canvas before selecting an earlier node.
  await page.locator(".react-flow__controls-fitview").click();
  await page.waitForTimeout(350);
  await card("generate").locator(".canvas-node-title").click();
  const started = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/runs"),
  );
  await page.getByRole("button", { name: "生成这一节点", exact: true }).click();
  const startResponse = await started;
  assert.ok(startResponse.ok(), await startResponse.text());
  const createdRun = await startResponse.json();
  assert.equal(createdRun.target_node_id, "generate");
  let run;
  for (let i = 0; i < 60; i++) {
    run = (await call("get", path + "/runs")).find(
      (r) => r.id === createdRun.id,
    );
    if (["succeeded", "failed"].includes(run?.status)) break;
    await page.waitForTimeout(500);
  }
  assert.equal(run.status, "succeeded", JSON.stringify(run));
  assert.equal(run.target_node_id, "generate");
  assert.deepEqual(Object.keys(run.steps).sort(), ["generate", "note"]);
  await card("generate").locator("img").waitFor();
  await toggleAccountTheme(page);
  await page.waitForFunction(() =>
    document.querySelector(".react-flow")?.classList.contains("light"),
  );
  await page.screenshot({ path: out + "/canvas-media-light.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.locator("main").evaluate((e) => e.scrollWidth <= e.clientWidth),
  );
  await page.getByRole("button", { name: "展开属性面板", exact: true }).click();
  await page.screenshot({ path: out + "/canvas-media-mobile.png" });
  assert.ok(
    await page
      .locator(".canvas-inspector")
      .evaluate((e) => e.getBoundingClientRect().right <= innerWidth),
  );
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify(
      {
        passed: true,
        fixture: fixture.project,
        canvas: fixture.canvas,
        run: run.id,
        surface,
        checks: [
          "full workspace and closed default inspector",
          "real uploaded images and playable MP4",
          "image lightbox",
          "fixed version does not adopt project result",
          "image reference branch and inline prompt",
          "direct text editing",
          "keyboard duplication and undo persist",
          "material picker inserts fixed video reference",
          "reload preserves content and version",
          "real targeted offline run with upstream only",
          "light theme and 390px layout",
        ],
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Canvas media workspace acceptance passed");
} catch (e) {
  await page.screenshot({ path: out + "/failure.png", fullPage: true });
  await writeFile(
    out + "/failure.json",
    JSON.stringify(
      { error: String(e), errors, project: fixture?.project },
      null,
      2,
    ),
  );
  throw e;
} finally {
  await browser.close();
}
