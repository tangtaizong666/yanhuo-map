import { expect, test, type Page, type Route } from "@playwright/test";
import { fulfillCsrf, assertNoHorizontalOverflow } from "./helpers";
import { publicProduct, publicResponse, mealResponse } from "./public-contracts";

// Every API request is intercepted. No business server, account or payment is used.
const product = {
  id: 701,
  name: "试点煎饼",
  price_cents: 1000,
  stock: 12,
  is_active: true,
  sale_paused: false,
  stock_version: 0,
  image: "/images/food-jianbing.jpg",
  description: "",
  category: "小吃",
  taste_options: [],
};
function order(id: string, status = "pending"): any {
  return {
    id,
    number: id,
    stall_id: 701,
    stall_name: "试点小摊",
    stall_image: product.image,
    status,
    mode: "live",
    fulfillment_type: "pickup",
    payment_method: "offline",
    payment_status: status === "completed" ? "paid" : "unpaid",
    payment: null,
    refund: null,
    review: null,
    payment_review_required: false,
    cancel_requested: false,
    note: "",
    contact_phone: "",
    merchant_contact_phone: "13800138000",
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 240000).toISOString(),
    pickup_code: "12345678",
    pickup_address: "校园南门",
    pickup_latitude: 30,
    pickup_longitude: 120,
    current_address: "校园南门",
    total_cents: 1000,
    items: [
      {
        product_id: 701,
        name: product.name,
        image: product.image,
        quantity: 1,
        unit_price_cents: 1000,
        portions: [],
      },
    ],
    wechat_payment: {
      mode: "live",
      available: false,
      channels: [],
      reason: "未开通",
    },
  };
}
async function fixture(page: Page, merchant = false) {
  const state = {
    user: {
      id: 701,
      username: "pilot-a",
      display_name: "试点A",
      is_merchant: merchant,
      is_staff: false,
    } as any,
    orders: [] as any[],
    requests: [] as string[],
    creates: [] as any[],
    created: new Map<string, any>(),
    loseCreation: false,
    holdStalls: false,
    heldStalls: undefined as Route | undefined,
    heldSnapshot: undefined as any,
    stall: {
      id: 701,
      name: "试点小摊",
      description: "",
      category: "小吃",
      image: product.image,
      area_id: 1,
      area_name: "校园南门",
      address: "校园南门",
      latitude: 30,
      longitude: 120,
      status: "open",
      session_status: "open",
      last_confirmed_at: new Date().toISOString(),
      closes_at: null,
      accepting_orders: true,
      transaction_enabled: true,
      can_order: true,
      prep_minutes: 10,
      rating: null,
      review_count: 0,
      products: [{ ...product }],
      reviews: [],
      wechat_payment: {
        available: false,
        channels: [],
        mode: "live",
        reason: "未开通",
      },
    } as any,
  };
  await page.route("https://**/*", (r) => r.abort());
  await page.route("**/api/v1/**", async (r) => {
    const req = r.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", "");
    state.requests.push(path + url.search);
    const send = (json: any) => r.fulfill({ json: publicResponse(path, json) });
    if (path === "/auth/csrf") return fulfillCsrf(r);
    if (path === "/config")
      return send({
        user: state.user,
        demo_mode: false,
        areas: [],
        amap_key: "",
        stale_minutes: 60,
      });
    if (path === "/auth/me") return send(state.user);
    if (path === "/events") return send({});
    if (path === "/stalls") return send([state.stall]);
    if (path === "/products") return send(mealResponse([state.stall], url.searchParams));
    if (path === "/stalls/701") return send(state.stall);
    if (path === "/orders/active-summary")
      return send({ counts: { total: 0 }, next_order: null });
    if (path === "/orders/recent-completed" || path === "/follows")
      return send([]);
    if (path === "/merchant/metrics")
      return send({
        mode: "live",
        today: { revenue_cents: 0, orders_created: 0, orders_completed: 0 },
        trend: [],
        top_products: [],
        funnel: {},
        totals: {},
      });
    if (path === "/merchant/stalls") {
      if (state.holdStalls) {
        state.holdStalls = false;
        state.heldStalls = r;
        state.heldSnapshot = structuredClone([state.stall]);
        return;
      }
      return send([state.stall]);
    }
    if (path === "/merchant/stalls/701/products") {
      const body = req.postDataJSON();
      state.creates.push(body);
      if (!state.created.has(body.idempotency_key)) {
        const row = { ...product, ...body, id: 800 + state.created.size };
        state.created.set(body.idempotency_key, row);
        state.stall.products.push(row);
      }
      if (state.loseCreation) {
        state.loseCreation = false;
        return r.abort("failed");
      }
      return send(state.created.get(body.idempotency_key));
    }
    if (path === "/merchant/products/701") {
      Object.assign(state.stall.products[0], req.postDataJSON());
      return send(state.stall.products[0]);
    }
    if (path === "/orders" || path === "/merchant/orders") {
      const active = (o: any) =>
        [
          "pending_payment",
          "pending",
          "preparing",
          "ready",
          "delivering",
          "arrived",
        ].includes(o.status);
      const follows = (o: any) => !!o.financial_hold_reason;
      const filters: Record<string, (o: any) => boolean> = {
        all: () => true,
        active,
        followup: follows,
        attention: (o) => active(o) || follows(o),
        completed: (o) => o.status === "completed",
        cancelled: (o) => ["cancelled", "rejected"].includes(o.status),
      };
      const counts = Object.fromEntries(
        Object.entries(filters).map(([key, f]) => [
          key,
          state.orders.filter(f).length,
        ]),
      );
      const rows = state.orders.filter(
        filters[url.searchParams.get("filter") || "all"]!,
      );
      const offset = Number(url.searchParams.get("cursor") || 0),
        end = offset + 30;
      return send({
        results: rows.slice(offset, end),
        next: end < rows.length ? String(end) : null,
        counts,
      });
    }
    if (path.startsWith("/orders/") || path.startsWith("/merchant/orders/")) {
      const found = state.orders.find((o) => path.endsWith("/" + o.id));
      if (found) return send(found);
    }
    return r.fulfill({
      status: 500,
      json: { detail: `Unexpected fixture request: ${path}` },
    });
  });
  return state;
}
async function syncIdentity(page: Page) {
  await page.evaluate(async () => {
    const path = "/src/stores/session.ts";
    await (await import(path)).useSession().refreshUser();
  });
}

