import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE
    ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
    : "playwright"
);
const base = process.env.APP_URL || "http://127.0.0.1:5173";
const output = process.env.ARTIFACT_DIR;
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
const errors = [],
  requests = [];
let lostUploadReceipt = false;
let rejectNextRender = false;
page.on("pageerror", (e) => errors.push(e.message));
await context.addInitScript((token) => {
  localStorage.setItem("inspiration_token", token);
  localStorage.setItem("inspiration_lang", "zh");
}, process.env.LIVE_TOKEN);
await context.route("**/api/**", async (route) => {
  const request = route.request(),
    url = new URL(request.url());
  url.searchParams.delete("token"); // The isolated bridge supplies its own test identity.
  if (rejectNextRender && request.method() === "POST" && url.pathname.endsWith("/render")) {
    rejectNextRender = false;
    await route.fulfill({status:503,contentType:"application/json",body:JSON.stringify({detail:"模拟导出服务暂不可用"})});
    return;
  }
  const response = await context.request.fetch(
    process.env.LIVE_API + url.pathname + url.search,
    {
      method: request.method(),
      data: request.postDataBuffer() ?? undefined,
      headers: Object.fromEntries(
        Object.entries(request.headers()).filter(([k]) =>
          ["content-type", "range"].includes(k),
        ),
      ),
    },
  );
  requests.push({
    path: url.pathname,
    method: request.method(),
    status: response.status(),
  });
  if (
    request.method() === "PUT" &&
    url.pathname.includes("/archive-uploads/") &&
    !lostUploadReceipt
  ) {
    lostUploadReceipt = true;
    await route.abort("failed");
    return;
  }
  try {
    await route.fulfill({ response });
  } catch (error) {
    // Navigating away can finish a canceled media route before the bridge replies.
    if (
      !String(error).includes("Route is already handled") &&
      !request.failure()
    )
      throw error;
  }
});
try {
  await page.goto(
    `${base}/projects/${process.env.LIVE_PROJECT}/works/${process.env.LIVE_WORK}`,
  );
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  await page.getByRole("button", { name: "新建工程文档", exact: true }).click();
  await page.getByLabel("文档名称", { exact: true }).fill("真实片头画布");
  await page.getByRole("button", { name: "创建文档候选", exact: true }).click();
  await page.getByRole("button", { name: "检查修改影响", exact: true }).click();
  await page.getByRole("button", { name: "生成工程预览", exact: true }).click();
  const preview = page.getByRole("img", {
    name: "固定工程修订的图像预览",
    exact: true,
  });
  await preview.waitFor();
  await preview.evaluate((image) => image.decode());
  await page
    .getByRole("button", { name: "确认采用工程内容", exact: true })
    .click();
  await page.getByText("工程内容修改", { exact: true }).waitFor();
  await page.reload();
  await page.getByRole("button", { name: "已采用内容", exact: true }).click();
  await page.getByText("真实片头画布", { exact: true }).waitFor();
  await page
    .getByRole("heading", { name: "真实工程链路验收", exact: true })
    .waitFor();
  const adopted = page.getByRole("img", {
    name: "真实片头画布 · 已采用修订预览",
    exact: true,
  });
  await adopted.waitFor();
  await adopted.evaluate((image) => image.decode());
  await page.screenshot({ path: `${output}/adopted.png`, fullPage: true });
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  const downloadEvent = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "导出工程引用包", exact: true })
    .click();
  const download = await downloadEvent;
  await download.saveAs(`${output}/export.json`);
  await page.getByRole("button", { name: "导出完整归档", exact: true }).click();
  await page.getByLabel("工程归档任务").waitFor();
  await page.reload();
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  await page
    .getByRole("button", { name: "下载归档", exact: true })
    .waitFor({ timeout: 30000 });
  await page.evaluate(() =>
    Object.defineProperty(window, "showSaveFilePicker", { value: undefined }),
  );
  const archiveEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载归档", exact: true }).click();
  await (await archiveEvent).saveAs(`${output}/export.zip`);
  await page.screenshot({
    path: `${output}/archive-tasks.png`,
    fullPage: true,
  });
  await page.goto(
    `${base}/projects/${process.env.LIVE_PROJECT}/works/${process.env.LIVE_IMPORT_WORK}`,
  );
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  await page
    .getByLabel("选择工程包", { exact: true })
    .setInputFiles(`${output}/export.zip`);
  await page.getByRole("alert").waitFor();
  await page.reload();
  await page.getByRole("button", { name: "工程内容", exact: true }).click();
  const chooserEvent = page.waitForEvent("filechooser");
  await page
    .getByRole("button", { name: "选择原文件续传", exact: true })
    .click();
  await (await chooserEvent).setFiles(`${output}/export.zip`);
  await page
    .getByLabel("工程归档任务")
    .getByText(/已恢复为候选/)
    .waitFor({ timeout: 30000 });
  await page.screenshot({
    path: `${output}/archive-import.png`,
    fullPage: true,
  });
  assert(lostUploadReceipt);
  await page.goto(
    `${base}/projects/${process.env.LIVE_PROJECT}/works/${process.env.LIVE_WORK}`,
  );
  await page.getByRole("button", { name: "开始剪辑", exact: true }).click();
  await page.waitForURL(/\/cuts\?timeline=/);
  await page.getByRole("button", { name: "导出成片", exact: true }).waitFor();
  assert.equal(
    await page.locator("#timeline-export-settings").isVisible(),
    false,
  );
  await page.getByRole("button", { name: "导出成片", exact: true }).click();
  assert.equal(
    await page.locator("#timeline-export-settings").isVisible(),
    true,
  );
  await page.getByRole("dialog", {name:"导出成片",exact:true}).getByRole("button", {name:"关闭",exact:true}).click();
  assert.equal(
    await page.locator("#timeline-export-settings").isVisible(),
    false,
  );
  await page.getByRole("button", { name: "添加素材", exact: true }).click();
  await page.getByRole("button", { name: "素材库", exact: true }).click();
  await page
    .getByRole("option")
    .filter({ hasText: "直接剪辑测试图片" })
    .click();
  await page.getByRole("button", { name: "加入剪辑", exact: true }).click();
  await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === "PUT" &&
        r.url().includes("/document") &&
        r.status() === 200,
    ),
    page.getByRole("button", { name: "保存时间线", exact: true }).click(),
  ]);
  await page.reload();
  await page.getByText("素材库 ·", { exact: false }).waitFor();
  const frame = page.locator(".timeline-composition-frame");
  await frame.waitFor();
  await page.waitForFunction(
    () =>
      document
        .querySelector(".timeline-composition-frame")
        ?.getBoundingClientRect().height > 250,
  );
  const frameBox = await frame.boundingBox();
  const transportBox = await page
    .locator(".timeline-live-controls")
    .boundingBox();
  assert(Math.abs(frameBox.width - transportBox.width) < 3);
  assert.equal(await page.locator("[data-visual-track]").count(), 0);
  await page.getByRole("button", { name: "显示叠加轨", exact: true }).click();
  assert.equal(await page.locator("[data-visual-track]").count(), 3);
  await page.getByRole("button", { name: "收起空叠加轨", exact: true }).click();
  assert.equal(
    await page
      .getByRole("slider", { name: "合成预览位置", exact: true })
      .getAttribute("aria-valuetext"),
    "00:00 / 00:03",
  );
  const endHandle = page.getByRole("button", { name: "裁切终点 items 1", exact: true });
  // Source metadata may still be loading; retry the outward edit until ready.
  await page.waitForTimeout(1000);
  await endHandle.focus();
  await endHandle.press("ArrowRight");
  await page.waitForFunction(() => document.querySelector('[aria-label="时长 1"]')?.value === "3.1");
  await endHandle.press("ArrowLeft");
  await page.waitForFunction(() => document.querySelector('[aria-label="时长 1"]')?.value === "3");
  await page.getByRole("button",{name:"播放时间线",exact:true}).click();
  await page.getByRole("button",{name:"暂停时间线",exact:true}).waitFor();
  const playbackSeek = page.getByRole("slider",{name:"合成预览位置",exact:true});
  const playbackBox = await playbackSeek.boundingBox();
  await playbackSeek.click({position:{x:playbackBox.width * 0.4,y:playbackBox.height/2}});
  assert(await page.getByRole("button",{name:"暂停时间线",exact:true}).isVisible());
  const sought = Number(await playbackSeek.inputValue());
  await page.waitForFunction(t => Number(document.querySelector('[aria-label="合成预览位置"]').value) > t + 100, sought);
  await page.getByRole("button",{name:"暂停时间线",exact:true}).click();
  await page.getByRole("button",{name:"回到开头",exact:true}).click();
  assert(await page.getByRole("button",{name:"播放时间线",exact:true}).isVisible());
  assert.equal(Number(await playbackSeek.inputValue()),0);
  const durationField = page.getByRole("spinbutton", { name: "时长 1", exact: true });
  await durationField.fill("4.5");
  await durationField.press("Enter");
  assert.equal(await durationField.inputValue(), "4.5");
  await page.getByRole("button", {name:"撤销剪辑",exact:true}).click();
  assert.equal(await durationField.inputValue(), "3");
  await page.getByRole("button", {name:"复制",exact:true}).click();
  assert.equal(await page.getByRole("button", {name:/裁切终点 items/}).count(), 2);
  const [copySave] = await Promise.all([
    page.waitForResponse(r => r.request().method() === "PUT" && r.url().includes("/document") && r.status() === 200),
    page.getByRole("button",{name:"保存时间线",exact:true}).click()
  ]);
  const copied = copySave.request().postDataJSON().items;
  assert.equal(new Set(copied.map(c => c.id)).size, 2);
  await page.getByRole("button", {name:"撤销剪辑",exact:true}).click();
  const seek = page.getByRole("slider", {name:"合成预览位置",exact:true});
  const seekBox = await seek.boundingBox();
  await seek.click({position:{x:seekBox.width / 2,y:seekBox.height / 2}});
  await page.getByRole("button", {name:"在播放头处拆分",exact:true}).click();
  assert.equal(await page.getByRole("button", {name:/裁切终点 items/}).count(), 2);
  await page.getByRole("button", {name:"撤销剪辑",exact:true}).click();
  await Promise.all([
    page.waitForResponse(r => r.request().method() === "PUT" && r.url().includes("/document") && r.status() === 200),
    page.getByRole("button",{name:"保存时间线",exact:true}).click()
  ]);
  await page.getByRole("tab", {name:"声音与字幕",exact:true}).click();
  await page.getByRole("button", {name:"从素材库添加音频",exact:true}).click();
  await page.getByRole("button", {name:"素材库",exact:true}).click();
  await page.getByRole("option").filter({hasText:"剪辑测试配乐"}).click();
  await page.getByRole("button", {name:"加入剪辑",exact:true}).click();
  await page.getByRole("button", {name:"裁切终点 audio 1",exact:true}).waitFor();
  const [audioSave] = await Promise.all([
    page.waitForResponse(r => r.request().method() === "PUT" && r.url().includes("/document") && r.status() === 200),
    page.getByRole("button",{name:"保存时间线",exact:true}).click()
  ]);
  assert.equal(audioSave.request().postDataJSON().audio[0].name, "剪辑测试配乐");
  await page.reload();
  await page.getByRole("button", {name:"裁切终点 audio 1",exact:true}).waitFor();
  await page.getByRole("tab", {name:"声音与字幕",exact:true}).click();
  const wave = Buffer.alloc(16044);
  wave.write("RIFF",0); wave.writeUInt32LE(16036,4); wave.write("WAVEfmt ",8);
  wave.writeUInt32LE(16,16); wave.writeUInt16LE(1,20); wave.writeUInt16LE(1,22);
  wave.writeUInt32LE(8000,24); wave.writeUInt32LE(16000,28); wave.writeUInt16LE(2,32); wave.writeUInt16LE(16,34);
  wave.write("data",36); wave.writeUInt32LE(16000,40);
  await page.getByLabel("上传音频文件",{exact:true}).setInputFiles({name:"local-music.wav",mimeType:"audio/wav",buffer:wave});
  await page.getByRole("button", {name:"裁切终点 audio 2",exact:true}).waitFor();
  await Promise.all([
    page.waitForResponse(r => r.request().method() === "PUT" && r.url().includes("/document") && r.status() === 200),
    page.getByRole("button",{name:"保存时间线",exact:true}).click()
  ]);
  await page.getByRole("tab", {name:"片段调整",exact:true}).click();
  await page.getByRole("button", { name: "全屏预览", exact: true }).click();
  await page.getByRole("button", { name: "退出全屏预览", exact: true }).click();
  await page.setViewportSize({ width: 768, height: 900 });
  await page
    .getByRole("heading", { name: "剪辑成片", exact: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `${output}/editing-narrow.png`,
    fullPage: true,
  });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  );
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page
    .getByRole("heading", { name: "剪辑成片", exact: true })
    .scrollIntoViewIfNeeded();
  const mediaPane = await page.getByRole("complementary", {name:"剪辑素材",exact:true}).boundingBox();
  const propertiesPane = await page.getByRole("complementary", {name:"剪辑属性",exact:true}).boundingBox();
  const previewPane = await page.locator(".timeline-preview-grid").boundingBox();
  const timelinePane = await page.locator(".timeline-overview").boundingBox();
  assert(mediaPane.x + mediaPane.width <= previewPane.x);
  assert(previewPane.x + previewPane.width <= propertiesPane.x);
  assert(timelinePane.y + timelinePane.height <= 1000);
  const heightControl = page.getByRole("slider",{name:"时间线高度",exact:true});
  await heightControl.focus(); await heightControl.press("End");
  await page.waitForFunction(() => document.querySelector(".timeline-overview").getBoundingClientRect().height >= 319);
  await heightControl.press("Home");
  await page.waitForFunction(() => document.querySelector(".timeline-overview").getBoundingClientRect().height <= 141);
  await heightControl.press("ArrowRight");
  await page.getByRole("button", {name:"作品版本",exact:true}).click();
  await page.getByRole("dialog", {name:"作品版本与审核",exact:true}).getByRole("button", {name:"关闭",exact:true}).click();
  await page.getByRole("button", {name:"历史导出",exact:true}).click();
  await page.getByRole("dialog", {name:"历史导出",exact:true}).getByRole("button", {name:"关闭",exact:true}).click();
  await page.getByRole("button",{name:"添加素材",exact:true}).click();
  await page.getByRole("button",{name:"素材库",exact:true}).click();
  await page.getByRole("option").filter({hasText:"剪辑测试视频"}).click();
  await page.getByRole("button",{name:"加入剪辑",exact:true}).click();
  await page.getByRole("button",{name:"裁切终点 items 2",exact:true}).waitFor();
  await page.getByRole("tab",{name:"叠加画面",exact:true}).click();
  await page.getByRole("button",{name:"叠加素材版本",exact:true}).click();
  await page.getByRole("option").filter({hasText:"直接剪辑测试图片"}).click();
  await page.getByRole("button",{name:"添加叠加画面",exact:true}).click();
  await page.getByRole("button",{name:"裁切终点 visuals 1",exact:true}).waitFor();
  await page.getByRole("tab",{name:"声音与字幕",exact:true}).click();
  await page.getByRole("tab",{name:/^字幕/}).click();
  await page.getByRole("button",{name:"添加字幕",exact:true}).click();
  await page.getByRole("textbox",{name:"字幕文字 1",exact:true}).fill("混合剪辑回归测试");
  await page.getByRole("tab",{name:"片段调整",exact:true}).click();
  await page.getByRole("button",{name:"移除片段",exact:true}).click();
  assert.equal(await page.getByRole("button",{name:/裁切终点 items/}).count(),1);
  await page.getByRole("button",{name:"撤销剪辑",exact:true}).click();
  await page.getByRole("button",{name:"裁切终点 items 2",exact:true}).waitFor();
  rejectNextRender = true;
  await page.getByRole("button",{name:"导出成片",exact:true}).click();
  const exportDialog = page.getByRole("dialog",{name:"导出成片",exact:true});
  await exportDialog.getByRole("button",{name:"保存并导出 MP4",exact:true}).click();
  await exportDialog.getByText(/导出提交失败/).waitFor();
  const [renderResult] = await Promise.all([
    page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/render") && r.status() === 200, {timeout:60000}),
    exportDialog.getByRole("button",{name:"保存并导出 MP4",exact:true}).click()
  ]);
  assert.equal((await renderResult.json()).status, "succeeded");
  await exportDialog.waitFor({state:"hidden"});
  await page.reload();
  await page.getByRole("button",{name:"裁切终点 items 2",exact:true}).waitFor();
  await page.getByRole("button",{name:"裁切终点 audio 2",exact:true}).waitFor();
  await page.getByRole("button",{name:"裁切终点 subtitles 1",exact:true}).waitFor();
  await page.getByRole("button",{name:"裁切终点 visuals 1",exact:true}).waitFor();
  await page.screenshot({
    path: `${output}/editing-simplified.png`,
    fullPage: true,
  });
  await page
    .getByRole("link", { name: "真实工程链路验收 · 返回作品 ↗", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "真实工程链路验收", exact: true })
    .waitFor();
  await page.getByRole("link").filter({ hasText: "继续剪辑" }).waitFor();
  assert.deepEqual(errors, []);
  assert(requests.some((r) => r.path.endsWith("/accept") && r.status === 200));
  assert(
    requests.some(
      (r) =>
        r.path.endsWith("/preview") && r.method === "POST" && r.status === 200,
    ),
  );
  await writeFile(`${output}/requests.json`, JSON.stringify(requests, null, 2));
} catch (error) {
  await page.screenshot({ path: `${output}/failure.png`, fullPage: true });
  await writeFile(
    `${output}/failure.txt`,
    (await page.locator("body").innerText()) + "\n" + JSON.stringify(requests),
  );
  throw error;
} finally {
  await context.unrouteAll({ behavior: "ignoreErrors" });
  await browser.close();
}
