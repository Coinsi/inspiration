import { toggleAccountTheme } from "./helpers/account.mjs";
// Writes only to a new QA project. Agent uses its explicit local mock engine.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
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
    viewport: { width: 1440, height: 1000 },
  }),
  page = await context.newPage();
const errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
const check = (s) => {
  checks.push(s);
  console.log(s);
};
const shot = async (name) =>
  page.screenshot({ path: out + "/" + name + ".png", animations: "disabled" });
try {
  const login = await context.request.post(base + "/api/v1/auth/login", {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(login.ok());
  const token = (await login.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](base + "/api/v1" + path, {
      headers,
      data,
    });
    assert.ok(r.ok(), await r.text());
    return r.status() === 204 ? null : r.json();
  };
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  const project = await call("post", "/projects", {
      code: "CONTINUITY-" + Date.now(),
      name: "界面连续体验验收",
    }),
    root = "/projects/" + project.id;
  await writeFile(
    out + "/fixture.json",
    JSON.stringify({ project: project.id }),
  );
  const script = await call("post", root + "/scripts", { title: "长场景验收" });
  await call("post", `${root}/scripts/${script.id}/apply-scenes`, {
    scenes: [
      {
        title: "雨夜街道",
        shots: Array.from({ length: 90 }, (_, i) => ({
          title: "镜头" + (i + 1),
          description: "霓虹照在雨水中，旅人走过街头。",
        })),
      },
    ],
  });
  const shots = await call("get", root + "/shots");
  for (let i = 0; i < 55; i++)
    await call("post", root + "/assets", {
      type: i % 2 ? "character" : "location",
      name: "素材" + String(i + 1).padStart(3, "0"),
      summary: "为雨夜的场景和人物保留创作描述。",
    });
  await page.goto(base + root + "/assets");
  await page.locator("main article").first().waitFor();
  assert.equal(await page.locator("main article").count(), 48);
  assert.equal(await page.getByText("暂无代表图", { exact: true }).count(), 0);
  assert.ok(
    (await page.locator("main article").first().boundingBox()).height < 330,
  );
  await shot("assets-dark");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  assert.equal(await page.locator("main article").count(), 7);
  await page
    .getByRole("button", { name: /^快速预览 / })
    .first()
    .click();
  await page.getByRole("dialog").waitFor();
  await page.keyboard.press("Escape");
  await page.reload();
  await page.locator("main article").first().waitFor();
  assert.equal(await page.locator("main article").count(), 7);
  check(
    "Asset cards have compact no-artwork state; bounded pages, preview and reload preserve page",
  );
  await page.getByLabel("素材排序", { exact: true }).selectOption("name.asc");
  await page.locator("main article").filter({ hasText: "素材001" }).waitFor();
  assert.equal(await page.locator("main article").count(), 48);
  await page.reload();
  await page.locator("main article").first().waitFor();
  assert.equal(
    await page.getByLabel("素材排序", { exact: true }).inputValue(),
    "name.asc",
  );
  const nav = page.getByRole("navigation", { name: "工作区功能", exact: true });
  assert.ok((await nav.getByRole("link").count()) <= 12);
  await nav
    .getByRole("button", { name: "展开创作工具", exact: true })
    .click();
  await nav.getByRole("link", { name: "技能库", exact: true }).waitFor();
  await page.goto(base + root + "/library");
  await page.getByRole("heading", { name: "把第一段视频放进来" }).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "选择本页", exact: true }).count(),
    0,
  );
  assert.equal(await page.getByLabel("视频排序", { exact: true }).count(), 0);
  await page
    .getByRole("button", { name: "目录与批量管理", exact: true })
    .click();
  await page.getByRole("button", { name: "新建目录", exact: true }).click();
  await page.getByLabel("目录名称", { exact: true }).fill("镜头参考");
  await page.getByRole("button", { name: "保存目录", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.equal((await call("get", root + "/library/folders")).length, 1);
  await page
    .getByRole("button", { name: "目录与批量管理", exact: true })
    .click();
  await shot("library-empty-dark");
  await page.goto(base + root + "/transcriptions");
  await page.getByRole("heading", { name: "先放入一段视频" }).waitFor();
  assert.equal(await page.getByLabel("转写语言").count(), 0);
  await shot("transcriptions-empty");
  await page.goto(base + root + "/cuts");
  await page.getByRole("heading", { name: "开始你的第一条成片" }).waitFor();
  assert.equal(await page.getByLabel("时间线名称", { exact: true }).count(), 0);
  assert.equal(
    await page.getByText("交付与版本对比", { exact: true }).count(),
    0,
  );
  await shot("cuts-empty");
  await page.getByRole("button", { name: "新建时间线", exact: true }).click();
  await page.getByLabel("时间线名称", { exact: true }).fill("第一版");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("heading", { name: "开始你的第一条成片" }).waitFor();
  await page.getByRole("button", { name: "新建时间线", exact: true }).click();
  assert.equal(
    await page.getByLabel("时间线名称", { exact: true }).inputValue(),
    "第一版",
  );
  await page.getByRole("button", { name: "新建", exact: true }).click();
  await page.getByLabel("工作时间线", { exact: true }).waitFor();
  assert.equal((await call("get", root + "/timelines")).length, 1);
  check(
    "Stage navigation retains tools; empty libraries hide unavailable controls; directory and timeline creation work",
  );
  await page.goto(base + root + "/storyboard");
  await page
    .getByRole("link", { name: /生成与引用/ })
    .nth(30)
    .scrollIntoViewIfNeeded();
  const scroll = await page.locator("main").evaluate((e) => e.scrollTop);
  assert.ok(scroll > 300);
  await page
    .getByRole("link", { name: /生成与引用/ })
    .nth(30)
    .click();
  await page.getByLabel("本次生成指令", { exact: true }).waitFor();
  await page.getByRole("button", { name: "返回分镜", exact: true }).click();
  await page
    .getByRole("link", { name: /生成与引用/ })
    .nth(30)
    .waitFor();
  await page.waitForFunction(
    (n) => Math.abs(document.querySelector("main").scrollTop - n) < 5,
    scroll,
  );
  await page.goto(base + root + "/shots");
  await page.getByLabel("制作状态看板").waitFor();
  assert.equal(
    await page.getByLabel("制作状态看板").locator("button").count(),
    41,
  );
  await page.getByRole("button", { name: /加载更多（40/ }).click();
  assert.equal(
    await page.getByLabel("制作状态看板").locator("button").count(),
    81,
  );
  check(
    "Returning to storyboard restores scroll; production board hides empty columns and bounds initial cards",
  );
  const fragment = await call("post", root + "/prompt-fragments", {
    name: "晨光",
    text: "柔和晨光",
    category: "lighting",
  });
  await page.goto(base + root + "/prompts");
  await page.getByRole("button", { name: "查看提示词 晨光" }).click();
  await page.getByRole("button", { name: "编辑片段", exact: true }).click();
  await page.getByLabel("提示词内容").fill("暖色侧光");
  await page.getByRole("button", { name: "保存提示词", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  let updated = (await call("get", root + "/prompt-fragments"))[0];
  assert.equal(updated.text, "暖色侧光");
  await page.getByRole("button", { name: "编辑片段", exact: true }).click();
  await page.getByLabel("提示词内容").fill("我的未保存修改");
  await call("patch", root + "/prompt-fragments/" + fragment.id, {
    name: "晨光",
    text: "另一处修改",
    category: "lighting",
    expected_updated_at: updated.updated_at,
  });
  await page.getByRole("button", { name: "保存提示词", exact: true }).click();
  await page.getByRole("dialog").getByRole("alert").waitFor();
  assert.equal(
    await page.getByLabel("提示词内容").inputValue(),
    "我的未保存修改",
  );
  await page
    .getByRole("button", { name: "重新读取最新片段", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  assert.equal(
    await page.getByLabel("提示词内容").inputValue(),
    "我的未保存修改",
  );
  await page
    .getByRole("button", { name: "重新读取最新片段", exact: true })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelector('[aria-label="提示词内容"]')?.value ===
      "另一处修改",
  );
  await page.getByRole("dialog").getByTitle("关闭", { exact: true }).click();
  await page.getByRole("button", { name: "删除片段", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  assert.equal((await call("get", root + "/prompt-fragments")).length, 1);
  await page.getByRole("button", { name: "删除片段", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "确定", exact: true })
    .click();
  await page.getByText("把常用的画面描述留在这里").waitFor();
  assert.equal((await call("get", root + "/prompt-fragments")).length, 0);
  check(
    "Fragment edit/delete persist; concurrent edits preserve local input; canceled deletion is harmless",
  );
  await page.goto(base + root + "/workbench");
  await page
    .getByLabel("创作想法", { exact: true })
    .fill("检查雨夜街道的镜头描述");
  await page.getByRole("button", { name: "带入创作助理", exact: true }).click();
  const assistant = page.getByRole("dialog", { name: "创作助理", exact: true });
  await assistant.getByLabel("创作目标").waitFor();
  assert.ok(page.url().endsWith("/workbench"));
  await assistant.getByRole("button", { name: /^选择创作对象/ }).click();
  const picker = page.getByRole("dialog", {
    name: "选择创作对象",
    exact: true,
  });
  assert.equal(
    await picker.getByRole("button", { name: /^选择对象 / }).count(),
    40,
  );
  const started = performance.now();
  await picker.getByLabel("搜索创作对象").fill(shots[89].code);
  await picker
    .getByRole("button", { name: /^选择对象 / })
    .first()
    .waitFor();
  assert.equal(
    await picker.getByRole("button", { name: /^选择对象 / }).count(),
    1,
  );
  const searchMs = performance.now() - started;
  await picker.getByRole("button", { name: /^选择对象 / }).click();
  await picker.getByRole("button", { name: "完成选择" }).click();
  await assistant.getByText("高级运行选项", { exact: true }).click();
  await assistant.getByLabel("运行方式", { exact: true }).selectOption("mock");
  await assistant.getByTitle("关闭", { exact: true }).click();
  await page.getByRole("button", { name: "带入创作助理", exact: true }).click();
  assert.equal(
    await assistant.getByLabel("运行方式", { exact: true }).inputValue(),
    "mock",
  );
  assert.equal((await call("get", root + "/agent/runs")).length, 0);
  await shot("assistant-context");
  await assistant
    .getByRole("button", { name: "开始创作", exact: true })
    .click();
  await assistant
    .getByRole("heading", { name: "检查雨夜街道的镜头描述", exact: true })
    .waitFor();
  assert.equal((await call("get", root + "/agent/runs")).length, 1);
  assert.ok(page.url().endsWith("/workbench"));
  // Drive only this test's explicitly mock run when the preview's automatic queue is disabled.
  assert.ok(process.env.TEST_PYTHON);
  const run = (await call("get", root + "/agent/runs"))[0];
  execFileSync(
    process.env.TEST_PYTHON,
    [
      "-c",
      `import sys,uuid\nfrom dotenv import load_dotenv\nload_dotenv('.env')\nfrom app.core.database import SessionLocal\nfrom app.models.agent import AgentRun\nfrom app.modules.agent.worker import advance\nrid=uuid.UUID(sys.argv[1])\nwith SessionLocal() as db:\n r=db.get(AgentRun,rid)\n assert str(r.project_id)==sys.argv[2] and r.engine=='mock'\nfor i in range(10):\n if not advance(rid): break\n`,
      run.id,
      project.id,
    ],
    { cwd: new URL("../../backend/", import.meta.url), timeout: 30000 },
  );
  assert.equal(
    (await call("get", root + "/agent/runs/" + run.id)).status,
    "succeeded",
  );
  await assistant.getByText("已结束", { exact: true }).waitFor();
  await assistant.getByTitle("关闭", { exact: true }).click();
  await page.getByRole("button", { name: "带入创作助理", exact: true }).click();
  await assistant
    .getByRole("heading", { name: "检查雨夜街道的镜头描述", exact: true })
    .waitFor();
  assert.equal((await call("get", root + "/agent/runs")).length, 1);
  check(
    "Workbench Agent stays in place; scope survives close; only explicit start creates a run; result reopens without duplication",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await assistant.evaluate((e) => e.scrollWidth <= e.clientWidth));
  await shot("assistant-mobile");
  await assistant
    .getByRole("button", { name: "新的创作任务", exact: true })
    .click();
  await assistant.getByLabel("创作目标", { exact: true }).fill("继续完善开场");
  assert.equal((await call("get", root + "/agent/runs")).length, 1);
  await assistant.getByTitle("关闭", { exact: true }).click();
  await page.goto(base + root + "/assets");
  await page.locator("main article").first().waitFor();
  assert.ok(
    await page.locator("main").evaluate((e) => e.scrollWidth <= e.clientWidth),
  );
  await shot("assets-mobile");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await toggleAccountTheme(page);
  await shot("assets-light");
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify(
      { passed: true, project: project.id, checks, searchMs, errors },
      null,
      2,
    ),
  );
} catch (e) {
  await shot("failure");
  await writeFile(
    out + "/failure.json",
    JSON.stringify({ error: String(e), checks, errors }, null, 2),
  );
  throw e;
} finally {
  await browser.close();
}