test("late unauthorized responses cannot expire a new identity", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/cart");
  let held: Route | undefined;
  await page.route("**/api/v1/old-identity", (r) => {
    held = r;
  });
  await page.evaluate(async () => {
    const path = "/src/lib/api.ts";
    (window as any).oldRequest = (await import(path))
      .api("/old-identity")
      .catch(() => {});
  });
  await expect.poll(() => !!held).toBe(true);
  state.user = { ...state.user, id: 702, display_name: "试点B" };
  await syncIdentity(page);
  await held!.fulfill({
    status: 403,
    json: { code: "not_authenticated", detail: "old session" },
  });
  await page.evaluate(() => (window as any).oldRequest);
  await expect(page).toHaveURL(/\/cart$/);
  await expect(page.locator(".header-user")).toContainText("试点B");
});

test("storage denial leaves student and merchant shells usable", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.addInitScript(() => {
    // Vite includes Pinia's devtools storage. Deny app keys while letting devtools boot.
    for (const method of ["getItem", "setItem", "removeItem"] as const) {
      const original = Storage.prototype[method];
      (Storage.prototype as any)[method] = function (
        key: string,
        ...args: any[]
      ) {
        if (/^(yanhuo|merchant)/.test(key))
          throw new DOMException("denied", "SecurityError");
        return (original as any).call(this, key, ...args);
      };
    }
  });
  await page.goto("/cart");
  await expect(page.getByRole("heading", { name: "我的餐袋。" })).toBeVisible();
  expect(
    await page.evaluate(async () => {
      const path = "/src/lib/storage.ts";
      const storage = await import(path);
      Object.defineProperty(window, "localStorage", {
        configurable: true,
        get() {
          throw new DOMException("denied", "SecurityError");
        },
      });
      return [
        storage.readStorage("any-key"),
        storage.writeStorage("any-key", "x"),
        storage.removeStorage("any-key"),
      ];
    }),
  ).toEqual([null, false, false]);
  state.user = { ...state.user, is_merchant: true };
  await page.goto("/merchant/products");
  await expect(
    page.getByRole("heading", { name: /我的菜品/ }),
  ).toBeVisible();
});

test("account carts isolate portion notes; guest merging is explicit and legacy is sealed", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.addInitScript((p) => {
    if (sessionStorage.getItem("pilot-seeded")) return;
    const rows = {
      701: [
        {
          product: p,
          quantity: 1,
          portions: [{ options: {}, note: "旧版私人备注" }],
        },
      ],
    };
    localStorage.setItem("yanhuo-cart-v1", JSON.stringify(rows));
    localStorage.setItem(
      "yanhuo-cart-v2:guest",
      JSON.stringify({ 701: [{ product: p, quantity: 1 }] }),
    );
    sessionStorage.setItem("pilot-seeded", "yes");
  }, publicProduct(product));
  await page.goto("/cart");
  await expect(page.getByText("旧版私人备注")).toHaveCount(0);
  await expect(page.locator(".cart-groups")).toHaveCount(0);
  await page.getByRole("button", { name: "加入当前账号餐袋" }).click();
  await page.evaluate(async (p) => {
    const path = "/src/stores/cart.ts";
    (await import(path))
      .useCart()
      .setQuantity(701, p, 1, [{ options: {}, note: "仅A可见" }]);
  }, publicProduct(product));
  await expect(page.getByText(/仅A可见/)).toBeVisible();
  state.user = { ...state.user, id: 702 };
  await syncIdentity(page);
  await expect(page.getByText(/仅A可见/)).toHaveCount(0);
  await expect(page.locator(".cart-groups")).toHaveCount(0);
  state.user = { ...state.user, id: 701 };
  await syncIdentity(page);
  await expect(page.getByText(/仅A可见/)).toBeVisible();
});

