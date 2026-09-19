import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
assert.equal(process.env.TEST_ISOLATED_PREVIEW, "1");
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR;
await mkdir(out, { recursive: true });
const { chromium } = await import(
  pathToFileURL(process.env.PLAYWRIGHT_MODULE).href
);
const browser = await chromium.launch({ headless: true, channel: "chrome" }),
  context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
  }),
  page = await context.newPage(),
  errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
try {
  const login = await context.request.post(base + "/api/v1/auth/login", {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(login.ok());
  const token = (await login.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const res = await context.request[method](base + "/api/v1" + path, {
      headers,
      data,
    });
    assert.ok(res.ok(), await res.text());
    return res.json();
  };
  const project = await call("post", "/projects", {
      name: "技能视觉验收 · 创作方法",
    }),
    root = `/projects/${project.id}`;
  const seeds = [
    [
      "shot",
      "电影感镜头设计",
      "从景别、构图到镜头运动，把一段故事拆成有情绪、有节奏的画面。",
    ],
    [
      "shot",
      "人物连续性检查",
      "核对人物服装、光线与动作衔接，让每一个镜头自然连接。",
    ],
    [
      "shot",
      "自然语言转分镜",
      "把文字中的动作与情绪，整理为可直接用于创作的分镜描述。",
    ],
    [
      "story",
      "短剧故事结构",
      "梳理冲突、人物动机与情绪转折，为故事建立清晰的叙事节奏。",
    ],
    [
      "story",
      "对白与人物声音",
      "保留角色的说话习惯，让对白推动剧情，也让人物更鲜活。",
    ],
    [
      "character",
      "角色视觉档案",
      "整理角色的外貌、服装和识别特征，建立一致的视觉设定。",
    ],
    [
      "edit",
      "剪辑节奏与交付",
      "从粗剪到成片，逐项核对镜头节奏、字幕和作品交付要求。",
    ],
  ];
  const created = [];
  for (const [category, name, description] of seeds)
    created.push(
      await call("post", root + "/skills", {
        category,
        name,
        description,
        instructions: `# ${name}\n\n把创作意图整理成可执行的方法。\n\n## 适合什么时候用\n\n- 开始设计一组新镜头\n- 核对人物与环境的连续性\n\n## 创作步骤\n\n1. 阅读当前场景与已有设定。\n2. 明确画面重点和人物动机。\n3. 参考 [镜头检查表](references/checklist.md) 完成核对。\n\n> 优先保留故事的情绪与创作者的表达。`,
        required_tools: ["object.read"],
        source: "创作团队 · 验收方法",
        files: [
          {
            path: "references/checklist.md",
            content:
              "# 镜头检查表\n\n| 检查项 | 要点 |\n| --- | --- |\n| 画面 | 保持构图与视线方向一致 |\n| 人物 | 服装与动作衔接 |",
            encoding: "utf-8",
          },
          {
            path: "examples/scene.md",
            content: "# 雨夜重逢\n\n中景，人物进入街灯的暖光中。",
            encoding: "utf-8",
          },
        ],
      }),
    );
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(base + root + "/skills");
  await page
    .getByRole("button", { name: "查看技能 电影感镜头设计", exact: true })
    .waitFor();
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(await page.getByLabel("技能名称", { exact: true }).count(), 0);
  await page.screenshot({ path: out + "/catalog-dark.png", fullPage: true });
  checks.push("Browse-first grouped cards, no inline editor");
  await page.getByRole("button", { name: "镜头与画面", exact: true }).click();
  await page
    .getByRole("button", { name: "查看技能 电影感镜头设计", exact: true })
    .waitFor();
  assert.equal(await page.locator(".knowledge-card").count(), 3);
  await page
    .getByRole("button", { name: "查看技能 电影感镜头设计", exact: true })
    .click();
  let reader = page.getByRole("dialog");
  await reader
    .getByRole("heading", { name: "电影感镜头设计", exact: true, level: 2 })
    .waitFor();
  await reader.getByRole("button", { name: "镜头检查表", exact: true }).click();
  await reader.locator("table").waitFor();
  await reader.getByRole("button", { name: "SKILL.md", exact: true }).click();
  await page.screenshot({ path: out + "/reader-dark.png" });
  await reader.getByLabel("筛选技能文件").fill("scene");
  assert.equal(
    await reader
      .getByRole("button", { name: "references/checklist.md", exact: true })
      .count(),
    0,
  );
  await reader
    .getByRole("button", { name: "examples/scene.md", exact: true })
    .click();
  await reader.getByRole("button", { name: "源码", exact: true }).click();
  assert.match(await reader.locator("pre").textContent(), /# 雨夜重逢/);
  await reader.getByRole("button", { name: "编辑技能", exact: true }).click();
  const editor = page.getByRole("dialog");
  await editor.getByLabel("技能名称", { exact: true }).waitFor();
  await editor
    .getByLabel("适用任务", { exact: true })
    .fill("保留草稿的电影镜头方法");
  await editor.getByRole("button", { name: "关闭", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  assert.equal(
    await editor.getByLabel("适用任务", { exact: true }).inputValue(),
    "保留草稿的电影镜头方法",
  );
  await editor.getByRole("tab", { name: "方法说明", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  assert.equal(
    await editor
      .getByRole("tab", { name: "配套文件", exact: false })
      .getAttribute("aria-selected"),
    "true",
  );
  await editor
    .getByRole("button", { name: "references/checklist.md", exact: true })
    .click();
  await editor
    .getByLabel("配套文件内容")
    .fill("# 更新检查表\n保留角色服装连续性。");
  await page.screenshot({ path: out + "/editor-files-dark.png" });
  await editor.getByRole("button", { name: "保存技能", exact: true }).click();
  await editor.getByText("已保存版本 2", { exact: true }).waitFor();
  await editor.getByRole("tab", { name: "版本历史", exact: true }).click();
  await editor.getByText("版本 1 · 电影感镜头设计", { exact: true }).waitFor();
  await editor
    .getByRole("button", { name: "查看内容", exact: true })
    .last()
    .click();
  await page
    .getByRole("dialog")
    .last()
    .getByRole("button", { name: "关闭", exact: true })
    .click();
  await editor.getByRole("button", { name: "关闭", exact: true }).click();
  assert.equal(await page.getByRole("dialog").count(), 0);
  assert.equal(await page.evaluate(() => document.body.style.overflow), "");
  checks.push(
    "Reader navigation/source and independent editor, draft close protection, file save and history reading",
  );
  await page
    .getByRole("button", { name: "收藏 电影感镜头设计", exact: true })
    .click();
  await page
    .getByRole("button", { name: "取消收藏 电影感镜头设计", exact: true })
    .waitFor();
  await page.getByRole("tab", { name: "我的收藏", exact: true }).click();
  await page.locator(`[data-skill-id="${created[0].id}"]`).waitFor();
  await page.getByRole("tab", { name: "项目技能", exact: true }).click();
  await page.getByRole("button", { name: "全部分类", exact: true }).click();
  await page.getByLabel("搜索技能", { exact: true }).fill("没有这样的技能");
  await page.getByText("没有找到匹配的技能", { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "清除筛选", exact: true })
    .last()
    .click();
  await page
    .getByRole("button", { name: "查看技能 电影感镜头设计", exact: true })
    .waitFor();
  await page.evaluate(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  });
  await page.screenshot({ path: out + "/catalog-light.png", fullPage: true });
  await page.getByRole("tab", { name: "方法模板", exact: true }).click();
  await page
    .getByRole("button", { name: "查看技能 镜头连续性检查", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "使用模板", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "保存技能", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByText("已保存版本 1", { exact: true })
    .waitFor();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "关闭", exact: true })
    .click();
  checks.push(
    "Favorites/search/reset and template-to-editor creation retain real actions",
  );
  await page.getByRole("tab", { name: "项目技能", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: out + "/catalog-mobile-light.png",
    fullPage: true,
  });
  assert.ok(
    await page
      .locator("main")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  );
  await page
    .getByRole("button", { name: "查看技能 电影感镜头设计", exact: true })
    .click();
  reader = page.getByRole("dialog");
  await reader
    .getByRole("button", { name: "切换文件目录", exact: true })
    .click();
  await reader
    .getByRole("button", { name: "references/checklist.md", exact: true })
    .click();
  await reader.getByText("保留角色服装连续性。", { exact: true }).waitFor();
  assert.ok(await reader.evaluate((el) => el.scrollWidth <= el.clientWidth));
  await page.screenshot({ path: out + "/reader-mobile-light.png" });
  await reader.getByRole("button", { name: "编辑技能", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByLabel("技能名称", { exact: true })
    .waitFor();
  await page.screenshot({ path: out + "/editor-mobile-light.png" });
  assert.ok(
    await page
      .getByRole("dialog")
      .evaluate((el) => el.scrollWidth <= el.clientWidth),
  );
  assert.deepEqual(errors, []);
  checks.push(
    "Dark/light desktop and mobile, collapsible file navigation, no overflow or page errors",
  );
  await writeFile(
    out + "/report.json",
    JSON.stringify(
      { passed: true, project: project.id, checks, errors },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ passed: true, project: project.id, checks }));
} catch (e) {
  await page.screenshot({ path: out + "/failure.png", fullPage: true });
  throw e;
} finally {
  await browser.close();
}
