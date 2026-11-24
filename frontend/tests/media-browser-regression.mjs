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
  { ...asset("a", "Alpha"), representative_blob_hash: "hash4" },
  asset("b", "Beta", "draft", 1),
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
async function scenario(name, run, { role = "admin", catalog = assets, allowCreate = false } = {}) {
  const items = [...catalog];
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
      if (allowCreate && req.method() === "POST" && path === "/projects/p/assets") {
        const created = { ...asset("created-prop", body.name), ...body };
        items.push(created);
        return respond(created);
      }
      if (path.endsWith("/assets/batch-delete/preview")) {
        return respond({
          eligible_ids: items.filter(a => body.ids.includes(a.id) && a.status !== "locked").map(a => a.id),
          shot_count: 0, floating_shots: 0, pinned_shots: 0,
        });
      }
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
        memberships: [{ project_id: "p", role }],
      });
    if (path === "/projects")
      return respond([{ id: "p", name: "创作样片", code: "P", settings: {} }]);
    if (path === "/projects/p")
      return respond({ id: "p", name: "创作样片", code: "P", settings: {} });
    if (path.endsWith("/assets"))
      return respond(
        items.filter(
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
    if (path.endsWith("/assets/b/usages")) return respond([
      { shot_id: "s1", shot_code: "SHOT1", shot_title: "镜头 1", scene_title: scene.title,
        role: "character", ref_mode: "floating" },
      { shot_id: "s1", shot_code: "SHOT1", shot_title: "镜头 1", scene_title: scene.title,
        role: "costume", ref_mode: "pinned", pinned_version_no: 2 },
    ]);
    const entity = items.find(a => path === "/projects/p/assets/" + a.id);
    if (entity) return respond(entity);
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
    "asset sorting, preview, scoped page selection and empty search",
    async ({ page, writes }) => {
      await page.goto(`${base}/projects/p/assets`);
      await page
        .getByRole("button", { name: "快速预览 Beta", exact: true })
        .waitFor();
      assert.match(await page.locator("article").first().innerText(), /Beta/);
      const tops = await page.locator("main article").evaluateAll(nodes => nodes.map(node => ({
        heading: node.querySelector("h2").getBoundingClientRect().top,
        actions: node.querySelector('[aria-haspopup="menu"]').getBoundingClientRect().top,
        bottom: node.getBoundingClientRect().bottom,
      })));
      for (const metric of ["heading", "actions", "bottom"]) {
        assert.ok(Math.max(...tops.map(row => row[metric])) - Math.min(...tops.map(row => row[metric])) < 2,
          `Cards with and without covers align at their ${metric}`);
      }
      await page.getByRole("button", { name: "素材排序" }).click();
      await page.getByRole("option", { name: "名称 A → Z", exact: true }).click();
      assert.match(await page.locator("article").first().innerText(), /Alpha/);
      await page
        .getByRole("button", { name: "快速预览 Alpha", exact: true })
        .click();
      await page.getByRole("dialog").waitFor();
      await page.getByText("原始参考", { exact: true }).waitFor();
      const previewActions = page.getByRole("dialog").getByRole("button", { name: "Alpha的更多操作" });
      await previewActions.click();
      await page.getByRole("menuitem", { name: "查看详情" }).waitFor();
      await page.keyboard.press("Escape");
      await page.getByRole("menu").waitFor({ state: "hidden" });
      await page.getByRole("dialog").waitFor();
      assert.ok(await previewActions.evaluate(el => el === document.activeElement));
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "图谱", exact: true }).click();
      await page.getByRole("button", { name: "选择", exact: true }).click();
      assert.equal(await page.getByRole("button", { name: "列表", exact: true }).getAttribute("aria-pressed"), "true");
      await page.getByRole("button", { name: "选择本页", exact: true }).click();
      await page.getByText("已选 2", { exact: true }).waitFor();
      assert.ok(await page.getByRole("button", { name: "选择 Locked", exact: true }).isDisabled());
      await page.getByRole("button", { name: "场景", exact: true }).click();
      await page.getByText("没有符合筛选的素材", { exact: true }).waitFor();
      await page.getByText("已选 0", { exact: true }).waitFor();
      await page.getByRole("button", { name: "清除筛选", exact: true }).click();
      await page.getByRole("button", { name: "退出选择", exact: true }).click();
      await page.getByRole("button", { name: "卡片", exact: true }).click();
      await page
        .getByRole("button", { name: "快速预览 Alpha", exact: true })
        .waitFor();
      await page.screenshot({ path: `${out}/assets-fixture.png` });
      assert.deepEqual(writes, []);
    },
  );
  await scenario("asset menus and references are usable for read-only members", async ({ page, writes }) => {
    await page.goto(base + "/projects/p/assets");
    const more = page.getByRole("button", { name: "Beta的更多操作", exact: true });
    await more.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("menu").waitFor();
    assert.equal(await page.getByRole("menuitem", { name: "移入回收站" }).getAttribute("aria-disabled"), "true");
    await page.getByRole("menuitem", { name: "查看引用镜头" }).click();
    await page.getByRole("tab", { name: "引用镜头" }).waitFor();
    const shot = page.getByRole("link").filter({ hasText: scene.title });
    const href = new URL(await shot.getAttribute("href"), base);
    assert.equal(href.pathname, "/projects/p/shots");
    assert.equal(href.searchParams.get("shot"), "s1");
    assert.equal(href.searchParams.get("tab"), "refs");
    assert.equal(href.searchParams.get("return"), new URL(page.url()).pathname + new URL(page.url()).search);
    assert.match(await shot.innerText(), /镜头 1/);
    assert.match(await shot.innerText(), /浮动引用/);
    assert.match(await shot.innerText(), /固定引用 v2/);
    assert.deepEqual(writes, []);
  }, { role: "viewer" });
  await scenario("page selection preserves other pages and skips locked items", async ({ page, writes }) => {
    await page.goto(base + "/projects/p/assets");
    await page.getByRole("button", { name: "选择", exact: true }).click();
    const button = name => page.getByRole("button", { name, exact: true });
    await button("选择本页").click();
    await page.getByText("已选 48", { exact: true }).waitFor();
    await button("下一页").click();
    await button("选择本页").click();
    await page.getByText("已选 50", { exact: true }).waitFor();
    await button("取消本页选择").click();
    await page.getByText("已选 48", { exact: true }).waitFor();
    await button("选择全部匹配（50）").click();
    await page.getByText("已选 50", { exact: true }).waitFor();
    await button("移入回收站").click();
    const confirmation = page.getByRole("alertdialog", { name: "将 50 项移入回收站？" });
    await confirmation.getByText("可在「素材 → 回收站」恢复。", { exact: true }).waitFor();
    await confirmation.getByRole("button", { name: "取消" }).click();
    await page.getByText("已选 50", { exact: true }).waitFor();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].path, "/projects/p/assets/batch-delete/preview");
    assert.equal(new Set(writes[0].body.ids).size, 50);
    assert.ok(!writes[0].body.ids.includes("entity51"));
  }, {
    catalog: Array.from({ length: 51 }, (_, i) => asset("entity" + (i + 1), "旅人 " + (i + 1), i === 50 ? "locked" : "draft")),
  });
  await scenario("creating a prop outside the filter opens its details and preserves the list", async ({ page, writes }) => {
    await page.goto(base + "/projects/p/assets");
    const button = (name) => page.getByRole("button", { name, exact: true });
    await button("角色").click();
    const search = page.getByRole("searchbox", { name: "搜索名称" });
    await search.fill("Alpha");
    await page.getByText("当前结果 1 项", { exact: true }).waitFor();
    await button("新建").click();
    const dialog = page.getByRole("dialog", { name: "新建" });
    await dialog.getByRole("button", { name: "类型", exact: true }).click();
    await page.getByRole("option", { name: "道具", exact: true }).click();
    await dialog.getByRole("textbox", { name: "名称", exact: true }).fill("铜铃");
    await Promise.all([
      page.getByRole("status").filter({ hasText: "已创建「铜铃」" }).waitFor(),
      dialog.getByRole("button", { name: "创建", exact: true }).click(),
    ]);
    await page.getByRole("heading", { name: "铜铃", exact: true }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/projects/p/assets/created-prop");
    const editableName = page.getByRole("textbox", { name: "名称", exact: true });
    assert.equal(await editableName.inputValue(), "铜铃");
    assert.ok(await editableName.isEditable());
    await page.getByRole("link", { name: "返回角色与场景", exact: true }).click();
    await page.getByText("当前结果 1 项", { exact: true }).waitFor();
    assert.equal(await search.inputValue(), "Alpha");
    assert.equal(await button("角色").getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator("main article").count(), 1);
    assert.match(await page.locator("main article").innerText(), /Alpha/);
    assert.deepEqual(writes, [{ path: "/projects/p/assets", body: { name: "铜铃", type: "prop", tags: [], metadata: {} } }]);
  }, { allowCreate: true });
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
        .getByRole("button", { name: "快速预览 镜头 1", exact: true })
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
        .getByRole("button", { name: "快速预览 镜头 1", exact: true })
        .first()
        .click();
      await page.getByRole("dialog").waitFor();
      await page.keyboard.press("Escape");
      await page.getByRole("tab", { name: "编辑", exact: true }).click();
      await page.getByLabel("镜头标题").first().waitFor();
      await page.getByRole("tab", { name: "浏览", exact: true }).click();
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
      const confirmation = page.getByRole("alertdialog", { name: "更换当前采用版本？" });
      const adopted = page.waitForResponse(response =>
        new URL(response.url()).pathname === "/api/v1/projects/p/generations/g2/select" && response.ok());
      await confirmation.getByRole("button", { name: "确定", exact: true }).click();
      await adopted;
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
