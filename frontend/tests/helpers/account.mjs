/** Exercise the public account menu rather than editing theme storage. */
export async function toggleAccountTheme(page) {
  const light = await page.evaluate(() =>
    document.documentElement.classList.contains("light"),
  );
  await page.getByRole("button", { name: "用户菜单", exact: true }).click();
  await page.getByRole("menuitem", { name: "外观与语言", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "账户与偏好", exact: true });
  await dialog
    .getByRole("button", { name: light ? "深色" : "浅色", exact: true })
    .click();
  await dialog.getByTitle("关闭", { exact: true }).click();
}