test("unknown product creation survives reload and retries exact key and body", async ({
  page,
}) => {
  const state = await fixture(page, true);
  state.loseCreation = true;
  await page.goto("/merchant/products");
  await page.getByRole("button", { name: "添加商品", exact: true }).click();
  await page.getByPlaceholder("例如：招牌烤冷面").fill("测试新餐点");
  await page.getByPlaceholder("0.00").fill("12");
  await page
    .getByRole("spinbutton", { name: "线上剩余可卖份数", exact: true })
    .fill("7");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "添加商品", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("新增结果尚未确认");
  await expect(page.getByPlaceholder("例如：招牌烤冷面")).toBeDisabled();
  await page.reload();
  await page.getByRole("button", { name: "确认上一笔新增" }).click();
  await page.getByRole("button", { name: "确认原新增结果" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.creates).toHaveLength(2);
  expect(state.creates[0]).toEqual(state.creates[1]);
  expect(state.creates[0].idempotency_key).toBeTruthy();
  expect(state.created.size).toBe(1);
});

test("a catalog save queues refresh and discards a held older read", async ({
  page,
}) => {
  const state = await fixture(page, true);
  await page.goto("/merchant/products");
  const product = page.locator('article.merchant-product').filter({ has: page.getByRole('heading', { name: '试点煎饼', exact: true }) });
  await product.getByRole('button', { name: '编辑商品：试点煎饼', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '编辑商品', exact: true });
  const price = dialog.getByRole('spinbutton', { name: /单价（元）/ });
  await expect(price).toHaveValue("10.00");
  state.holdStalls = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => !!state.heldStalls).toBe(true);
  await price.fill("18");
  await dialog.getByRole('button', { name: '保存修改', exact: true }).click();
  await expect.poll(() => state.stall.products[0].price_cents).toBe(1800);
  await expect(dialog).toHaveCount(0);
  await state.heldStalls!.fulfill({ json: state.heldSnapshot });
  await expect(product.locator('.product-info strong')).toHaveText('¥18');
  await product.getByRole('button', { name: '编辑商品：试点煎饼', exact: true }).click();
  await expect(price).toHaveValue("18.00");
  await expect(product.locator('.product-info strong')).toHaveText('¥18');
});

