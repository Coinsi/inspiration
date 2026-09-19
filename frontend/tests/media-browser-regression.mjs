// All API calls are fixtures. Destructive/model actions cannot reach a real project.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.APP_URL || "http://127.0.0.1:5187";
const out = process.env.ARTIFACT_DIR || "./test-results/media-browser";
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const results = [];
const asset = (id, name, status = "draft", shots = 0) => ({
  id,
  name,
  status,
  type: "character",
  code: id,
  tags: [],
  summary: "角色与场景的视觉参考",
  shot_count: shots,
  ref_count: 1,
  gen_count: 2,
  representative_blob_hash: null,
});
const assets = [
  asset("a", "Alpha"),
  asset("b", "Beta", "draft", 9),
  asset("c", "Locked", "locked"),
];
const gens = [1, 2, 3].map((n) => ({
  id: `g${n}`,
  provider: "mock",
  output_type: "image",
  output_blob_hash: `hash${n}`,
  prompt_snapshot: `Version ${n}`,
  is_selected: n === 1,
  is_favorite: false,
  input_refs: {},
}));
const scene = { id: "s", title: "窗边的告别", code: "SC1", shot_count: 2 };
const shots = [1, 2].map((n) => ({
  id: `s${n}`,
  code: `SHOT${n}`,
  title: `镜头 ${n}`,
  scene_id: "s",
  description: "人物站在窗边，光线渐暗。",
  ordinal: n,
  production_status: n === 1 ? "approved" : "draft",
  selected_generation_id: null,
  storyboard: { duration_sec: 3, shot_size: "medium", dialogue: "再见" },
}));
async function scenario(name, run) {
  const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    }),
    page = await context.newPage(),
    requests = [],
    writes = [],
    errors = [];
  let broken = "",
    selected = "g1";
  page.on("pageerror", (e) => errors.push(e.message));
  await context.addInitScript(() => {
    localStorage.setItem("inspiration_token", "fixture");
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("sb.scene.p", '"deleted-scene"');
    localStorage.setItem("sb.novel.p", '"removed-novel"');
    localStorage.setItem("sb.chapter.p", '"removed-chapter"');
  });
  await context.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", "");
    requests.push(path + url.search);
    const respond = (body, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (req.method() !== "GET") {
      const body = req.postDataJSON();
      writes.push({ path, body });
      if (path.endsWith("/prompts/optimize"))
        return respond({
          prompt: "优化后的画面",
          avoid: "",
          explanation: "整合参考",
          assumptions: [],
          optimizer_model: "vision-fixture",
          reference_sources: [
            { kind: "generation", id: "g1", title: "Current image" },
          ],
        });
      if (path.endsWith("/select")) {
        selected = path.split("/").at(-2);
        return respond({});
      }
      return respond({ error: { message: "Blocked fixture write" } }, 403);
    }
    if (broken && path.endsWith(broken))
      return respond({ error: { message: "Fixture unavailable" } }, 503);
    if (path === "/me")
      return respond({
        user: { id: "u", username: "test", display_name: "Test" },
        memberships: [],
      });
    if (path === "/projects")
      return respond([{ id: "p", name: "创作样片", code: "P", settings: {} }]);
    if (path === "/projects/p")
      return respond({ id: "p", name: "创作样片", code: "P", settings: {} });
    if (path.endsWith("/assets"))
      return respond(
        assets.filter(
          (a) =>
            (!url.searchParams.get("type") ||
              a.type === url.searchParams.get("type")) &&
            a.name
              .toLowerCase()
              .includes((url.searchParams.get("q") || "").toLowerCase()),
        ),
      );
    if (path.endsWith("/reference-images"))
      return respond([
        {
          id: "r1",
          blob_hash: "hash4",
          role: "ref",
          note: "原始参考",
          ordinal: 0,
        },
      ]);
    if (path.endsWith("/generations"))
      return respond(
        gens.map((g) => ({ ...g, is_selected: g.id === selected })),
      );
    if (path.includes("/blobs/"))
      return route.fulfill({
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="#6f8093"/><circle cx="200" cy="130" r="70" fill="#d9b697"/></svg>',
      });
    if (path.endsWith("/providers")) return respond([]);
    if (path.endsWith("/capabilities"))
      return respond({
        modalities: ["image"],
        features: ["img2img", "inpaint"],
        param_schema: {},
      });
    if (path.endsWith("/quota"))
      return respond({ used_cost: 0, limit_cost: 100 });
    if (path.endsWith("/novels")) return respond([{ id: "n", title: "原著" }]);
    if (path.endsWith("/scenes")) return respond([scene]);
    if (path.endsWith("/shots")) return respond(shots);
    return respond([]);
  });
  try {
    await run({ page, requests, writes, setBroken: (v) => (broken = v) });
    assert.deepEqual(errors, []);
    results.push({ name, passed: true });
  } finally {
    await context.close();
  }
}
try {
  await scenario(
    "asset sorting, preview, hidden selection and empty search",
    async ({ page, writes }) => {
      await page.goto(`${base}/projects/p/assets`);
      await page
        .getByRole("button", { name: "快速预览 Beta", exact: true })
        .waitFor();
      assert.match(await page.locator("article").first().innerText(), /Beta/);
      await page.getByLabel("素材排序").selectOption("name.asc");
      assert.match(await page.locator("article").first().innerText(), /Alpha/);
      await page
        .getByRole("button", { name: "快速预览 Alpha", exact: true })
        .click();
      await page.getByRole("dialog").waitFor();
      await page.getByText("原始参考", { exact: true }).waitFor();
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "批量管理", exact: true }).click();
      await page.getByRole("button", { name: "全选", exact: true }).click();
      assert.match(await page.locator("main").innerText(), /2/);
      await page.getByRole("button", { name: "场景", exact: true }).click();
      await page.getByText("没有符合筛选的素材", { exact: true }).waitFor();
      await page.getByRole("button", { name: "清除筛选", exact: true }).click();
      await page
        .getByRole("button", { name: "快速预览 Alpha", exact: true })
        .waitFor();
      await page.screenshot({ path: `${out}/assets-fixture.png` });
      assert.deepEqual(writes, []);
    },
  );
  await scenario("asset errors and retry", async ({ page, setBroken }) => {
    setBroken("/assets");
    await page.goto(`${base}/projects/p/assets`);
    await page.getByRole("alert").waitFor();
    assert.equal(
      await page.getByText("素材数量暂不可用", { exact: true }).count(),
      1,
    );
    setBroken("");
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await page
      .getByRole("button", { name: "快速预览 Beta", exact: true })
      .waitFor();
  });
  await scenario(
    "storyboard scoped gallery, editor and mobile",
    async ({ page, requests, writes }) => {
      await page.goto(`${base}/projects/p/storyboard`);
      await page
        .getByRole("heading", { name: "镜头 1", exact: true })
        .waitFor();
      assert.ok(
        !requests.some(
          (p) =>
            p.includes("deleted-scene") ||
            p.includes("removed-novel") ||
            p.includes("removed-chapter"),
        ),
      );
      await page
        .getByRole("button", { name: "快速预览", exact: true })
        .first()
        .click();
      await page.getByRole("dialog").waitFor();
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "分镜编辑", exact: true }).click();
      await page.getByLabel("镜头标题").first().waitFor();
      await page.getByRole("button", { name: "画面浏览", exact: true }).click();
      await page.setViewportSize({ width: 390, height: 844 });
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
      await page.screenshot({
        path: `${out}/storyboard-mobile.png`,
        fullPage: true,
      });
      assert.deepEqual(writes, []);
    },
  );
  await scenario(
    "visual optimizer, explicit adoption and version comparison",
    async ({ page, writes }) => {
      await page.goto(`${base}/tests/fixtures/generation-panel.html`);
      await page
        .getByRole("button", { name: "优化提示词", exact: true })
        .click();
      await page.getByLabel("原始想法").fill("一个人物站在窗边");
      await page.getByRole("button", { name: "结合参考", exact: true }).click();
      assert.ok(
        await page
          .getByRole("button", { name: "生成优化建议", exact: true })
          .isDisabled(),
      );
      await page
        .getByRole("button", { name: "已选定 · g1", exact: true })
        .click();
      await page
        .getByRole("button", { name: "生成优化建议", exact: true })
        .click();
      await page.getByLabel("优化结果", { exact: true }).waitFor();
      assert.equal(writes.length, 1);
      assert.deepEqual(writes[0].body.references, [
        { kind: "generation", id: "g1" },
      ]);
      await page
        .getByRole("button", { name: "采用到生成指令", exact: true })
        .click();
      assert.equal(await page.getByRole("dialog").count(), 0);
      assert.ok(!writes.some((w) => w.path.endsWith("/generate")));
      await page.getByLabel("加入比较 · g1", { exact: true }).check();
      await page.getByLabel("加入比较 · g2", { exact: true }).check();
      assert.ok(
        await page.getByLabel("加入比较 · g3", { exact: true }).isDisabled(),
      );
      await page
        .getByRole("button", { name: "比较版本 (2/2)", exact: true })
        .click();
      await page.getByRole("dialog", { name: "图片版本比较" }).waitFor();
      await page.screenshot({ path: `${out}/version-compare.png` });
      const choose = page
        .getByRole("dialog")
        .getByRole("button")
        .filter({ hasText: "设为" });
      await choose.click();
      await page
        .getByRole("dialog")
        .getByText("Version 2", { exact: true })
        .waitFor();
      assert.ok(
        writes.some((w) => w.path === "/projects/p/generations/g2/select"),
      );
    },
  );
  await writeFile(
    `${out}/regression.json`,
    JSON.stringify({ passed: true, results }, null, 2),
  );
  console.log(JSON.stringify({ passed: true, results }));
} finally {
  await browser.close();
}
