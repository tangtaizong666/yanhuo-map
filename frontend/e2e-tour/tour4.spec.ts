import { test, devices, type Page } from "@playwright/test";

test.use({ ...devices["iPhone 13"], browserName: "chromium" });

async function smallInputs(page: Page, name: string) {
  await page.waitForTimeout(900);
  const rows = await page.evaluate(() =>
    [...document.querySelectorAll("input,select,textarea")]
      .filter((e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (e as HTMLInputElement).type !== "hidden" && (e as HTMLInputElement).type !== "file";
      })
      .map((e) => ({ fs: parseFloat(getComputedStyle(e).fontSize), label: e.getAttribute("aria-label") || (e as HTMLInputElement).placeholder || e.closest("label")?.textContent?.trim().slice(0, 12) || e.tagName }))
      .filter((r) => r.fs < 16),
  );
  if (rows.length) console.log(`${name}: ` + rows.map((r) => `${r.label}=${r.fs}px`).join(" | "));
}

test("form controls below 16px trigger iOS zoom", async ({ page }) => {
  for (const p of ["/", "/search?q=面", "/map", "/stalls/1", "/login", "/login?role=merchant&returnTo=/merchant"]) {
    await page.goto(p);
    await smallInputs(page, p);
  }
  await page.getByRole("textbox", { name: "账号", exact: true }).fill("vendor");
  await page.getByLabel("密码", { exact: true }).fill("demo12345");
  await page.getByRole("button", { name: "登录商家工作台" }).click();
  await page.waitForURL((u) => u.pathname !== "/login");
  for (const p of ["/merchant", "/merchant/products", "/merchant/store", "/merchant/orders?filter=all", "/merchant/reviews"]) {
    await page.goto(p);
    await smallInputs(page, p);
  }
  await page.goto("/merchant/products");
  await page.getByRole("button", { name: /添加商品/ }).first().click();
  await smallInputs(page, "product-editor");
});
