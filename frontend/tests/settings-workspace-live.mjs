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
  errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => d.accept());
try {
  const login = await context.request.post(`${base}/api/v1/auth/login`, {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(login.ok());
  const token = (await login.json()).access_token,
    headers = { Authorization: `Bearer ${token}` };
  const call = async (method, path, data) => {
    const r = await context.request[method](`${base}/api/v1${path}`, {
      headers,
      data,
    });
    assert.ok(r.ok(), `${method} ${path} ${r.status()}`);
    return r.json();
  };
  const project = await call("post", "/projects", {
      name: "设置分区与技能入口验收 " + Date.now(),
    }),
    root = `/projects/${project.id}`;
  for (const name of ["镜头连续性", "人物视觉整理", "对白节奏"])
    await call("post", root + "/skills", {
      name,
      description: "验证技能卡片与编辑、草稿保持",
      instructions: "先读取当前版本，再提出修改。",
      source: "验收",
      required_tools: ["object.read"],
    });
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    localStorage.setItem("inspiration_lang", "zh");
    if (!localStorage.getItem("inspiration_theme"))
      localStorage.setItem("inspiration_theme", "dark");
  }, token);
  await page.goto(`${base}${root}/settings`);
  const nav = page.getByRole("navigation", { name: "设置分类" });
  await nav.waitFor();
  await page.getByRole("button", { name: "添加渠道", exact: true }).waitFor();
  await page.screenshot({ path: `${out}/settings-channels-dark.png` });
  await nav.getByRole("button", { name: /项目资料/ }).click();
  await page.getByRole("button", { name: "管理项目封面", exact: true }).click();
  await page
    .getByLabel("上传项目封面", { exact: true })
    .setInputFiles("frontend/public/images/studio-scenes.webp");
  await page.getByRole("button", { name: "保存封面", exact: true }).click();
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.ok((await call("get", root)).cover_blob_hash);
  await page.getByAltText("项目封面", { exact: true }).waitFor();
  await page.screenshot({ path: `${out}/settings-project-dark.png` });
  await nav.getByRole("button", { name: /项目配额/ }).click();
  await page.getByText("未设置", { exact: true }).waitFor();
  const quota = page.getByLabel("项目配额", { exact: true });
  await quota.fill("42.5");
  await page.getByRole("button", { name: "用户菜单", exact: true }).click();
  await page.getByRole("menuitem", { name: "外观与语言", exact: true }).click();
  await page.getByRole("dialog", { name: "账户与偏好" }).getByTitle("关闭", { exact: true }).click();
  assert.equal(await quota.inputValue(), "42.5");
  let fail = true;
  await page.route(`**/api/v1${root}/quota`, async (route) => {
    if (route.request().method() === "PUT") {
      await new Promise((r) => setTimeout(r, 450));
      if (fail) {
        fail = false;
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            error: { message: "验收：配额暂时无法保存" },
          }),
        });
      }
    }
    await route.continue();
  });
  await page.getByRole("button", { name: "保存配额", exact: true }).click();
  assert.ok(await quota.isDisabled());
  await page.getByText("验收：配额暂时无法保存", { exact: true }).waitFor();
  assert.equal(await quota.inputValue(), "42.5");
  await page.getByRole("button", { name: "保存配额", exact: true }).click();
  await page.getByText("配额已保存", { exact: true }).waitFor();
  assert.equal((await call("get", root + "/quota")).limit_cost, 42.5);
  await page.reload();
  await quota.waitFor();
  assert.equal(await quota.inputValue(), "42.5");
  await nav.getByRole("button", { name: /外部 AI 连接/ }).click();
  await page.getByText("查看连接配置示例", { exact: true }).click();
  const config = await page.locator(".settings-config pre").textContent();
  assert.ok(config.includes(project.id));
  assert.ok(!config.includes(token));
  assert.ok(config.includes("INSPIRATION_TOKEN"));
  await page.screenshot({ path: `${out}/settings-connections-dark.png` });
  await page.getByRole("button", { name: "用户菜单", exact: true }).click();
  await page.getByRole("menuitem", { name: "外观与语言", exact: true }).click();
  await page.getByRole("button", { name: "浅色", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "浅色", exact: true }).waitFor();
  assert.equal(
    await page
      .getByRole("button", { name: "浅色", exact: true })
      .getAttribute("aria-pressed"),
    "true",
  );
  await page.screenshot({ path: `${out}/settings-preferences-light.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .locator("#main-content")
      .evaluate((e) => e.scrollWidth <= e.clientWidth + 1),
  );
  await page.screenshot({ path: `${out}/settings-mobile.png` });
  await page.goto(`${base}${root}/skills`);
  await page.getByRole("button", { name: "查看技能 镜头连续性", exact: true }).waitFor();
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.screenshot({ path: `${out}/skills-library-light.png` });
  await page.getByRole("button", { name: "编辑技能 镜头连续性", exact: true }).click();
  await page
    .getByLabel("方法与步骤", { exact: true })
    .fill("从设置迁移后仍保留技能草稿");
  await page.getByRole("dialog", { name: /编辑技能/ }).getByRole("button", { name: "关闭", exact: true }).click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  assert.equal(
    await page.getByLabel("方法与步骤", { exact: true }).inputValue(),
    "从设置迁移后仍保留技能草稿",
  );
  await page.goto(`${base}${root}/settings?section=connections`);
  await nav.waitFor();
  assert.match(page.url(), /section=connections/);
  await page.goBack();
  await page
    .getByText("已恢复本机未保存的技能草稿，核对后即可继续保存。", {
      exact: true,
    })
    .waitFor();
  assert.equal(
    await page.getByLabel("方法与步骤", { exact: true }).inputValue(),
    "从设置迁移后仍保留技能草稿",
  );
  await page.getByRole("dialog", { name: /编辑技能/ }).getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "确定", exact: true }).click();
  await page.getByRole("link", { name: "外部 AI 连接 ↗", exact: true }).click();
  await nav.waitFor();
  await page.goBack();
  await page.getByRole("link", { name: "导入外部目录 ↗", exact: true }).click();
  await page
    .getByLabel("选择外部目录文件", { exact: true })
    .setInputFiles({
      name: "assets.json",
      mimeType: "application/json",
      buffer: Buffer.from(
        JSON.stringify({
          source: "外部方法库资料",
          items: [{ name: "迁移入口的海岸", type: "location" }],
        }),
      ),
    });
  await page
    .getByRole("button", { name: "导入 1 项资产信息", exact: true })
    .click();
  await page.getByText(/已导入 1 项资产并保留来源信息/).waitFor();
  await page.getByRole("link", { name: "进入资产库", exact: true }).click();
  await page.getByText("迁移入口的海岸", { exact: true }).waitFor();
  assert.equal((await call("get", root + "/assets")).length, 1);
  await page.goto(`${base}${root}/settings?section=quota`);
  await quota.waitFor();
  await page.route("**/api/v1/me", async (route) => {
    const r = await route.fetch();
    const data = await r.json();
    data.memberships = data.memberships.map((m) =>
      m.project_id === project.id ? { ...m, role: "writer" } : m,
    );
    await route.fulfill({ response: r, json: data });
  });
  await page.reload();
  await page
    .getByText("只有项目管理员可以修改额度。", { exact: true })
    .waitFor();
  assert.equal(
    await page.getByRole("button", { name: "保存配额", exact: true }).count(),
    0,
  );
  await nav.getByRole("button", { name: /渠道与模型/ }).click();
  await page.getByText(/渠道、密钥和模型由项目管理员管理/).waitFor();
  assert.equal(
    await page.getByRole("button", { name: "添加渠道", exact: true }).count(),
    0,
  );
  assert.deepEqual(errors, []);
  await writeFile(
    `${out}/report.json`,
    JSON.stringify(
      {
        passed: true,
        project: project.id,
        checks: [
          "Section URL/reload and browser preferences",
          "Real cover upload and quota save/retry/input locking",
          "Quota draft retained across sections",
          "MCP example includes project and no login token",
          "Skill gallery and draft retained across new routes",
          "Asset import moved and real assets visible",
          "Read-only UI role with controlled me response; server permissions unchanged",
          "Dark/light/390px without overflow",
        ],
        errors,
      },
      null,
      2,
    ),
  );
  console.log("Settings and skill entry workflow passed");
} catch (e) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true });
  throw e;
} finally {
  await browser.close();
}
