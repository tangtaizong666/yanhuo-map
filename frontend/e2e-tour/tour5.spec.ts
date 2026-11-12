import { test, devices, type Page, type BrowserContext } from "@playwright/test";

const phone = { ...devices["iPhone 13"], browserName: "chromium" as const };
const shot = (page: Page, n: string) => page.screenshot({ path: `tour-shots/${n}.png` });
const problems: string[] = [];

function watch(page: Page, who: string) {
  page.on("console", (m) => m.type() === "error" && problems.push(`${who} console: ${m.text().slice(0, 160)}`));
  page.on("response", (r) => r.status() >= 500 && problems.push(`${who} http ${r.status()} ${new URL(r.url()).pathname}`));
}

async function signIn(page: Page, user: string, merchant: boolean) {
  await page.goto(merchant ? "/login?role=merchant&returnTo=/merchant" : "/login");
  await page.getByRole("textbox", { name: "账号", exact: true }).fill(user);
  await page.getByLabel("密码", { exact: true }).fill("demo12345");
  await page.getByRole("button", { name: merchant ? "登录商家工作台" : "登录，继续探索" }).click();
  await page.waitForURL((u) => u.pathname !== "/login");
}

test("full pickup order loop on two phones", async ({ browser }) => {
  test.setTimeout(240_000);
  const mctx: BrowserContext = await browser.newContext(phone);
  const sctx: BrowserContext = await browser.newContext(phone);
  const m = await mctx.newPage(), s = await sctx.newPage();
  watch(m, "merchant"); watch(s, "student");

  // Merchant reopens: confirm the stall is still here.
  await signIn(m, "vendor", true);
  const confirm = m.getByRole("button", { name: /确认仍在这里|开摊|开始营业/ }).first();
  console.log("merchant primary:", await confirm.textContent({ timeout: 2000 }).catch(() => "none"));
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
  await m.waitForTimeout(1500);
  const dialog = m.getByRole("button", { name: /确认|开摊/ }).last();
  if (await dialog.isVisible().catch(() => false)) { await dialog.click(); await m.waitForTimeout(1200); }
  await shot(m, "50-merchant-open");
  console.log("merchant status:", (await m.locator(".m-content").innerText()).slice(0, 200).replace(/\n/g, " / "));

  // Student orders.
  const resume = m.getByRole("button", { name: "恢复接单" });
  if (await resume.isVisible().catch(() => false)) { await resume.click(); await m.waitForTimeout(1200); }
  await signIn(s, "student", false);
  await s.goto("/stalls/1");
  await s.waitForTimeout(1200);
  await shot(s, "51-student-stall");
  const add = s.getByRole("button", { name: /^加入|添加|^\+|加一份/ }).first();
  console.log("add button:", await add.getAttribute("aria-label").catch(() => null), await add.isEnabled().catch(() => "missing"));
  await add.click();
  await s.waitForTimeout(800);
  await shot(s, "52-student-added");
  const go = s.getByRole("link", { name: /去结算|结算/ }).or(s.getByRole("button", { name: /去结算|结算/ })).first();
  await go.click();
  await s.waitForURL(/checkout/);
  await s.waitForTimeout(1200);
  await shot(s, "53-checkout");
  const submit = s.getByRole("button", { name: /提交订单|确认下单|提交/ }).last();
  console.log("submit:", await submit.textContent());
  const resp = s.waitForResponse((r) => r.url().endsWith("/api/v1/orders") && r.request().method() === "POST", { timeout: 15000 }).catch(() => null);
  await submit.click();
  const r = await resp;
  console.log("POST /orders:", r ? r.status() : "no request", r ? (await r.text()).slice(0, 300) : "");
  console.log("alerts:", await s.locator("[role=alert], .error-message, .checkout-issue").allTextContents());
  await s.waitForURL(/\/orders\//, { timeout: 15000 }).catch(() => {});
  await s.waitForTimeout(1500);
  await shot(s, "54-order");
  console.log("student order url:", s.url());
  console.log("student order text:", (await s.locator("main, .page").first().innerText()).slice(0, 400).replace(/\n/g, " / "));

  // Merchant handles it.
  await m.goto("/merchant");
  await m.waitForTimeout(2000);
  await shot(m, "55-merchant-new");
  for (const [label, stage] of [[/接单开始做/, /新订单/], [/做好了/, /制作中/]] as const) {
    await m.getByRole("button", { name: stage }).first().click().catch(() => {});
    await m.waitForTimeout(800);
    const b = m.getByRole("button", { name: label }).first();
    console.log("merchant click:", await b.textContent({ timeout: 3000 }).catch(() => "missing"));
    await b.click({ timeout: 5000 }).catch((e) => problems.push(`merchant click ${label}: ${e.message.slice(0, 80)}`));
    await m.waitForTimeout(1200);
    const ok = m.getByRole("button", { name: /^确认|接单开始做/ }).last();
    if (await ok.isVisible().catch(() => false) && (await ok.textContent())?.includes("确认")) { await ok.click(); await m.waitForTimeout(1200); }
  }
  await shot(m, "56-merchant-ready");
  await s.reload(); await s.waitForTimeout(1500);
  await shot(s, "57-student-ready");
  console.log("student ready text:", (await s.locator("main, .page").first().innerText()).slice(0, 300).replace(/\n/g, " / "));
  console.log("PROBLEMS\n" + problems.join("\n"));
});
