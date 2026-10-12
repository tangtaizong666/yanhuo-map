import {
  expect,
  request,
  test,
  type APIRequestContext,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { baseURL, createStudent, mutate, requireIsolatedApi } from "./helpers";

const noteSelector = "例如：餐具按需提供（每份口味请在上方分别填写）";
const phoneSelector = "如遇缺货，方便商家联系你";

test.skip(
  process.env.E2E_ISOLATED !== "1",
  "Requires an isolated demo server and E2E_ISOLATED=1",
);

let cleanupFixture: (() => Promise<void>) | undefined;
test.afterEach(async () => {
  // Hooks receive their own teardown timeout, even when a page action timed out.
  const cleanup = cleanupFixture;
  cleanupFixture = undefined;
  await cleanup?.();
});

async function cleanupMutation(
  api: APIRequestContext,
  path: string,
  data: unknown,
  method = "POST",
) {
  requireIsolatedApi();
  const csrf = await api.get("/api/v1/auth/csrf");
  expect(csrf.ok(), await csrf.text()).toBeTruthy();
  return api.fetch(`/api/v1${path}`, {
    method,
    data,
    headers: { "X-CSRFToken": (await csrf.json()).csrfToken, Origin: baseURL },
  });
}

async function expandContact(page: Page) {
  const details = page.locator('details[aria-label="联系与备注"]');
  if ((await details.getAttribute("open")) === null) {
    await details.locator("summary").click();
  }
}

async function fixture(browser: Browser, context: BrowserContext) {
  const first = await createStudent(context);
  const second = await createStudent(context);
  const loggedIn = await mutate(context, "/auth/login", first);
  expect(loggedIn.ok(), await loggedIn.text()).toBeTruthy();
  const firstUser = await loggedIn.json();
  const merchant = await browser.newContext({ baseURL });
  const merchantLogin = await mutate(merchant, "/auth/login", {
    username: "vendor",
    password: "demo12345",
  });
  expect(merchantLogin.ok(), await merchantLogin.text()).toBeTruthy();
  const stalls = await (
    await merchant.request.get(`${baseURL}/api/v1/merchant/stalls`)
  ).json();
  const stall = stalls.find((item: any) => item.transaction_enabled);
  expect(stall).toBeTruthy();
  const opened = await mutate(merchant, `/merchant/stalls/${stall.id}/status`, {
    status: "open",
    confirm_location: true,
    closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const created = await mutate(
    merchant,
    `/merchant/stalls/${stall.id}/products`,
    {
      name: `结算隔离验证餐点-${Date.now()}`,
      price_cents: 1000,
      stock: 30,
      image:
        stall.products.find((item: any) => item.image)?.image || stall.image,
      category: "测试餐点",
      description: "自动化验证专用，测试结束后下架。",
    },
  );
  expect(created.status(), await created.text()).toBe(201);
  const product = await created.json();
  async function start(page: Page) {
    await page.goto(`/stalls/${stall.id}`);
    await page
      .getByRole("button", { name: `添加${product.name}`, exact: true })
      .click();
    await page.getByRole("link", { name: "去结算", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/checkout/${stall.id}$`));
    await expandContact(page);
    await page.getByPlaceholder(noteSelector).fill("账号 A 的口味备注");
    await page.getByPlaceholder(phoneSelector).fill("13800000001");
  }
  async function cleanup() {
    // An independent API context survives the test page/browser-context closing.
    const api = await request.newContext({ baseURL, timeout: 10_000 });
    const errors: unknown[] = [];
    const attempt = async (action: () => Promise<void>) => {
      try {
        await action();
      } catch (error) {
        errors.push(error);
      }
    };
    const loginMerchant = async () => {
      const login = await cleanupMutation(api, "/auth/login", {
        username: "vendor",
        password: "demo12345",
      });
      expect(login.ok(), await login.text()).toBeTruthy();
    };
    try {
      // Hide only our fixture first; an account/order cleanup failure must not
      // leave the synthetic dish visible or prevent the remaining cleanup.
      await attempt(async () => {
        await loginMerchant();
        const hidden = await cleanupMutation(
          api,
          `/merchant/products/${product.id}`,
          { is_active: false },
          "PATCH",
        );
        expect(hidden.ok(), await hidden.text()).toBeTruthy();
      });
      for (const student of [first, second]) {
        await attempt(async () => {
          const login = await cleanupMutation(api, "/auth/login", student);
          expect(login.ok(), await login.text()).toBeTruthy();
          const orders = await (await api.get("/api/v1/orders")).json();
          for (const order of orders) {
            if (
              order.status === "pending" &&
              order.items.every((item: any) => item.product_id === product.id)
            ) {
              const cancelled = await cleanupMutation(
                api,
                `/orders/${order.id}/cancel`,
                { reason: "结算隔离测试结束，恢复库存" },
              );
              expect(cancelled.ok(), await cancelled.text()).toBeTruthy();
            }
          }
        });
      }
      await attempt(async () => {
        await loginMerchant();
        const restored = await cleanupMutation(
          api,
          `/merchant/stalls/${stall.id}/status`,
          {
            status: stall.session_status || "closed",
            confirm_location: true,
            closes_at:
              stall.closes_at && Date.parse(stall.closes_at) > Date.now()
                ? stall.closes_at
                : null,
          },
        );
        expect(restored.ok(), await restored.text()).toBeTruthy();
      });
    } finally {
      await api.dispose();
      await merchant.close().catch(() => {});
    }
    if (errors.length)
      throw new AggregateError(errors, "Checkout fixture cleanup failed");
  }
  cleanupFixture = cleanup;
  return { first, second, firstUser, merchant, stall, product, start };
}

async function holdRealOrderResponse(page: Page) {
  let release!: () => void;
  let captured!: (value: { body: any; request: any; status: number }) => void;
  let delivered!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const upstream = new Promise<{ body: any; request: any; status: number }>(
    (resolve) => {
      captured = resolve;
    },
  );
  const completed = new Promise<void>((resolve) => {
    delivered = resolve;
  });
  await page.route(
    "**/api/v1/orders",
    async (route) => {
      // The real server processes this request before its response is held back.
      const response = await route.fetch();
      const body = await response.body();
      captured({
        body: JSON.parse(body.toString()),
        request: route.request().postDataJSON(),
        status: response.status(),
      });
      await gate;
      try {
        await route.fulfill({ response, body });
      } catch {
        /* The old page may have aborted its wait. */
      } finally {
        delivered();
      }
    },
    { times: 1 },
  );
  return { upstream, release, completed };
}

async function switchIdentity(
  page: Page,
  context: BrowserContext,
  account: { username: string; password: string },
  draft: { stallId: number; product: unknown },
) {
  const response = await mutate(context, "/auth/login", account);
  expect(response.ok(), await response.text()).toBeTruthy();
  const user = await response.json();
  // Each account owns its cart. Give B an explicit draft before the identity
  // refresh, keeping A's mounted request alive to exercise the late response.
  await page.evaluate(
    ({ userId, draft }) => {
      localStorage.setItem(
        `yanhuo-cart-v2:user:${userId}`,
        JSON.stringify({
          [draft.stallId]: [{ product: draft.product, quantity: 1 }],
        }),
      );
    },
    { userId: user.id, draft },
  );
  const identity = page.waitForResponse((result) =>
    result.url().endsWith("/auth/me"),
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  expect((await identity).ok()).toBeTruthy();
  await expandContact(page);
  await expect(page.getByPlaceholder(noteSelector)).toHaveValue("");
  return user;
}

for (const lateResponse of ["success", "price_changed"] as const) {
  test(`a late ${lateResponse} response from account A cannot clear or reprice account B's checkout`, async ({
    page,
    context,
    browser,
  }) => {
    const f = await fixture(browser, context);
    let held: Awaited<ReturnType<typeof holdRealOrderResponse>> | undefined;
    try {
      await f.start(page);
      if (lateResponse === "price_changed") {
        const changed = await mutate(
          f.merchant,
          `/merchant/products/${f.product.id}`,
          { price_cents: 1100 },
          "PATCH",
        );
        expect(changed.ok(), await changed.text()).toBeTruthy();
      }
      held = await holdRealOrderResponse(page);
      await page
        .getByRole("button", { name: "提交自取订单", exact: true })
        .click();
      const original = await held.upstream;
      expect(original.status).toBe(lateResponse === "success" ? 201 : 409);
      if (lateResponse === "price_changed")
        expect(original.body.code).toBe("price_changed");
      const firstKey = `yanhuo-checkout-${f.firstUser.id}-${f.stall.id}`;
      const firstRecord = await page.evaluate(
        (key) => sessionStorage.getItem(key),
        firstKey,
      );
      expect(JSON.parse(firstRecord!).key).toBe(
        original.request.idempotency_key,
      );

      const secondUser = await switchIdentity(page, context, f.second, {
        stallId: f.stall.id,
        product: f.product,
      });
      const secondCartKey = `yanhuo-cart-v2:user:${secondUser.id}`;
      await page
        .getByRole("button", { name: `增加${f.product.name}`, exact: true })
        .click();
      await page.getByPlaceholder(noteSelector).fill("账号 B 的备注，必须保留");
      await page.getByPlaceholder(phoneSelector).fill("13800000002");
      // Give B a genuine retry record without placing another server order.
      await page.route("**/api/v1/orders", (route) => route.abort("failed"), {
        times: 1,
      });
      await page
        .getByRole("button", { name: "提交自取订单", exact: true })
        .click();
      const networkAlert = page
        .getByRole("alert")
        .filter({ hasText: "暂时连接不上" });
      await expect(networkAlert).toBeVisible();
      const secondKey = `yanhuo-checkout-${secondUser.id}-${f.stall.id}`;
      const before = await page.evaluate(
        ({ firstKey, secondKey, secondCartKey }) => ({
          first: sessionStorage.getItem(firstKey),
          second: sessionStorage.getItem(secondKey),
          cart: localStorage.getItem(secondCartKey),
        }),
        { firstKey, secondKey, secondCartKey },
      );
      expect(before.first).toBe(firstRecord);
      expect(before.second).toBeTruthy();
      const secondCart = JSON.parse(before.cart!)[f.stall.id];
      expect(secondCart).toHaveLength(1);
      expect(secondCart[0].quantity).toBe(2);
      expect(secondCart[0].product.price_cents).toBe(1000);

      held.release();
      await held.completed;
      // Let the fulfilled response (or abort rejection) finish its browser microtasks.
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      await expect(page).toHaveURL(new RegExp(`/checkout/${f.stall.id}$`));
      await expect(page.getByPlaceholder(noteSelector)).toHaveValue(
        "账号 B 的备注，必须保留",
      );
      await expect(page.getByPlaceholder(phoneSelector)).toHaveValue(
        "13800000002",
      );
      await expect(networkAlert).toBeVisible();
      const after = await page.evaluate(
        ({ firstKey, secondKey, secondCartKey }) => ({
          first: sessionStorage.getItem(firstKey),
          second: sessionStorage.getItem(secondKey),
          cart: localStorage.getItem(secondCartKey),
        }),
        { firstKey, secondKey, secondCartKey },
      );
      expect(after).toEqual(before);
      const secondOrders = await (
        await context.request.get(`${baseURL}/api/v1/orders`)
      ).json();
      expect(secondOrders).toHaveLength(0);
      expect((await mutate(context, "/auth/login", f.first)).ok()).toBeTruthy();
      const firstOrders = await (
        await context.request.get(`${baseURL}/api/v1/orders`)
      ).json();
      expect(firstOrders).toHaveLength(lateResponse === "success" ? 1 : 0);
      if (lateResponse === "success")
        expect(firstOrders[0].id).toBe(original.body.id);
    } finally {
      held?.release();
      await page.unroute("**/api/v1/orders").catch(() => {});
    }
  });
}

test("leaving checkout ignores a late success and returning can retry the committed order with its original key", async ({
  page,
  context,
  browser,
}) => {
  const f = await fixture(browser, context);
  let held: Awaited<ReturnType<typeof holdRealOrderResponse>> | undefined;
  try {
    await f.start(page);
    held = await holdRealOrderResponse(page);
    await page
      .getByRole("button", { name: "提交自取订单", exact: true })
      .click();
    const original = await held.upstream;
    expect(original.status).toBe(201);
    const firstKey = `yanhuo-checkout-${f.firstUser.id}-${f.stall.id}`;
    const originalRecord = await page.evaluate(
      (key) => sessionStorage.getItem(key),
      firstKey,
    );
    await page
      .getByRole("link", { name: `查看${f.product.name}详情`, exact: true })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/stalls/${f.stall.id}/products/${f.product.id}$`),
    );
    held.release();
    await held.completed;
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(page).toHaveURL(
      new RegExp(`/stalls/${f.stall.id}/products/${f.product.id}$`),
    );
    expect(
      await page.evaluate((key) => sessionStorage.getItem(key), firstKey),
    ).toBe(originalRecord);
    const cart = await page.evaluate(
      (userId) =>
        JSON.parse(
          localStorage.getItem(`yanhuo-cart-v2:user:${userId}`) || "{}",
        ),
      f.firstUser.id,
    );
    expect(cart[f.stall.id]).toHaveLength(1);
    expect(cart[f.stall.id][0].quantity).toBe(1);
    await page.unroute("**/api/v1/orders");
    await page.goto(`/checkout/${f.stall.id}`);
    await expandContact(page);
    await expect(page.getByPlaceholder(noteSelector)).toHaveValue(
      "账号 A 的口味备注",
    );
    const retry = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/orders") &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "确认原订单结果", exact: true })
      .click();
    const response = await retry;
    expect(response.status(), await response.text()).toBe(200);
    expect(response.request().postDataJSON().idempotency_key).toBe(
      original.request.idempotency_key,
    );
    expect((await response.json()).id).toBe(original.body.id);
    await expect(page).toHaveURL(new RegExp(`/orders/${original.body.id}$`));
    const orders = await (
      await context.request.get(`${baseURL}/api/v1/orders`)
    ).json();
    expect(orders).toHaveLength(1);
    expect(
      await page.evaluate((key) => sessionStorage.getItem(key), firstKey),
    ).toBeNull();
    // Exercise teardown after the page has gone, as happens on test timeouts.
    await page.close();
  } finally {
    held?.release();
    await page.unroute("**/api/v1/orders").catch(() => {});
  }
});
