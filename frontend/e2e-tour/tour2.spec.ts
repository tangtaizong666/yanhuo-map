import { test, devices, type Page } from "@playwright/test";

test.use({ ...devices["iPhone 13"], browserName: "chromium" });

async function view(page: Page, name: string) {
  await page.waitForTimeout(900);
  await page.screenshot({ path: `tour-shots/${name}.png` });
}

async function signIn(page: Page, user: string, merchant = false) {
  await page.goto(merchant ? "/login?role=merchant&returnTo=/merchant" : "/login");
  await page.getByRole("textbox", { name: "账号", exact: true }).fill(user);
  await page.getByLabel("密码", { exact: true }).fill("demo12345");
  await page.getByRole("button", { name: merchant ? "登录商家工作台" : "登录，继续探索" }).click();
  await page.waitForURL((u) => u.pathname !== "/login");
}

test("viewport shots", async ({ page }) => {
  await signIn(page, "student");
  await page.goto("/stalls/2");
  await view(page, "20-stall2-top");
  await page.mouse.wheel(0, 900);
  await view(page, "21-stall2-scrolled");
  await page.goto("/stalls/2/products/" + (await page.evaluate(async () => (await (await fetch("/api/v1/stalls/2")).json()).products?.[0]?.id)));
  await view(page, "22-product");
  await page.goto("/orders");
  await view(page, "23-orders");
});

test("merchant viewport shots", async ({ page }) => {
  await signIn(page, "vendor", true);
  await view(page, "30-merchant-orders");
  await page.goto("/merchant/products");
  await view(page, "31-merchant-products");
  await page.mouse.wheel(0, 700);
  await view(page, "32-merchant-products-scrolled");
});
