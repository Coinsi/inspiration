// API fixtures only: this test never changes user projects or invokes a provider.
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL, fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.APP_URL || "http://127.0.0.1:5173";
const out = process.env.ARTIFACT_DIR || "./test-results/engineering-review";
await mkdir(out, { recursive: true });
const backend = fileURLToPath(new URL("../../backend/", import.meta.url));
execFileSync(
  resolve(
    backend,
    process.platform === "win32"
      ? ".venv/Scripts/python.exe"
      : ".venv/bin/python",
  ),
  [
    "-c",
    "from pathlib import Path; import sys; from app.modules.generation import media_engine as m; p=Path(sys.argv[1]); b=m.mock_image(); (p/'fixture.png').write_bytes(b); (p/'fixture.mp4').write_bytes(m.render([{'blob_hash':'fixture','output_type':'image','duration_ms':1000}],lambda _:b,{'height':720,'aspect_ratio':'16:9'}))",
    resolve(out),
  ],
  { cwd: backend, windowsHide: true },
);
const film = await readFile(`${out}/fixture.mp4`);
const image = await readFile(`${out}/fixture.png`);
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
const errors = [],
  writes = [];
let revision = 0,
  changes = [],
  commits = [],
  attempts = 0;
let nativePayload = { canvas: { width: 800, height: 600 }, layers: [] };
let doc = {
  revision: 0,
  items: [
    {
      id: "clip",
      shot_id: "shot",
      generation_id: "g",
      duration_ms: 1000,
      in_point_ms: 0,
      out_point_ms: 0,
    },
  ],
  audio: [],
  subtitles: [],
  visuals: [],
};
const project = { id: "p", code: "P", name: "工程审阅测试", settings: {} };
const shot = {
  id: "shot",
  title: "晨光中的开场",
  code: "SH1",
  scene_id: "s",
  ordinal: 1,
  selected_generation_id: "g",
  storyboard: { duration_sec: 1 },
};
await context.addInitScript(() => {
  localStorage.setItem("inspiration_token", "fixture");
  localStorage.setItem("inspiration_lang", "zh");
  localStorage.setItem("timeline.work.p.t", '"w"');
});
page.on("pageerror", (e) => errors.push(e.message));
await context.route("**/api/**", async (route) => {
  const req = route.request(),
    url = new URL(req.url()),
    path = url.pathname.replace("/api/v1", "");
  const respond = (body, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  if (path.includes("/blobs/"))
    return route.fulfill({
      contentType: path.includes("preview") ? "video/mp4" : "image/png",
      body: path.includes("preview") ? film : image,
    });
  if (req.method() !== "GET") {
    const body = req.postDataJSON();
    writes.push({ path, body });
    if (path.endsWith("/engineering/changes")) {
      const command = body.commands[0];
      if (command.operation === "restore") {
        const change = {
          id: `c${changes.length + 1}`,
          base_revision: body.base_revision,
          status: "proposed",
          snapshot: structuredClone(
            commits.find((c) => c.revision === command.revision).snapshot,
          ),
          timeline_update: {},
        };
        changes.unshift(change);
        return respond(change);
      }
      if (command.operation === "document.put") {
        const change = {
          id: `c${changes.length + 1}`,
          fingerprint: "b".repeat(64),
          base_revision: body.base_revision,
          status: "proposed",
          committed_revision: null,
          created_at: new Date().toISOString(),
          timeline_update: {},
          snapshot: { native_roots: { native: { id: "native", revision: 1 } } },
          nativePayload: command.payload,
        };
        changes.unshift(change);
        return respond(change);
      }
      assert.ok(
        ["timeline.edit", "timeline.restore", "document.edit"].includes(
          command.operation,
        ),
      );
      const content =
        command.document ||
        (command.operation === "document.edit"
          ? structuredClone(doc)
          : {
              ...commits.find(
                (c) => c.revision === command.engineering_revision,
              ).snapshot.timeline_captures.t.document,
              revision: doc.revision,
            });
      const change = {
        id: `c${changes.length + 1}`,
        fingerprint: "a".repeat(64),
        base_revision: revision,
        status: "proposed",
        committed_revision: null,
        created_at: new Date().toISOString(),
        preview_job_id: null,
        preview: null,
        timeline_update: {
          timeline_id: "t",
          document: structuredClone(content),
        },
      };
      changes.unshift(change);
      return respond(change);
    }
    const change = changes.find((c) => path.includes(`/changes/${c.id}/`));
    if (change && path.endsWith("/preview")) {
      if (change.id === "multi") {
        change.previews = [
          {
            job_id: "second-job",
            output_id: body.output_id,
            status: "succeeded",
            error: null,
            blob_hash: "preview",
            output_type: "video",
          },
        ];
        return respond(change);
      }
      if (change.nativePayload) {
        change.preview_job_id = "native-job";
        change.preview = {
          status: "succeeded",
          error: null,
          blob_hash: "image",
          output_type: "image",
        };
        return respond(change);
      }
      attempts++;
      change.preview_job_id = `job${attempts}`;
      change.preview = {
        status: attempts === 1 ? "failed" : "succeeded",
        error: attempts === 1 ? "模拟预览失败，请重试" : null,
        blob_hash: attempts === 1 ? null : "preview",
      };
      return respond(change);
    }
    if (change && path.endsWith("/accept")) {
      if (change.nativePayload) {
        assert.equal(change.base_revision, revision);
        revision++;
        change.status = "accepted";
        change.committed_revision = revision;
        nativePayload = change.nativePayload;
        commits.unshift({
          revision,
          created_at: new Date().toISOString(),
          snapshot: { ...commits[0]?.snapshot, ...change.snapshot },
        });
        return respond(change);
      }
      assert.equal(change.preview.status, "succeeded");
      revision++;
      change.status = "accepted";
      change.committed_revision = revision;
      doc = {
        ...structuredClone(change.timeline_update.document),
        revision: doc.revision + 1,
      };
      commits.unshift({
        revision,
        created_at: new Date().toISOString(),
        snapshot: {
          timeline_captures: {
            t: {
              document: structuredClone(doc),
              source_revision: doc.revision,
            },
          },
        },
      });
      return respond(change);
    }
    return respond({ error: { message: "Unexpected write blocked" } }, 403);
  }
  if (path === "/me")
    return respond({
      user: { id: "u", username: "test", display_name: "Test" },
      memberships: [],
    });
  if (path === "/projects") return respond([project]);
  if (path === "/projects/p") return respond(project);
  if (path.endsWith("/timelines"))
    return respond([{ id: "t", name: "样片时间线", kind: "main" }]);
  if (path.endsWith("/timelines/t/document")) return respond(doc);
  if (path.endsWith("/shots")) return respond([shot]);
  if (path.endsWith("/works/timeline-links"))
    return respond([
      {
        id: "w",
        title: "科幻短片",
        script_id: null,
        engineering_revision: revision,
      },
    ]);
  if (path.endsWith("/works"))
    return respond([
      {
        id: "w",
        title: "科幻短片",
        script_id: null,
        engineering_revision: revision,
      },
    ]);
  if (path.endsWith("/engineering/documents"))
    return respond({
      preview_executors: [
        { kind: "image.document" },
        { kind: "audio.document" },
      ],
      documents: [
        {
          id: "native",
          title: "角色海报",
          kind: "image.document",
          revisions: [{ revision: 1, created_at: new Date().toISOString() }],
        },
        {
          id: "sync",
          title: "配音同步",
          kind: "sync.document",
          revisions: [{ revision: 1 }],
        },
        {
          id: "picture",
          title: "画面合成",
          kind: "composition.document",
          revisions: [{ revision: 1 }],
        },
        {
          id: "voice",
          title: "配音工程",
          kind: "audio.document",
          revisions: [{ revision: 1 }],
        },
        {
          id: "opaque",
          title: "未来效果",
          kind: "extension.future-effect",
          revisions: [{ revision: 1 }],
        },
        {
          id: "d",
          title: "样片剪辑文档",
          kind: "sequence.timeline",
          source_timeline_id: "t",
          working_revision: 1,
          revisions: [
            {
              revision: 1,
              created_at: new Date().toISOString(),
              content_hash: "a".repeat(64),
            },
          ],
        },
      ],
    });
  if (path.endsWith("/documents/opaque/revisions/1"))
    return respond({
      title: "未来效果",
      kind: "extension.future-effect",
      schema_version: 9,
      adapter_available: false,
      inputs: {},
      payload: { future: "preserved" },
    });
  if (path.endsWith("/documents/sync/revisions/1"))
    return respond({
      title: "配音同步",
      kind: "sync.document",
      schema_version: 1,
      inputs: {
        picture: { target: { id: "picture", revision: 1 } },
        voice: { target: { id: "voice", revision: 1 } },
      },
      payload: {
        constraints: [
          {
            id: "sync-rule",
            name: "片头音画",
            policy: "hard",
            relation: "offset",
            members: [
              { document_input: "picture", instance_id: "a" },
              { document_input: "voice", instance_id: "b" },
            ],
          },
        ],
      },
    });
  if (path.endsWith("/documents/native/revisions/1"))
    return respond({
      title: "角色海报",
      kind: "image.document",
      inputs: {},
      payload: nativePayload,
    });
  if (path.endsWith("/impact"))
    return respond({
      affected_outputs: [{ output: "native" }],
      temporal_impacts: [
        {
          output: "native",
          truncated: false,
          before: {
            status: "conservative",
            intervals: [
              {
                start: { numerator: 1, denominator: 2 },
                end: { numerator: 1, denominator: 1 },
              },
            ],
          },
          after: {
            status: "conservative",
            intervals: [
              {
                start: { numerator: 3, denominator: 2 },
                end: { numerator: 2, denominator: 1 },
              },
            ],
          },
        },
      ],
      sync_reviews: [
        {
          key: "native:2:sync",
          name: "声音起点",
          document_title: "角色海报",
          reasons: ["同步锚点间距超出允许偏差"],
        },
      ],
      dependency_paths: [
        {
          path: [
            { name: "左画面", slot: "source" },
            { name: "主角", slot: "mask" },
          ],
        },
      ],
      instance_input_changes: [
        {
          document_id: "native",
          instance_id: "layer1",
          instance_name: "主角",
          slot: "mask",
          before: { revision: 1 },
          after: { revision: 2 },
        },
      ],
    });
  if (path.endsWith("/package"))
    return respond({
      format: "studio.editable-package@1",
      includes_media_files: false,
      documents: [],
    });
  if (path.endsWith("/documents/d/revisions/1"))
    return respond({
      id: "d",
      revision: 1,
      title: "样片剪辑文档",
      kind: "sequence.timeline",
      inputs: {},
      payload: { editor: doc, aspect_ratio: "16:9" },
    });
  if (path.endsWith("/engineering"))
    return respond({
      revision,
      snapshot: commits[0]?.snapshot || { resources: [] },
    });
  if (path.endsWith("/engineering/changes")) return respond(changes);
  if (path.endsWith("/engineering/history")) return respond(commits);
  if (/\/engineering\/changes\/[^/]+$/.test(path))
    return respond(changes.find((c) => path.endsWith(`/${c.id}`)));
  if (path.endsWith("/generations/g"))
    return respond({
      id: "g",
      output_type: "image",
      output_blob_hash: "image",
      input_refs: {},
    });
  if (path.endsWith("/generations"))
    return respond(
      url.searchParams.get("target_type") === "shot"
        ? [
            {
              id: "g",
              output_type: "image",
              output_blob_hash: "image",
              input_refs: {},
            },
          ]
        : [],
    );
  if (path.endsWith("/history")) return respond({ items: [], has_more: false });
  return respond([]);
});
try {
  await page.goto(`${base}/projects/p/cuts?timeline=t`);
  await page.getByRole("button", { name: "作品审阅", exact: true }).click();
  const modal = page.getByRole("dialog", { name: "作品审阅", exact: true });
  await modal
    .getByRole("button", { name: "将当前剪辑保存为候选", exact: true })
    .click();
  await modal.getByRole("button", { name: "生成预览", exact: true }).click();
  await modal.getByText("模拟预览失败，请重试", { exact: true }).waitFor();
  assert.equal(
    await modal
      .getByRole("button", { name: "确认采用", exact: true })
      .isDisabled(),
    true,
  );
  await modal.getByRole("button", { name: "重试预览", exact: true }).click();
  await modal.locator("video").waitFor();
  await modal.locator("video").evaluate(async (v) => {
    await v.play();
    v.pause();
  });
  await page.screenshot({ path: `${out}/review-desktop.png` });
  await modal.getByRole("button", { name: "确认采用", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await modal.getByRole("button", { name: "已采用", exact: true }).waitFor();
  assert.equal(revision, 1);
  await modal.getByRole("button", { name: "历史版本", exact: true }).click();
  await modal.getByRole("button", { name: "恢复为候选", exact: true }).click();
  await modal.getByRole("button", { name: "生成预览", exact: true }).click();
  await modal.getByRole("button", { name: "确认采用", exact: true }).waitFor();
  assert.equal(
    revision,
    1,
    "Restoring only proposes; it must not overwrite immediately",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await modal.evaluate((el) => el.scrollWidth <= el.clientWidth));
  await page.screenshot({ path: `${out}/review-mobile.png` });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`${base}/projects/p/works`);
  await page.getByRole("heading", { name: "作品", exact: true }).waitFor();
  await page.getByRole("link").filter({ hasText: "科幻短片" }).click();
  await page.getByRole("heading", { name: "科幻短片", exact: true }).waitFor();
  await page
    .getByRole("heading", { name: "样片时间线", exact: true })
    .waitFor();
  await page.screenshot({ path: `${out}/work-desktop.png` });
  await page.getByRole("link", { name: "样片时间线 继续剪辑 →" }).click();
  await page.getByRole("link", { name: "科幻短片 · 返回作品 ↗" }).click();
  await page.getByRole("heading", { name: "工作剪辑", exact: true }).waitFor();
  await page.getByRole("button", { name: "候选预览", exact: true }).click();
  await page.getByRole("button").filter({ hasText: "已采用 · 版本 1" }).click();
  await page.locator("video").waitFor();
  await page.getByRole("button", { name: "历史版本", exact: true }).click();
  await page.getByRole("button").filter({ hasText: "版本 1 ·" }).click();
  await page.getByRole("link", { name: "前往审阅与恢复" }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.screenshot({ path: `${out}/work-mobile.png` });
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  await page
    .getByRole("heading", { name: "样片剪辑文档", exact: true })
    .waitFor();
  await page.screenshot({ path: `${out}/work-documents-mobile.png` });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page
    .getByRole("button", { name: "从此修订创建候选", exact: true })
    .click();
  await page.getByRole("dialog", { name: "作品审阅", exact: true }).waitFor();
  await page.goto(`${base}/projects/p/works/w`);
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  await page.getByLabel("画布宽度").fill("1000");
  await page.getByRole("button", { name: "保存修改候选", exact: true }).click();
  assert.equal(
    nativePayload.canvas.width,
    800,
    "Candidate must not auto-adopt",
  );
  await page.getByRole("button", { name: "检查修改影响", exact: true }).click();
  await page.getByText("可能受影响的时间范围", { exact: true }).click();
  await page.getByText("原版：0.5–1 秒", { exact: true }).waitFor();
  await page.getByText("候选版：1.5–2 秒", { exact: true }).waitFor();

  assert.equal(
    await page
      .getByRole("button", { name: "确认采用工程内容", exact: true })
      .isDisabled(),
    true,
  );
  await page.getByRole("checkbox", { name: /声音起点/ }).check();
  await page
    .getByLabel("使用位置变化")
    .getByText(/角色海报 \/ 主角/)
    .waitFor();
  await page
    .getByLabel("嵌套修改位置")
    .getByText("左画面 → 主角（蒙版）", { exact: true })
    .waitFor();
  await page.getByRole("button", { name: "生成工程预览", exact: true }).click();
  await page
    .getByRole("img", { name: "固定工程修订的图像预览", exact: true })
    .waitFor();
  const adopted = page.waitForResponse(
    (r) => r.url().endsWith("/accept") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "确认采用工程内容", exact: true })
    .click();
  await adopted;
  await page.getByText("工程内容修改", { exact: true }).waitFor();
  await page.screenshot({ path: `${out}/native-document-mobile.png` });
  assert.equal(nativePayload.canvas.width, 1000);
  await page.getByRole("button", { name: "已采用内容", exact: true }).click();
  await page
    .getByRole("heading", { name: "固定工程输出", exact: true })
    .waitFor();
  await page.getByText("角色海报", { exact: true }).waitFor();
  await page.getByRole("button", { name: "历史版本", exact: true }).click();
  await page.getByRole("button").filter({ hasText: "版本 2 ·" }).click();
  await page
    .getByRole("button", { name: "从此版本创建恢复候选", exact: true })
    .click();
  await page
    .getByRole("button", { name: "检查修改影响", exact: true })
    .waitFor();
  assert.equal(revision, 2, "Whole-work restoration must remain a candidate");
  await page.screenshot({ path: `${out}/work-fixed-outputs-mobile.png` });
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  const download = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "导出工程引用包", exact: true })
    .click();
  assert.match((await download).suggestedFilename(), /inspiration\.json$/);
  await page.getByRole("button", { name: "编辑文档", exact: true }).click();
  await page
    .getByRole("option", { name: "未来效果 · extension.future-effect" })
    .click();
  await page.getByText("该文档已完整保留。", { exact: false }).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "保存修改候选", exact: true })
      .count(),
    0,
  );
  await page.getByText("查看保存的扩展内容", { exact: true }).click();
  await page.getByText(/preserved/).waitFor();
  await page.getByRole("button", { name: "新建工程文档", exact: true }).click();
  await page.getByLabel("文档名称", { exact: true }).fill("新建片头画布");
  const created = page.waitForResponse(
    (r) => r.url().endsWith("/changes") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "创建文档候选", exact: true }).click();
  const creation = await created;
  assert.equal(
    creation.request().postDataJSON().commands[0].title,
    "新建片头画布",
  );
  assert.equal(
    creation.request().postDataJSON().commands[0].kind,
    "image.document",
  );
  await page.goto(`${base}/projects/p/works/w`);
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  await page.getByRole("button", { name: "编辑文档", exact: true }).click();
  await page
    .getByRole("option", { name: "配音同步 · 同步规则", exact: true })
    .click();
  await page
    .getByText("画面合成 · 修订 1 / 配音工程 · 修订 1", { exact: true })
    .waitFor();
  await page.getByLabel("同步规则名称").fill("片头音画对齐");
  await page.getByRole("button", { name: "同步要求", exact: true }).click();
  await page
    .getByRole("option", { name: "变化后需要人工复核", exact: true })
    .click();
  await page.getByRole("button", { name: "保存修改候选", exact: true }).click();
  await page
    .getByRole("button", { name: "检查修改影响", exact: true })
    .waitFor();
  const ruleWrite = writes.filter((r) => r.path.endsWith("/changes")).at(-1)
    .body.commands[0];
  assert.equal(ruleWrite.kind, "sync.document");
  assert.equal(ruleWrite.payload.constraints[0].policy, "review");
  assert.equal(ruleWrite.payload.constraints[0].members.length, 2);
  await page.screenshot({ path: `${out}/sync-rules-mobile.png` });
  assert.deepEqual(errors, []);
  changes.unshift({
    id: "multi",
    fingerprint: "c".repeat(64),
    status: "proposed",
    created_at: new Date().toISOString(),
    timeline_update: {
      timeline_id: "t",
      additional_updates: [{ timeline_id: "t2" }],
    },
    snapshot: {},
    previews: [],
  });
  await page.goto(`${base}/projects/p/works/w`);
  await page.getByRole("button", { name: "候选预览", exact: true }).click();
  await page
    .getByRole("button")
    .filter({ hasText: "待审阅候选" })
    .first()
    .click();
  await page.getByRole("button", { name: "检查修改影响", exact: true }).click();
  await page.getByRole("button", { name: "预览输出", exact: true }).click();
  await page.getByRole("option", { name: "剪辑 2", exact: true }).click();
  await page.getByRole("button", { name: "生成工程预览", exact: true }).click();
  await page.locator("video").waitFor();
  assert.equal(
    writes.filter((r) => r.path.endsWith("/multi/preview")).at(-1).body
      .output_id,
    "timeline:t2",
  );
  assert.deepEqual(errors, []);
  assert.equal(writes.filter((r) => r.path.endsWith("/accept")).length, 2);
  await writeFile(
    `${out}/report.json`,
    JSON.stringify(
      { errors, writes: writes.map((r) => r.path), revision, attempts },
      null,
      2,
    ),
  );
  console.log(
    "PASS: candidate, failed preview, retry, video playback, adoption, restore proposal, mobile layout, work listing/detail/history and review navigation",
  );
} finally {
  await browser.close();
}
