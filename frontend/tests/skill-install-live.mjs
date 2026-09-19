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
      name: "技能安装验收 " + Date.now(),
    }),
    root = `/projects/${project.id}`;
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(base + root + "/skills");
  await page
    .getByRole("button", { name: "安装技能", exact: true })
    .first()
    .click();
  let dialog = page.getByRole("dialog");
  assert.ok(
    await dialog
      .getByRole("button", { name: "读取技能", exact: true })
      .isDisabled(),
  );
  await page.screenshot({ path: out + "/install-markdown-dark.png" });
  const drop = await page.evaluateHandle(() => {
    const dt = new DataTransfer();
    dt.items.add(
      new File(
        [
          "---\nname: 雨夜镜头方法\ndescription: 雨夜人物连续性\ncategory: shot\n---\n# 雨夜镜头\n保留人物的蓝色外套。",
        ],
        "SKILL.md",
        { type: "text/markdown" },
      ),
    );
    return dt;
  });
  await dialog
    .locator(".knowledge-install-drop")
    .dispatchEvent("drop", { dataTransfer: drop });
  await drop.dispose();
  await dialog.getByRole("button", { name: "读取技能", exact: true }).click();
  await dialog.getByLabel("安装技能名称", { exact: true }).waitFor();
  assert.equal((await call("get", root + "/skills")).length, 0);
  await dialog
    .getByLabel("安装技能名称", { exact: true })
    .fill("雨夜镜头 · 安装版");
  await dialog.getByLabel("技能分类", { exact: true }).selectOption("shot");
  await dialog.getByText("查看技能内容与文件", { exact: true }).click();
  await dialog
    .getByLabel("待安装技能")
    .getByText("SKILL.md", { exact: true })
    .waitFor();
  await page.screenshot({ path: out + "/install-preview-dark.png" });
  let lost = true;
  await page.route(`**/api/v1${root}/skills/install`, async (route) => {
    if (lost) {
      lost = false;
      const response = await route.fetch();
      assert.equal(response.status(), 200);
      await route.abort("failed");
    } else await route.continue();
  });
  await dialog.getByRole("button", { name: "确认安装", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  assert.equal((await call("get", root + "/skills")).length, 1);
  assert.ok(
    await dialog.getByLabel("安装技能名称", { exact: true }).isDisabled(),
  );
  await dialog.getByRole("button", { name: "重试安装", exact: true }).click();
  await dialog
    .getByRole("heading", { name: "雨夜镜头 · 安装版", exact: true, level: 2 })
    .waitFor();
  assert.equal((await call("get", root + "/skills")).length, 1);
  assert.equal(await page.getByLabel("技能名称", { exact: true }).count(), 0);
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await page
    .getByRole("button", { name: "查看技能 雨夜镜头 · 安装版", exact: true })
    .waitFor();
  checks.push(
    "Drop Markdown, preview without saving, metadata changes, lost-response retry creates exactly one package and opens reader",
  );
  await page
    .getByRole("button", { name: "安装技能", exact: true })
    .first()
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("tab", { name: "ZIP 技能包", exact: true }).click();
  await dialog
    .getByLabel("选择技能安装文件")
    .setInputFiles({
      name: "bad.zip",
      mimeType: "application/zip",
      buffer: Buffer.from("bad"),
    });
  await dialog.getByRole("button", { name: "读取技能", exact: true }).click();
  await dialog.getByRole("alert").waitFor();
  await dialog.getByLabel("选择技能安装文件").setInputFiles(out + "/story.zip");
  await dialog.getByRole("button", { name: "读取技能", exact: true }).click();
  await dialog.getByLabel("安装技能名称", { exact: true }).waitFor();
  await page.screenshot({ path: out + "/install-zip-dark.png" });
  await dialog.getByRole("button", { name: "确认安装", exact: true }).click();
  await dialog
    .getByRole("heading", { name: "海岸连续性", exact: true, level: 2 })
    .waitFor();
  await dialog
    .getByRole("button", { name: "references/rules.md", exact: true })
    .click();
  await dialog.getByText("服装始终保持蓝色", { exact: true }).waitFor();
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  checks.push(
    "Invalid ZIP retry, install complete package and read attachment without entering editor",
  );
  await page
    .getByRole("button", { name: "安装技能", exact: true })
    .first()
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("tab", { name: "GitHub", exact: true }).click();
  await dialog
    .getByLabel("GitHub 地址", { exact: true })
    .fill("https://github.com/example/film-skills");
  await dialog.getByLabel("分支或标签", { exact: true }).fill("main");
  await dialog
    .getByLabel("技能子目录", { exact: true })
    .fill("skills/director");
  const source = {
    name: "远程导演方法",
    description: "参考导演方法",
    instructions: "# 导演方法\n保持叙事方向。",
    required_tools: [],
    source: "GitHub: example/film-skills",
    category: "shot",
    files: [],
    source_metadata: {
      kind: "github",
      url: "https://github.com/example/film-skills",
      ref: "main",
      directory: "skills/director",
      commit: "a".repeat(40),
    },
  };
  await page.route(`**/api/v1${root}/skills/github-preview`, async (route) => {
    assert.deepEqual(route.request().postDataJSON(), {
      url: "https://github.com/example/film-skills",
      ref: "main",
      directory: "skills/director",
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(source),
    });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => {
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  });
  await page.screenshot({ path: out + "/install-github-mobile.png" });
  assert.ok(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth));
  await dialog.getByRole("button", { name: "读取技能", exact: true }).click();
  await dialog.getByLabel("安装技能名称", { exact: true }).waitFor();
  await page.screenshot({ path: out + "/install-preview-mobile.png" });
  await dialog.getByRole("button", { name: "确认安装", exact: true }).click();
  await dialog
    .getByRole("heading", { name: "远程导演方法", exact: true, level: 2 })
    .waitFor();
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  assert.equal(
    (await call("get", root + "/skills")).find((s) => s.name === "远程导演方法")
      .source_metadata.commit,
    "a".repeat(40),
  );
  checks.push(
    "Controlled GitHub preview passes source options and persists pinned commit; mobile light installation remains usable",
  );
  await page
    .getByRole("button", { name: "安装技能", exact: true })
    .first()
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "从空白创建技能", exact: true })
    .click();
  await page.getByLabel("技能名称", { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/install-report.json",
    JSON.stringify(
      { passed: true, project: project.id, checks, errors },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ passed: true, checks }));
} catch (e) {
  await page.screenshot({ path: out + "/install-failure.png", fullPage: true });
  throw e;
} finally {
  await browser.close();
}
