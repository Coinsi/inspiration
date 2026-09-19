// Read-only project navigation; changes only this browser's preferences and session.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
const base = process.env.APP_URL,
  out = process.env.ARTIFACT_DIR,
  project = process.env.TEST_PROJECT_ID;
assert.ok(base && out && project);
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
  },
  shot = async (name) =>
    page.screenshot({
      path: out + "/" + name + ".png",
      animations: "disabled",
    });
try {
  const login = await context.request.post(base + "/api/v1/auth/login", {
    data: { username: "demo", password: "demo1234" },
  });
  assert.ok(login.ok());
  const token = (await login.json()).access_token;
  await context.addInitScript((t) => {
    localStorage.setItem("inspiration_token", t);
    if (!localStorage.getItem("inspiration_lang"))
      localStorage.setItem("inspiration_lang", "zh");
    if (!localStorage.getItem("inspiration_theme"))
      localStorage.setItem("inspiration_theme", "dark");
  }, token);
  const root = base + "/projects/" + project;
  await page.goto(root + "/workbench");
  await page.getByRole("button", { name: "用户菜单", exact: true }).waitFor();
  const nav = page.getByRole("navigation", { name: "工作区功能", exact: true });
  for (const name of ["设置", "成员", "任务中心"])
    assert.equal(await nav.getByRole("link", { name, exact: true }).count(), 0);
  assert.equal(
    await page.locator("aside").getByText("Demo 用户", { exact: true }).count(),
    0,
  );
  await nav.getByRole("button", { name: "展开创作工具", exact: true }).click();
  await nav.getByRole("link", { name: "技能库", exact: true }).waitFor();
  await nav.getByRole("link", { name: "提示词", exact: true }).waitFor();
  const account = page.getByRole("button", { name: "用户菜单", exact: true });
  await account.focus();
  await page.keyboard.press("ArrowDown");
  const menu = page.getByRole("menu", { name: "用户菜单", exact: true });
  await menu.waitFor();
  await page.waitForFunction(
    () => document.activeElement?.textContent === "账户信息",
  );
  await page.keyboard.press("End");
  await page.waitForFunction(
    () => document.activeElement?.textContent === "退出登录",
  );
  await page.keyboard.press("Escape");
  assert.equal(await account.getAttribute("aria-expanded"), "false");
  assert.ok(await account.evaluate((e) => e === document.activeElement));
  await account.click();
  await shot("account-menu-dark");
  await menu.getByRole("menuitem", { name: "账户信息", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "账户与偏好", exact: true });
  await dialog.waitFor();
  await dialog.getByText("Demo 用户", { exact: true }).first().waitFor();
  await shot("account-info");
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  assert.ok(await account.evaluate((e) => e === document.activeElement));
  await account.click();
  await menu.getByRole("menuitem", { name: "外观与语言", exact: true }).click();
  await dialog.getByRole("button", { name: "浅色", exact: true }).click();
  assert.equal(
    await page.evaluate(() => localStorage.getItem("inspiration_theme")),
    "light",
  );
  await dialog.getByLabel("界面语言", { exact: true }).selectOption("en");
  await page.getByRole("dialog", { name: "Account & preferences" }).waitFor();
  await page.getByLabel("Language", { exact: true }).selectOption("zh");
  await page.keyboard.press("Escape");
  await page.reload();
  await account.waitFor();
  assert.equal(
    await page.evaluate(() =>
      document.documentElement.classList.contains("light"),
    ),
    true,
  );
  check(
    "Account menu moved to header; keyboard and focus return work; theme/language persist without changing project",
  );
  const projectButton = page.getByRole("button", {
    name: "项目菜单",
    exact: true,
  });
  await projectButton.click();
  await shot("project-menu-light");
  await page
    .getByRole("menu", { name: "项目菜单", exact: true })
    .getByRole("menuitem", { name: "项目资料与封面", exact: true })
    .click();
  await page.getByRole("heading", { name: "项目设置", exact: true }).waitFor();
  assert.ok(page.url().includes("section=project"));
  assert.equal(
    await page
      .getByRole("navigation", { name: "设置分类" })
      .getByRole("button", { name: /外观与语言/ })
      .count(),
    0,
  );
  await projectButton.click();
  await page.getByRole("menuitem", { name: "创作默认项", exact: true }).click();
  await page.waitForURL("**/settings?section=creative");
  await projectButton.click();
  await page.getByRole("menuitem", { name: "渠道与模型", exact: true }).click();
  await page.waitForURL("**/settings?section=channels");
  await projectButton.click();
  await page.getByRole("menuitem", { name: "成员与权限", exact: true }).click();
  await page.waitForURL("**/members");
  await page
    .locator("header")
    .getByRole("link", { name: "任务中心", exact: true })
    .click();
  await page.waitForURL("**/tasks");
  await page.goto(root + "/settings?section=appearance");
  await dialog.waitFor();
  assert.ok(page.url().includes("account=preferences"));
  await page.keyboard.press("Escape");
  assert.ok(!page.url().includes("account="));
  check(
    "Project settings, defaults, channels, members and task center remain reachable; old appearance link opens account preferences",
  );
  await page.goto(root + "/canvas");
  await account.waitFor();
  await projectButton.click();
  await page.getByRole("menu", { name: "项目菜单", exact: true }).waitFor();
  await page.keyboard.press("Escape");
  await shot("canvas-header");
  await page.goto(root + "/workbench");
  await page.setViewportSize({ width: 390, height: 844 });
  await account.click();
  await shot("account-mobile");
  let box = await menu.boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 390);
  await page.keyboard.press("Escape");
  await projectButton.click();
  const pm = page.getByRole("menu", { name: "项目菜单", exact: true });
  await pm.waitFor();
  box = await pm.boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 390);
  await shot("project-mobile");
  await page
    .getByRole("menuitem", { name: "项目资料与封面", exact: true })
    .click();
  await page.getByRole("heading", { name: "项目设置", exact: true }).waitFor();
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await page.getByRole("dialog", { name: "项目导航" }).waitFor();
  await page.keyboard.press("Escape");
  await page.goto(base + "/projects");
  await account.waitFor();
  await account.click();
  await menu.getByRole("menuitem", { name: "外观与语言", exact: true }).click();
  await dialog.waitFor();
  await shot("home-preferences-mobile");
  await page.keyboard.press("Escape");
  await account.click();
  await page.locator("h1").click();
  assert.equal(await account.getAttribute("aria-expanded"), "false");
  await account.click();
  await menu.getByRole("menuitem", { name: "退出登录", exact: true }).click();
  await page.waitForURL("**/login");
  assert.equal(
    await page.evaluate(() => localStorage.getItem("inspiration_token")),
    null,
  );
  check(
    "Desktop, immersive and 390px menus fit; mobile navigation, home preferences, outside close and sign-out work",
  );
  assert.deepEqual(errors, []);
  await writeFile(
    out + "/report.json",
    JSON.stringify({ passed: true, checks, errors }, null, 2),
  );
} catch (e) {
  await shot("failure");
  throw e;
} finally {
  await browser.close();
}