test("cancel dialog traps keyboard focus and returns it without cancelling", async ({
  page,
}) => {
  const state = await fixture(page);
  state.orders = [
    {
      ...order("cancel-fixture"),
      allowed_actions: ["cancel"],
      financial_hold_reason: "",
    },
  ];
  await page.goto("/orders/cancel-fixture");
  const trigger = page.getByRole("button", { name: "取消订单", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "确定取消这份订单？" });
  await expect(dialog).toBeVisible();
  for (let n = 0; n < 9; n++) {
    await page.keyboard.press("Tab");
    expect(
      await page.evaluate(() =>
        document.activeElement?.closest("dialog")?.hasAttribute("open"),
      ),
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(state.requests.some((path) => path.endsWith("/cancel"))).toBe(false);
});

test("history paginates and background refresh reads attention only", async ({
  page,
}) => {
  const state = await fixture(page);
  state.orders = Array.from({ length: 35 }, (_, n) =>
    order(`history-${n}`, "completed"),
  );
  await page.clock.install();
  await page.goto("/orders");
  await expect(page.locator(".order-card")).toHaveCount(30);
  await page.getByRole("button", { name: "加载更多订单" }).click();
  await expect(page.locator(".order-card")).toHaveCount(35);
  state.requests.length = 0;
  await page.clock.fastForward(10001);
  await expect
    .poll(() =>
      state.requests.some((path) => path.includes("filter=attention")),
    )
    .toBe(true);
  expect(
    state.requests.some(
      (path) =>
        path.startsWith("/orders?") && !path.includes("filter=attention"),
    ),
  ).toBe(false);
});

test("merchant attention beyond one page remains actionable and server financial holds win", async ({
  page,
}) => {
  const state = await fixture(page, true);
  state.orders = Array.from({ length: 35 }, (_, n) => ({
    ...order(`active-${n}`),
    allowed_actions: ["accept", "reject"],
    financial_hold_reason: "",
  }));
  state.orders[34] = {
    ...state.orders[34],
    financial_hold_reason: "付款等待运营核验",
    allowed_actions: ["sync_payment"],
  };
  await page.goto("/merchant/orders");
  await expect
    .poll(() =>
      state.requests.some(
        (path) =>
          path.includes("filter=attention") && path.includes("cursor=30"),
      ),
    )
    .toBe(true);
  await expect(
    page.getByRole("button", { name: "接单开始做", exact: true }),
  ).toHaveCount(34);
  await expect(
    page.locator("button:disabled").filter({ hasText: "接单开始做" }),
  ).toHaveCount(0);
  await expect(page.locator(".merchant-order").filter({ hasText: "付款等待运营核验" })).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await assertNoHorizontalOverflow(page);
});

for (const width of [360, 390, 768, 1440]) {
  test(`pilot key screens fit ${width}px`, async ({ page }, testInfo) => {
    const state = await fixture(page);
    state.orders = [order("viewport-order")];
    await page.setViewportSize({ width, height: 960 });
    for (const url of [
      "/orders",
      "/orders/viewport-order",
      "/cart",
      "/merchant/products",
      "/merchant/orders",
    ]) {
      state.user = { ...state.user, is_merchant: url.startsWith("/merchant") };
      await page.goto(url);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await assertNoHorizontalOverflow(page);
      const filename = `${width}-${url.replaceAll("/", "-")}.png`;
      await page.screenshot({
        path: testInfo.outputPath(filename),
        fullPage: true,
      });
      await testInfo.attach(filename, {
        path: testInfo.outputPath(filename),
        contentType: "image/png",
      });
    }
  });
}

test("production startup tolerates a denied storage getter", async ({
  page,
}) => {
  test.skip(
    process.env.E2E_PRODUCTION !== "1",
    "Run against production preview with E2E_PRODUCTION=1",
  );
  const state = await fixture(page);
  await page.addInitScript(() => {
    for (const name of ["localStorage", "sessionStorage"])
      Object.defineProperty(window, name, {
        get() {
          throw new DOMException("denied", "SecurityError");
        },
      });
  });
  await page.goto("/cart");
  await expect(page.getByRole("heading", { name: "我的餐袋。" })).toBeVisible();
  state.user = { ...state.user, is_merchant: true };
  await page.goto("/merchant/products");
  await expect(
    page.getByRole("heading", { name: /我的菜品/ }),
  ).toBeVisible();
});

test("terminal unpaid intents never expose Native or H5 payment entry", async ({
  page,
}) => {
  const state = await fixture(page);
  state.orders = [
    {
      ...order("terminal-payment", "cancelled"),
      allowed_actions: ["sync_payment"],
      financial_hold_reason: "支付结果尚未确认，请先查询或关闭支付。",
      payment: {
        id: "old-intent",
        status: "pending",
        channel: "native",
        code_url: "weixin://wxpay/test",
        expires_at: new Date(Date.now() + 600000).toISOString(),
      },
    },
  ];
  await page.goto("/orders/terminal-payment");
  await expect(
    page
      .getByText("支付结果尚未确认，请先查询或关闭支付。", { exact: true })
      .first(),
  ).toBeVisible();
  await expect(page.getByAltText("本订单微信支付二维码")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "前往微信支付" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "取消订单", exact: true }),
  ).toHaveCount(0);
});

for (const status of [200, 204]) {
  test(`old successful ${status} response cannot update a new identity`, async ({
    page,
  }) => {
    const state = await fixture(page);
    const original = { ...state.user };
    await page.goto("/cart");
    let held: Route | undefined;
    await page.route("**/api/v1/old-success", (route) => {
      held = route;
    });
    await page.evaluate(async () => {
      const apiPath = "/src/lib/api.ts",
        sessionPath = "/src/stores/session.ts";
      const { api } = await import(apiPath);
      const session = (await import(sessionPath)).useSession();
      (window as any).oldSuccess = api("/old-success")
        .then((data: any) => {
          // An unguarded caller would overwrite identity (or clear it for 204).
          session.user = data;
          return { accepted: true };
        })
        .catch((error: any) => ({
          accepted: false,
          code: error.code,
          submitted: error.data.submitted,
        }));
    });
    await expect.poll(() => !!held).toBe(true);
    state.user = { ...state.user, id: 702, display_name: "试点B" };
    await syncIdentity(page);
    await held!.fulfill(
      status === 204 ? { status } : { status, json: original },
    );
    expect(await page.evaluate(() => (window as any).oldSuccess)).toEqual({
      accepted: false,
      code: "stale_session_response",
      submitted: true,
    });
    await expect(page.locator(".header-user")).toContainText("试点B");
    await expect(page).toHaveURL(/\/cart$/);
  });
}
