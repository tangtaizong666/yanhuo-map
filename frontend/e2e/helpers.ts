import {
  expect,
  type BrowserContext,
  type Page,
  type Route,
} from "@playwright/test";

export const baseURL = process.env.E2E_BASE_URL || "http://127.0.0.1:5183";

// Isolated UI fixtures must model the cookie that Django sets before a write.
export async function fulfillCsrf(route: Route) {
  // WebKit's intercepted response may omit Set-Cookie under the test runner.
  // Establish the same readable, same-origin cookie before resolving the mock;
  // production still requires Django's response cookie before every mutation.
  await route
    .request()
    .frame()
    .page()
    .context()
    .addCookies([
      {
        name: "csrftoken",
        value: "fixture-csrf-token",
        url: new URL(route.request().url()).origin,
        sameSite: "Lax",
      },
    ]);
  // Some WebKit intercepts acknowledge the cookie jar update before exposing
  // it to the document. Model only a successful same-origin CSRF response;
  // failure fixtures never call this helper and still block the write.
  await route
    .request()
    .frame()
    .evaluate(() => {
      document.cookie = "csrftoken=fixture-csrf-token; Path=/; SameSite=Lax";
      if (!document.cookie.split("; ").includes("csrftoken=fixture-csrf-token"))
        throw new Error(
          "Successful CSRF fixture did not establish a readable cookie",
        );
    });
  return route.fulfill({
    json: { csrfToken: "fixture-csrf-token" },
    headers: {
      "Set-Cookie": "csrftoken=fixture-csrf-token; Path=/; SameSite=Lax",
    },
  });
}

export async function mutate(
  context: BrowserContext,
  path: string,
  data: unknown,
  method = "POST",
) {
  await context.request.get(`${baseURL}/api/v1/auth/csrf`);
  const csrf = (await context.cookies()).find(
    (cookie) => cookie.name === "csrftoken",
  );
  expect(csrf, "Django must set a real CSRF cookie").toBeTruthy();
  return context.request.fetch(`${baseURL}/api/v1${path}`, {
    method,
    data,
    headers: { "X-CSRFToken": csrf!.value, Origin: baseURL },
  });
}

export async function createStudent(context: BrowserContext) {
  const config = await (
    await context.request.get(`${baseURL}/api/v1/config`)
  ).json();
  expect(
    config.demo_mode,
    "Mutating browser tests require a DEMO_MODE server",
  ).toBe(true);
  const username = `e2e_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
  const password = "CampusE2E!2026";
  const registered = await mutate(context, "/auth/register", {
    username,
    password,
    display_name: "自动化测试同学",
  });
  expect(registered.status(), await registered.text()).toBe(201);
  const loggedOut = await mutate(context, "/auth/logout", {});
  expect(loggedOut.ok()).toBeTruthy();
  return { username, password };
}

// Live integration setup must use the same versioned correction contract as a merchant.
export async function correctStock(
  context: BrowserContext,
  productId: number,
  stock: number,
) {
  const response = await context.request.get(
    `${baseURL}/api/v1/merchant/stalls`,
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const stalls = await response.json();
  const product = stalls
    .flatMap((stall: any) => stall.products)
    .find((item: any) => item.id === productId);
  expect(product, "Stock correction requires an owned product").toBeTruthy();
  expect(
    Number.isInteger(product.stock_version),
    "Server must support versioned stock correction",
  ).toBe(true);
  return mutate(context, `/merchant/products/${productId}/stock-correction`, {
    stock,
    expected_stock_version: product.stock_version,
    idempotency_key: crypto.randomUUID(),
    reason: "隔离自动化测试准备线上可售余量",
  });
}

export async function login(page: Page, username: string, password: string) {
  await page.getByRole("textbox", { name: "账号", exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  const authenticated = page.waitForResponse(
    (response) =>
      /\/api\/v1\/auth\/login$/.test(response.url()) &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: /^(登录，继续探索|登录商家工作台)$/ })
    .click();
  const result = await authenticated;
  expect(result.status(), await result.text()).toBe(200);
  await expect(page).not.toHaveURL((url) => url.pathname === "/login");
}

export async function assertNoHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    ),
  ).toBeLessThanOrEqual(1);
}
