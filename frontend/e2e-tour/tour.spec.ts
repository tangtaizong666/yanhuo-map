import { test, devices, type Page } from "@playwright/test";

// Manual mobile QA tour: screenshots + console errors + overflow per page.
test.use({ ...devices["iPhone 13"], browserName: "chromium" });

const out = (name: string) => `tour-shots/${name}.png`;
const problems: string[] = [];

async function snap(page: Page, name: string) {
  await page.waitForTimeout(900);
  await page.screenshot({ path: out(name), fullPage: true });
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - innerWidth,
  );
  if (overflow > 1) problems.push(`${name}: horizontal overflow ${overflow}px`);
  const small = await page.evaluate(() =>
    [...document.querySelectorAll("button,a,input,select")]
      .filter((e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (r.height < 32 || r.width < 32) && getComputedStyle(e).visibility !== "hidden";
      })
      .map((e) => `${e.tagName}:${(e.textContent || (e as HTMLInputElement).placeholder || e.getAttribute("aria-label") || "").trim().slice(0, 18)}(${Math.round(e.getBoundingClientRect().width)}x${Math.round(e.getBoundingClientRect().height)})`)
      .slice(0, 12),
  );
  if (small.length) problems.push(`${name}: small targets ${small.join(" | ")}`);
}

test("guest + student tour", async ({ page }) => {
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text().slice(0, 160)}`));
  page.on("response", (r) => r.status() >= 400 && problems.push(`http ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`));
  await page.goto("/");
  await snap(page, "01-home-guest");
  await page.goto("/map");
  await snap(page, "02-map");
  await page.goto("/stalls/1");
  await snap(page, "03-stall");
  await page.goto("/login");
  await snap(page, "04-login");
  await page.getByRole("textbox", { name: "账号", exact: true }).fill("student");
  await page.getByLabel("密码", { exact: true }).fill("demo12345");
  await page.getByRole("button", { name: "登录，继续探索" }).click();
  await page.waitForURL((u) => u.pathname !== "/login");
  await snap(page, "05-home-student");
  await page.goto("/orders");
  await snap(page, "06-orders");
  await page.goto("/me");
  await snap(page, "07-me");
  await page.goto("/cart");
  await snap(page, "08-cart");
  console.log("PROBLEMS\n" + problems.join("\n"));
});

test("merchant tour", async ({ page }) => {
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text().slice(0, 160)}`));
  page.on("response", (r) => r.status() >= 400 && problems.push(`http ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`));
  await page.goto("/merchant");
  await snap(page, "10-merchant-guest");
  await page.goto("/login?role=merchant&returnTo=/merchant");
  await snap(page, "11-merchant-login");
  await page.getByRole("textbox", { name: "账号", exact: true }).fill("vendor");
  await page.getByLabel("密码", { exact: true }).fill("demo12345");
  await page.getByRole("button", { name: "登录商家工作台" }).click();
  await page.waitForURL((u) => u.pathname !== "/login");
  await snap(page, "12-merchant-home");
  for (const s of ["products", "more", "store", "analytics", "reviews"]) {
    await page.goto(`/merchant/${s}`);
    await snap(page, `13-merchant-${s}`);
  }
  console.log("PROBLEMS\n" + problems.join("\n"));
});
