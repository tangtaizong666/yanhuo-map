import { expect, test } from "@playwright/test";
import { assertNoHorizontalOverflow, login, mutate } from "./helpers";

test("student login returns to the requested checkout", async ({ page }) => {
  await page.goto("/checkout/1");
  await expect(page).toHaveURL(/\/login\?returnTo=/);
  await login(page, "student", "demo12345");
  await expect(page).toHaveURL(/\/checkout\/1$/);
  await expect(page.locator(".site-header")).toBeVisible();
});

test("student identity and demo shortcuts preserve the checkout return destination", async ({
  page,
}) => {
  await page.goto("/login?returnTo=/checkout/1");
  await page.getByRole("button", { name: "学生端", exact: true }).click();
  await expect(page).toHaveURL(/returnTo=(?:%2F|\/)checkout(?:%2F|\/)1/);
  await page.getByRole("button", { name: "填入学生账号", exact: true }).click();
  await expect(page).toHaveURL(/returnTo=(?:%2F|\/)checkout(?:%2F|\/)1/);
  await expect(
    page.getByRole("textbox", { name: "账号", exact: true }),
  ).toHaveValue("student");
  await page
    .getByRole("button", { name: "登录，继续探索", exact: true })
    .click();
  await expect(page).toHaveURL(/\/checkout\/1$/);
});

test("merchant account logs into its workbench from the shared login and keeps that default", async ({
  page,
}) => {
  await page.goto("/login");
  await login(page, "vendor", "demo12345");
  await expect(page).toHaveURL(/\/merchant$/);
  await expect(page.getByRole("heading", { name: "经营首页." })).toBeVisible();
  await expect(page.locator(".site-header")).toHaveCount(0);
  await expect(
    page.getByRole("navigation", { name: "底部导航", exact: true }),
  ).toHaveCount(0);
  await page.goto("/");
  await expect(page).toHaveURL(/\/merchant$/);
  await page.reload();
  await expect(page.getByRole("heading", { name: "经营首页." })).toBeVisible();
  await page
    .getByRole("link", { name: "预览学生端", exact: true })
    .filter({ visible: true })
    .click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator(".consumer-preview-bar")).toContainText(
    "正在预览学生端",
  );
  await page.reload();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator(".consumer-preview-bar")).toBeVisible();
  await page.getByRole("link", { name: "返回商家工作台", exact: true }).click();
  await page.goto("/");
  await expect(page).toHaveURL(/\/merchant$/);
});

test("authenticated merchant hard navigation and reload preserve workbench subsections", async ({
  page,
}) => {
  await page.goto("/login");
  await login(page, "vendor", "demo12345");
  for (const [section, heading] of [
    ["products", "商品管理."],
    ["orders", "订单处理."],
    ["store", "店铺设置."],
  ]) {
    await page.goto(`/merchant/${section}`);
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/merchant/${section}$`));
    await page.reload();
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/merchant/${section}$`));
  }
});

test("merchant identity has dedicated login shell, matching demo account and mobile layout", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "商家工作台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "登录商家工作台", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".site-header")).toHaveCount(0);
  await expect(page.locator(".site-footer")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "注册", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "填入学生账号", exact: true }),
  ).toHaveCount(0);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page.getByRole("button", { name: "填入商家账号", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "账号", exact: true }),
  ).toHaveValue("vendor");
  await page
    .getByRole("button", { name: "登录商家工作台", exact: true })
    .click();
  await expect(page).toHaveURL(/\/merchant$/);
  await page
    .getByRole("button", { name: "退出登录", exact: true })
    .filter({ visible: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "登录商家工作台", exact: true }),
  ).toBeVisible();
});

test("student keeps the requested account page, and another tab changing identity refreshes the shell", async ({
  page,
  context,
}) => {
  await page.goto("/login?returnTo=/me");
  await login(page, "student", "demo12345");
  await expect(page).toHaveURL(/\/me$/);
  await expect(page.locator(".site-header")).toBeVisible();
  await page.goto("/");
  const response = await mutate(context, "/auth/login", {
    username: "vendor",
    password: "demo12345",
  });
  expect(response.ok()).toBeTruthy();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page).toHaveURL(/\/merchant$/);
  await expect(page.getByRole("heading", { name: "经营首页." })).toBeVisible();
});
