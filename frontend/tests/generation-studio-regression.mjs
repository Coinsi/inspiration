// Intercept every API call; no model or project mutation can reach the server.
import assert from "node:assert/strict";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const { chromium } = await import(
    pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
  ),
  base = process.env.APP_URL || "http://127.0.0.1:5187",
  out = process.env.ARTIFACT_DIR;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  }),
  page = await context.newPage(),
  errors = [],
  writes = [];
let user = "u1",
  broken = false,
  brokenPicture = false,
  releaseEstimate,
  releaseGenerate;
page.on("pageerror", (e) => errors.push(e.message));
await context.addInitScript(() => {
  localStorage.setItem("inspiration_token", "fixture");
  localStorage.setItem("inspiration_lang", "zh");
});
const picture = await readFile("frontend/public/images/studio-scenes.webp");
await context.route("**/api/**", async (route) => {
  const r = route.request(),
    url = new URL(r.url()),
    p = url.pathname.replace("/api/v1", ""),
    respond = (x, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(x),
      });
  if (r.method() !== "GET") {
    writes.push({ path: p, body: r.postDataJSON() });
    if (p.endsWith("/estimate")) {
      await new Promise((resolve) => (releaseEstimate = resolve));
      return respond({ points: 111 });
    }
    if (p.endsWith("/generate")) {
      await new Promise((resolve) => (releaseGenerate = resolve));
      return respond({ id: "job1", status: "succeeded" });
    }
    return respond({});
  }
  if (p === "/me")
    return respond({
      user: { id: user, username: "test", display_name: "Test" },
      memberships: [],
    });
  if (p.endsWith("/providers")) return respond([]);
  if (p.endsWith("/capabilities"))
    return respond({
      modalities: ["image", "video"],
      features: ["img2img", "inpaint", "first_frame", "last_frame"],
      param_schema: {},
    });
  if (p.endsWith("/quota")) return respond({ used_cost: 0, limit_cost: 200 });
  if (p.endsWith("/generations"))
    return broken
      ? respond({ error: { message: "Fixture unavailable" } }, 503)
      : respond(
          [1, 2].map((n) => ({
            id: "g" + n,
            provider: "mock",
            output_type: "image",
            output_blob_hash: "hash" + n,
            prompt_snapshot: "版本指令 " + n,
            is_selected: n === 1,
            is_favorite: n === 2,
            input_refs: {},
          })),
        );
  if (p.includes("/blobs/"))
    return brokenPicture
      ? route.fulfill({ status: 404, body: "Unavailable" })
      : route.fulfill({ contentType: "image/webp", body: picture });
  if (p.includes("/jobs/")) return respond({ id: "job1", status: "succeeded" });
  return respond([]);
});
const url = base + "/tests/fixtures/generation-panel.html";
try {
  await page.goto(url);
  const prompt = page.getByLabel("本次生成指令", { exact: true });
  await prompt.fill("镜头A的未提交草稿");
  await page.reload();
  assert.equal(await prompt.inputValue(), "镜头A的未提交草稿");
  await page.goto(url + "?target=b");
  assert.equal(await prompt.inputValue(), "");
  await prompt.fill("镜头B草稿");
  await page.goto(url);
  assert.equal(await prompt.inputValue(), "镜头A的未提交草稿");
  user = "u2";
  await page.reload();
  assert.equal(await prompt.inputValue(), "");
  user = "u1";
  await page.reload();
  assert.equal(await prompt.inputValue(), "镜头A的未提交草稿");
  await page
    .locator('[data-generation-id="g2"]')
    .getByRole("button", { name: "复用指令", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  assert.equal(await prompt.inputValue(), "镜头A的未提交草稿");
  await page
    .locator('[data-generation-id="g2"]')
    .getByRole("button", { name: "复用指令", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  assert.equal(await prompt.inputValue(), "版本指令 2");
  assert.equal(writes.length, 0);
  await page
    .locator('[data-generation-id="g2"]')
    .getByRole("button", { name: "作为参考", exact: true })
    .click();
  await page.getByText("将基于此图片继续创作", { exact: true }).waitFor();
  assert.equal(
    await page.locator(".generation-reference-preview img").getAttribute("alt"),
    "重绘参考",
  );
  await page
    .getByRole("group", { name: "筛选生成结果" })
    .getByRole("button", { name: "收藏", exact: true })
    .click();
  assert.equal(await page.locator("[data-generation-id]").count(), 1);
  await page
    .getByRole("group", { name: "筛选生成结果" })
    .getByRole("button", { name: "视频", exact: true })
    .click();
  await page.getByText("没有符合筛选的作品", { exact: true }).waitFor();
  await page.getByRole("button", { name: "查看全部作品", exact: true }).click();
  await page.getByRole("button", { name: "预估", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector(".generation-submit-row button")?.disabled,
  );
  await prompt.fill("改变后的指令");
  releaseEstimate();
  await page.waitForFunction(
    () => !document.querySelector(".generation-submit-row button")?.disabled,
  );
  assert.equal(await page.getByText(/≈ 111/).count(), 0);
  await page.getByRole("button", { name: "生成", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("fieldset").disabled);
  assert.ok(await prompt.isDisabled());
  releaseGenerate();
  await page.getByText("已完成", { exact: true }).waitFor();
  const request = writes.find((w) => w.path.endsWith("/generate"));
  assert.equal(request.body.prompt_override, "改变后的指令");
  assert.equal(request.body.source_generation_id, "g2");
  assert.equal(request.body.use_references, false);
  await page.screenshot({ path: out + "/generation-studio-dark.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.evaluate(() => document.documentElement.classList.add("light"));
  await page.screenshot({
    path: out + "/generation-studio-mobile-light.png",
    fullPage: true,
  });
  broken = true;
  await page.reload();
  await page
    .getByText("作品暂时无法加载，已有版本不会丢失。", { exact: true })
    .waitFor();
  assert.equal(
    await page.getByText("暂无生成结果", { exact: true }).count(),
    0,
  );
  broken = false;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await page.locator('[data-generation-id="g2"]').waitFor();
  brokenPicture = true;
  await page.reload();
  await page.getByText("画面暂不可用", { exact: true }).first().waitFor();
  brokenPicture = false;
  await page
    .locator('[data-generation-id="g2"]')
    .getByRole("button", { name: "重新加载", exact: true })
    .click();
  await page.waitForFunction(() => {
    const img = document.querySelector('[data-generation-id="g2"] img');
    return img?.naturalWidth > 0;
  });
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify(
      {
        passed: true,
        checks: [
          "draft reload/object/account isolation",
          "reuse prompt confirmation and cancel",
          "reference preview and exact submitted source",
          "favorites and empty filter",
          "late estimate ignored",
          "pending generation locks inputs",
          "failed result load and retry",
          "missing image placeholder and explicit retry",
          "390px layout",
        ],
        writes,
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Generation studio regression passed");
} catch (e) {
  await page.screenshot({ path: out + "/failure.png", fullPage: true });
  throw e;
} finally {
  await browser.close();
}
