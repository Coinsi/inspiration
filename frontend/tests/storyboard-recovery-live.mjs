// Supply an authenticated, isolated project entry URL. Only the newly created test scenes are edited.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { pathToFileURL } from "node:url";

assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const entry = process.env.TEST_PROJECT_ENTRY;
assert.ok(entry);
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({
  viewport: { width: 1440, height: 940 },
  locale: "zh-CN",
});
page.setDefaultTimeout(10000);
const errors = [];
const generated = [];
const referenceWrites = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("request", (req) => {
  if (
    req.method() === "POST" &&
    /\/(generate|breakdown|generation-batches)(\?|$)/.test(req.url())
  )
    generated.push(req.url());
  if (req.method() === "PUT" && req.url().endsWith("/asset-refs"))
    referenceWrites.push(req.url());
});
const button = (name, within = page) =>
  within.getByRole("button", { name, exact: true });
const tab = (name, within = page) =>
  within.getByRole("tab", { name, exact: true });
const menuItem = (name) => page.getByRole("menuitem", { name, exact: true });
const dialog = page.getByRole("dialog");
const stamp = Date.now();
const art = process.env.ARTIFACT_DIR;
async function capture(name) {
  if (!art) return;
  await mkdir(art, { recursive: true });
  await page.screenshot({
    path: art + "/" + name + ".png",
    animations: "disabled",
  });
}
try {
  await page.goto(entry);
  await page.waitForURL((url) => url.pathname.startsWith("/projects/"));
  const origin = new URL(entry).origin;
  const root = new URL(page.url()).pathname.match(/^\/projects\/[^/]+/)[0];
  const api = (method, route, data) =>
    page.evaluate(
      async ({ method, path, data }) => {
        const res = await fetch("/api/v1" + path, {
          method,
          headers: {
            "Content-Type": "application/json",
            Authorization:
              "Bearer " + localStorage.getItem("inspiration_token"),
          },
          body: data === undefined ? undefined : JSON.stringify(data),
        });
        if (!res.ok)
          throw Error(method + " " + path + " returned " + res.status);
        return res.status === 204 ? null : res.json();
      },
      { method, path: root + route, data },
    );
  const script = await api("POST", "/scripts", {
    title: "分镜恢复验收 " + stamp,
  });
  const [scene, neighbor] = await api(
    "POST",
    "/scripts/" + script.id + "/apply-scenes",
    {
      scenes: [
        {
          title: "镜头恢复 " + stamp,
          body: "雨落在街灯下",
          shots: [
            { title: "雨滴", description: "窗边水珠滑落" },
            { title: "窗边", description: "人物侧身" },
          ],
        },
        {
          title: "邻场 " + stamp,
          shots: [{ title: "远街", description: "行人散开" }],
        },
      ],
    },
  );
  const path = "/scenes/" + scene.id + "/shots";
  const original = await api("GET", path);
  const [first, second] = original;
  const asset = await api("POST", "/assets", {
    name: "校验旅人 " + stamp,
    type: "character",
  });
  const companion = await api("POST", "/assets", {
    name: "校验同行者 " + stamp,
    type: "character",
  });
  await api("PUT", "/shots/" + first.id + "/asset-refs", {
    refs: [{ asset_id: asset.id }, { asset_id: companion.id }],
  });
  await api("POST", "/reviews", { target_id: first.id });
  const board = origin + root + "/storyboard?scene=" + scene.id;
  await page.goto(board);
  const menu = (name) => button("更多镜头操作 · " + name);
  const items = ["查看镜头", "参考素材", "审阅镜头", "重命名", "移除镜头"];
  for (const view of ["浏览", "编辑"]) {
    await tab(view).click();
    await menu("雨滴").focus();
    await page.keyboard.press("Enter");
    assert.deepEqual(await page.getByRole("menuitem").allTextContents(), items);
    await page.keyboard.press("Escape");
  }
  await menu("窗边").click();
  await menuItem("重命名").click();
  await dialog.getByLabel("镜头名称").fill("转身");
  await button("保存名称", dialog).click();
  await menu("转身").waitFor();
  const restoredName = await api("GET", "/shots/" + second.id);
  assert.equal(restoredName.title, "转身");
  await tab("浏览").click();
  await menu("雨滴").click();
  await menuItem("审阅镜头").click();
  await tab("审阅镜头").waitFor();
  assert.equal(await tab("审阅镜头").getAttribute("aria-selected"), "true");
  assert.equal(new URL(page.url()).searchParams.get("shot"), first.id);
  await page
    .getByText(/第.?1.?轮/)
    .first()
    .waitFor();
  const returnPath = root + "/assets/" + asset.id + "?tab=usages";
  const shotPath = origin + root + "/shots?shot=" + first.id;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(
    shotPath + "&tab=refs&return=" + encodeURIComponent(returnPath),
  );
  await dialog.getByRole("tab", { name: "角色与场景" }).waitFor();
  const chosen = dialog.getByLabel("已选角色与场景");
  const removeCompanion = button("移除引用 " + companion.name, chosen);
  await removeCompanion.waitFor();
  const previousWrites = referenceWrites.length;
  await chosen.getByText(companion.name, { exact: true }).click();
  assert.equal(
    (await api("GET", "/shots/" + first.id + "/asset-refs")).length,
    2,
  );
  assert.equal(referenceWrites.length, previousWrites);
  await removeCompanion.click();
  await removeCompanion.waitFor({ state: "detached" });
  assert.deepEqual(
    (await api("GET", "/shots/" + first.id + "/asset-refs")).map(
      (r) => r.asset_id,
    ),
    [asset.id],
  );
  const companionCard = button(
    `参考素材 ${companion.name}`,
    dialog,
  );
  await companionCard.click();
  await removeCompanion.waitFor();
  assert.equal(await companionCard.getAttribute("aria-pressed"), "true");
  assert.equal(await companionCard.isEnabled(), false);
  await dialog.getByText("镜头里用到了哪些设定", { exact: true }).click();
  await dialog.getByRole("group", { name: companion.name }).waitFor();
  const scrollAreas = await dialog.evaluate((el) =>
    Array.from(el.querySelectorAll("*"))
      .filter((node) => {
        const style = getComputedStyle(node);
        return (
          /^(auto|scroll)$/.test(style.overflowY) &&
          node.scrollHeight > node.clientHeight + 2
        );
      })
      .map((node) => node.classList.contains("shot-reference-scroll")),
  );
  assert.deepEqual(scrollAreas, [true]);
  const done = await button("完成", dialog).boundingBox();
  assert.ok(done.y >= 0 && done.y + done.height <= 844);
  await capture("shot-reference-mobile");
  await button("返回角色与场景详情", dialog).click();
  await page.waitForURL((url) => url.pathname + url.search === returnPath);
  await page.goto(
    shotPath +
      "&tab=refs&ref=media&return=" +
      encodeURIComponent(root + "/../foreign/assets"),
  );
  assert.equal(
    await tab("素材库", dialog).getAttribute("aria-selected"),
    "true",
  );
  assert.equal(await dialog.getByRole("button", { name: /^返回/ }).count(), 0);
  await button("完成", dialog).click();
  await button("返回分镜").click();
  await menu("雨滴").waitFor();
  await capture("storyboard-mobile");
  assert.ok(
    await page
      .locator("main")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  );
  await button("选择").click();
  await page.getByLabel("选择镜头 雨滴", { exact: true }).check();
  await page.getByRole("button", { name: /场景目录/ }).click();
  await page
    .locator(".storyboard-scene-item")
    .filter({ hasText: neighbor.title })
    .click();
  await menu("远街").waitFor();
  assert.equal(await button("选择").getAttribute("aria-pressed"), "false");
  await page.goto(board);
  await menu("雨滴").waitFor();
  await button("选择").click();
  await button("选择本场").click();
  await button("移除所选").click();
  const confirmation = page.getByRole("alertdialog");
  assert.match(await confirmation.innerText(), /移除 2 个镜头/);
  assert.match(await confirmation.innerText(), /已移除镜头/);
  await button("取消", confirmation).click();
  assert.equal((await api("GET", path)).length, 2);
  await page.route(
    "**" + path + "/remove",
    (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "移除暂不可用" } }),
      }),
    { times: 1 },
  );
  await button("移除所选").click();
  await button("移除 2 个镜头", confirmation).click();
  await page.getByRole("status").filter({ hasText: "移除暂不可用" }).waitFor();
  assert.equal((await api("GET", path)).length, 2);
  await button("移除所选").click();
  await button("移除 2 个镜头", confirmation).click();
  await page.getByText("该场景暂无镜头", { exact: true }).waitFor();
  assert.equal((await api("GET", path)).length, 0);
  assert.equal(
    (await api("GET", "/scenes/" + neighbor.id + "/shots")).length,
    1,
  );
  await page.reload();
  await button("已移除镜头").click();
  await dialog.getByLabel("恢复镜头 雨滴").click();
  await dialog.getByLabel("恢复镜头 雨滴").waitFor({ state: "detached" });
  await button("选择本场", dialog).click();
  await button("恢复所选（1）", dialog).click();
  await dialog.getByText("本场没有已移除的镜头").waitFor();
  await button("关闭", dialog).click();
  const active = await api("GET", path);
  assert.deepEqual(
    active.map((s) => s.id),
    original.map((s) => s.id),
  );
  assert.equal(
    (await api("GET", "/shots/" + first.id + "/asset-refs"))[0].asset_id,
    asset.id,
  );
  assert.equal(
    (await api("GET", "/reviews?target_type=shot&target_id=" + first.id))
      .length,
    1,
  );
  await page.setViewportSize({ width: 1440, height: 940 });
  await capture("storyboard-restored");
  assert.deepEqual(generated, []);
  assert.deepEqual(errors, []);
  console.log(
    "Passed: gallery/editor menus, rename, review, both reference targets, scoped return, mobile, canceled and confirmed scene removal, reload, single and bulk restore.",
  );
} catch (error) {
  await capture("storyboard-failure");
  throw error;
} finally {
  await browser.close();
}
