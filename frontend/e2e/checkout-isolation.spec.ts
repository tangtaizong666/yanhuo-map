import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { baseURL, createStudent, mutate } from "./helpers";

const noteSelector = "例如：餐具按需提供（每份口味请在上方分别填写）";
const phoneSelector = "如遇缺货，方便商家联系你";

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
    await page.getByPlaceholder(noteSelector).fill("账号 A 的口味备注");
    await page.getByPlaceholder(phoneSelector).fill("13800000001");
  }
  async function cleanup() {
    // Each account was created by this fixture, and only this isolated product is cleaned up.
    for (const student of [first, second]) {
      expect((await mutate(context, "/auth/login", student)).ok()).toBeTruthy();
      const orders = await (
        await context.request.get(`${baseURL}/api/v1/orders`)
      ).json();
      for (const order of orders) {
        if (
          order.status === "pending" &&
          order.items.every((item: any) => item.product_id === product.id)
        ) {
          const cancelled = await mutate(
            context,
            `/orders/${order.id}/cancel`,
            { reason: "结算隔离测试结束，恢复库存" },
          );
          expect(cancelled.ok(), await cancelled.text()).toBeTruthy();
        }
      }
    }
    const hidden = await mutate(
      merchant,
      `/merchant/products/${product.id}`,
      { is_active: false },
      "PATCH",
    );
    expect(hidden.ok(), await hidden.text()).toBeTruthy();
    const restored = await mutate(
      merchant,
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
    await merchant.close();
  }
  return { first, second, firstUser, merchant, stall, product, start, cleanup };
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
) {
  const response = await mutate(context, "/auth/login", account);
  expect(response.ok(), await response.text()).toBeTruthy();
  const user = await response.json();
  const identity = page.waitForResponse((result) =>
    result.url().endsWith("/auth/me"),
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  expect((await identity).ok()).toBeTruthy();
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

      const secondUser = await switchIdentity(page, context, f.second);
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
      await expect(page.getByRole("alert")).toContainText("暂时连接不上");
      const secondKey = `yanhuo-checkout-${secondUser.id}-${f.stall.id}`;
      const before = await page.evaluate(
        ({ firstKey, secondKey }) => ({
          first: sessionStorage.getItem(firstKey),
          second: sessionStorage.getItem(secondKey),
          cart: localStorage.getItem("yanhuo-cart-v1"),
        }),
        { firstKey, secondKey },
      );
      expect(before.first).toBe(firstRecord);
      expect(before.second).toBeTruthy();

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
      await expect(page.getByRole("alert")).toContainText("暂时连接不上");
      const after = await page.evaluate(
        ({ firstKey, secondKey }) => ({
          first: sessionStorage.getItem(firstKey),
          second: sessionStorage.getItem(secondKey),
          cart: localStorage.getItem("yanhuo-cart-v1"),
        }),
        { firstKey, secondKey },
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
      await page.unroute("**/api/v1/orders");
      await f.cleanup();
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
    const cart = await page.evaluate(() =>
      JSON.parse(localStorage.getItem("yanhuo-cart-v1") || "{}"),
    );
    expect(cart[f.stall.id]).toHaveLength(1);
    expect(cart[f.stall.id][0].quantity).toBe(1);
    await page.unroute("**/api/v1/orders");
    await page.goto(`/checkout/${f.stall.id}`);
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
  } finally {
    held?.release();
    await page.unroute("**/api/v1/orders");
    await f.cleanup();
  }
});
