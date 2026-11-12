import { test, devices, type Page } from "@playwright/test";

test.use({ ...devices["iPhone 13"], browserName: "chromium" });
const shot = (page: Page, n: string) => page.screenshot({ path: `tour-shots/${n}.png` });

test("merchant entry edge paths", async ({ page }) => {
  // 1. guest opens merchant workspace, follows the onboarding CTA, logs in with demo merchant
  await page.goto("/merchant");
  await page.getByRole("link", { name: /已有账号登录/ }).click();
  await page.waitForURL(/\/login/);
  await shot(page, "40-cta-login");
  console.log("CTA login url:", page.url());
  await page.getByRole("textbox", { name: "账号", exact: true }).fill("vendor");
  await page.getByLabel("密码", { exact: true }).fill("demo12345");
  await page.locator("form button[type=submit], form .submit-button").first().click();
  await page.waitForTimeout(1500);
  console.log("after CTA login url:", page.url());
  await shot(page, "41-after-cta-login");
  await page.goto("/merchant/apply");
  await page.waitForTimeout(1200);
  console.log("merchant at /merchant/apply:", page.url());
  await shot(page, "42-merchant-apply");
  // 2. log out and try registering an existing username
  await page.request.post("/api/v1/auth/logout").catch(() => {});
  await page.context().clearCookies();
  await page.goto("/login?role=merchant&returnTo=/merchant");
  await page.getByRole("button", { name: "注册并申请" }).click();
  await page.getByRole("textbox", { name: "账号", exact: true }).fill("vendor");
  await page.getByLabel("昵称").fill("老李");
  await page.getByLabel("密码", { exact: true }).fill("demo12345");
  await page.getByLabel("确认密码").fill("demo12345");
  await page.getByRole("button", { name: "创建账号" }).click();
  await page.waitForTimeout(1200);
  console.log("register-existing error:", await page.locator(".error-message").allTextContents());
  await shot(page, "43-register-existing");
});
