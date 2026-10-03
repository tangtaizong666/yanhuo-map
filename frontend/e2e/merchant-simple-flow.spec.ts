import { test, expect, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// All requests are local fixtures; no demo database or payment gateway is used.
async function fixture(
  page: Page,
  scenario: "normal" | "exceptions" | "delivery" = "normal",
) {
  // These tests start with an authenticated merchant. Establish the readable
  // cookie before navigation: WebKit can defer cookies added during a routed
  // response, which would test CSRF preflight instead of a lost mutation result.
  await page.context().addCookies([
    {
      name: "csrftoken",
      value: "fixture-csrf-token",
      domain: new URL(process.env.E2E_BASE_URL || "http://127.0.0.1:5183")
        .hostname,
      path: "/",
      sameSite: "Lax",
    },
  ]);
  const user = {
    id: 960,
    username: "simple_merchant",
    display_name: "校园饭摊",
    is_merchant: true,
    is_staff: false,
  };
  const product = {
    id: 961,
    name: "香菇鸡肉饭",
    image: "",
    price_cents: 1200,
    stock: 20,
    stock_version: 1,
    is_active: true,
    sale_paused: false,
    taste_options: [],
    description: "",
  };
  const stall = {
    id: 960,
    name: "校园饭摊",
    products: [product],
    reviews: [],
    image: "",
    area_name: "南门",
    address: "南门橙色棚",
    latitude: 30,
    longitude: 120,
    status: "open",
    session_status: "open",
    prep_minutes: 10,
    accepting_orders: true,
    transaction_enabled: true,
    is_visible: true,
    can_order: true,
    contact_phone: "",
    services: { mode: "live" },
    last_confirmed_at: new Date().toISOString(),
    closes_at: new Date(Date.now() + 7200000).toISOString(),
  };
  const make = (id: string, status: string, extra: any = {}) => ({
    id,
    number: id.toUpperCase(),
    stall_id: 960,
    stall_name: stall.name,
    mode: "live",
    status,
    fulfillment_type: "pickup",
    payment_method: "offline",
    payment_status: "unpaid",
    payment: null,
    refund: null,
    review: null,
    cancel_requested: false,
    payment_review_required: false,
    financial_hold_reason: "",
    total_cents: 2400,
    items_total_cents: 2400,
    pickup_address: stall.address,
    note: "",
    contact_phone: "",
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 240000).toISOString(),
    items: [
      {
        product_id: 961,
        name: product.name,
        image: "",
        quantity: 2,
        unit_price_cents: 1200,
        portions: [
          { options: { 辣度: "不辣" }, note: "不要香菜" },
          { options: { 辣度: "微辣" }, note: "" },
        ],
      },
    ],
    ...extra,
  });
  const orders: any[] =
    scenario === "exceptions"
      ? [
          make("cancel-order", "preparing", {
            cancel_requested: true,
            cancel_reason: "临时有事",
          }),
          make("review-order", "ready", {
            payment_method: "wechat",
            payment_status: "paid",
            payment_review_required: true,
            financial_hold_reason:
              "付款存在异常，须由运营核验资金后才能继续处理。",
            payment: { status: "review" },
          }),
          make("refund-order", "cancelled", {
            payment_method: "wechat",
            payment_status: "refunding",
            financial_hold_reason: "退款尚未核验结案",
            refund: {
              id: "refund-1",
              status: "abnormal",
              reason: "测试退款",
              amount_cents: 2400,
            },
          }),
        ]
      : scenario === "delivery"
        ? [
            make("delivery-ready", "ready", {
              fulfillment_type: "delivery",
              payment_method: "wechat",
              payment_status: "paid",
              payment: { status: "paid" },
              delivery_point_name: "南门交接点",
              delivery_point_address: "南门橙色棚旁",
            }),
            make("delivery-wait", "pending_payment", {
              fulfillment_type: "delivery",
              payment_method: "wechat",
              delivery_point_name: "南门交接点",
            }),
          ]
        : [make("simple-order", "pending")];
  const state = {
    orders,
    actions: [] as any[],
    loseAction: "",
    unexpected: [] as string[],
  };
  function serialized(order: any) {
    let allowed =
      order.payment_review_required || order.refund
        ? ["sync_payment"]
        : order.cancel_requested
          ? ["approve_cancel", "deny_cancel"]
          : order.status === "pending"
            ? ["accept", "reject"]
            : order.status === "preparing"
              ? ["ready", "update_prep"]
              : order.status === "ready" &&
                  order.fulfillment_type === "delivery"
                ? ["dispatch", "report_delivery_issue", "refund"]
                : order.status === "delivering"
                  ? ["arrive", "report_delivery_issue", "refund"]
                  : order.status === "arrived"
                    ? ["complete", "report_delivery_issue", "refund"]
                    : order.status === "ready"
                      ? order.payment_status === "paid"
                        ? ["complete"]
                        : ["confirm_payment"]
                      : [];
    return { ...order, allowed_actions: allowed };
  }
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config")
      return send({
        user,
        demo_mode: false,
        areas: [],
        amap_key: "",
        stale_minutes: 60,
      });
    if (path === "/auth/me") return send(user);
    if (path === "/events") return send({});
    if (path === "/merchant/stalls") return send([stall]);
    if (path === "/merchant/orders") {
      const filter = new URL(request.url()).searchParams.get("filter") || "all";
      const active = orders.filter(
        (order) =>
          !["completed", "cancelled", "rejected"].includes(order.status),
      );
      const attention = orders.filter(
        (order) =>
          active.includes(order) ||
          order.refund ||
          order.payment_review_required ||
          order.delivery_issue,
      );
      const rows =
        filter === "attention"
          ? attention
          : filter === "all"
            ? orders
            : filter === "cancelled"
              ? orders.filter((order) =>
                  ["cancelled", "rejected"].includes(order.status),
                )
              : orders.filter((order) => order.status === filter);
      return send({
        results: rows.map(serialized),
        next: null,
        counts: {
          all: orders.length,
          active: active.length,
          attention: attention.length,
        },
      });
    }
    if (path === "/merchant/metrics")
      return send({
        today: {
          revenue_cents: 0,
          orders_created: orders.length,
          orders_completed: 0,
        },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    if (path === "/merchant/stalls/960/pickup-lookup")
      return send(serialized(orders[0]));
    const matched = path.match(/^\/merchant\/orders\/([^/]+)\/action$/);
    if (matched) {
      const body = request.postDataJSON(),
        order = orders.find((row) => row.id === matched[1]);
      state.actions.push(body);
      if (body.action === "accept") {
        order.status = "preparing";
        order.accepted_at = new Date().toISOString();
        order.estimated_ready_at = new Date(
          Date.now() + body.prep_minutes * 60000,
        ).toISOString();
      }
      if (body.action === "ready") {
        order.status = "ready";
        order.ready_at = new Date().toISOString();
      }
      if (body.action === "confirm_payment") order.payment_status = "paid";
      if (body.action === "complete") order.status = "completed";
      if (body.action === "approve_cancel") {
        order.status = "cancelled";
        order.cancel_requested = false;
      }
      if (body.action === "dispatch") order.status = "delivering";
      if (body.action === "arrive") order.status = "arrived";
      if (state.loseAction === body.action) {
        state.loseAction = "";
        return route.abort("failed");
      }
      return send(serialized(order));
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  return state;
}

function stage(page: Page, label: string) {
  return page
    .getByRole("group", { name: "订单阶段", exact: true })
    .getByRole("button", { name: new RegExp(label) });
}

test("three clear queues keep dishes, portions and the primary action visible on a phone", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant");
  await expect(stage(page, "新订单")).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("group", { name: "订单阶段" }).getByRole("button"),
  ).toHaveCount(3);
  await expect(page.locator(".merchant-order")).toContainText("不要香菜");
  await expect(page.locator(".merchant-order")).toContainText("第 2 份");
  await expect(page.locator(".m-simple-dish-title")).toContainText("2 份");
  await expect(
    page.getByRole("button", { name: "接单开始做", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "出餐台", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.locator(".merchant-order .m-orders-button.primary"),
  ).toHaveCount(1);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
    await assertNoHorizontalOverflow(page);
    const box = await page
      .getByRole("button", { name: "接单开始做", exact: true })
      .boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(48);
    if (width <= 390) {
      const bottomNavigation = await page
        .getByRole("navigation", { name: "商家底部导航" })
        .boundingBox();
      expect(box!.y + box!.height).toBeLessThan(bottomNavigation!.y);
      await page.screenshot({
        path: info.outputPath(`merchant-simple-${width}-top.png`),
        animations: 'disabled',
      });
    }
  }
  await page.screenshot({
    path: info.outputPath("merchant-simple-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("merchant-simple-mobile.png"),
    fullPage: true,
  });
  expect(state.unexpected).toEqual([]);
});

test("accept, cook, collect payment and verify pickup remain four separate actions", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant/orders");
  await page.getByRole("button", { name: "接单开始做", exact: true }).click();
  await expect.poll(() => state.orders[0].status).toBe("preparing");
  await stage(page, "制作中").click();
  await page.getByRole("button", { name: "做好了", exact: true }).click();
  await expect.poll(() => state.orders[0].status).toBe("ready");
  await stage(page, "待取餐").click();
  await page.getByRole("button", { name: "收款 ¥24", exact: true }).click();
  expect(state.orders[0].payment_status).toBe("unpaid");
  await page.getByRole("button", { name: "确认收款", exact: true }).click();
  await expect.poll(() => state.orders[0].payment_status).toBe("paid");
  expect(state.orders[0].status).toBe("ready");
  await page.getByRole("button", { name: "核对取餐码", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "订单详情", exact: true });
  await drawer.getByPlaceholder("输入取餐码", { exact: true }).fill("12345678");
  await drawer.getByRole("button", { name: "核销并完成", exact: true }).click();
  await expect.poll(() => state.orders[0].status).toBe("completed");
  expect(state.actions.map((row) => row.action)).toEqual([
    "accept",
    "ready",
    "confirm_payment",
    "complete",
  ]);
  expect(new Set(state.actions.map((row) => row.idempotency_key)).size).toBe(4);
  expect(state.unexpected).toEqual([]);
});

test("an unknown action remains prominent and replays the same request after reload", async ({
  page,
}) => {
  const state = await fixture(page);
  state.loseAction = "accept";
  await page.goto("/merchant/orders");
  await page.getByRole("button", { name: "接单开始做", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /1 笔操作结果待确认/ }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: /1 笔操作结果待确认/ }).click();
  await page
    .getByRole("button", { name: "确认原操作结果", exact: true })
    .click();
  await expect.poll(() => state.actions.length).toBe(2);
  expect(state.actions[1]).toEqual(state.actions[0]);
  await expect(
    page.getByRole("button", { name: /笔操作结果待确认/ }),
  ).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("lost final pickup response can be confirmed from history without an empty trapped dialog", async ({
  page,
}) => {
  const state = await fixture(page);
  state.orders[0].status = "ready";
  state.orders[0].payment_status = "paid";
  state.loseAction = "complete";
  await page.goto("/merchant/orders");
  await stage(page, "待取餐").click();
  await page.getByRole("button", { name: "核对取餐码", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "订单详情", exact: true });
  await drawer.getByPlaceholder("输入取餐码", { exact: true }).fill("12345678");
  await drawer.getByRole("button", { name: "核销并完成", exact: true }).click();
  await expect.poll(() => state.actions.length).toBe(1);
  await expect(drawer).not.toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: /1 笔操作结果待确认/ }).click();
  await page
    .getByRole("button", { name: "确认原操作结果", exact: true })
    .click();
  await expect.poll(() => state.actions.length).toBe(2);
  expect(state.actions[1]).toEqual(state.actions[0]);
  expect(state.orders[0].status).toBe("completed");
  await expect(
    page.getByRole("button", { name: /笔操作结果待确认/ }),
  ).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("cancellations and unresolved finances stay visible across all queues", async ({
  page,
}) => {
  const state = await fixture(page, "exceptions");
  await page.goto("/merchant/orders");
  await expect(
    page.getByRole("button", { name: /1 笔取消申请/ }),
  ).toBeVisible();
  await stage(page, "待取餐").click();
  await expect(
    page.getByRole("button", { name: /1 笔取消申请/ }),
  ).toBeVisible();
  await expect(page.locator(".merchant-order")).toContainText("付款需人工核对");
  await expect(page.getByRole("button", { name: /^收款 ¥/ })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "核对取餐码", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /1 笔取消申请/ }).click();
  await page.getByRole("button", { name: "处理取消申请", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "订单详情", exact: true });
  await expect(drawer).toContainText("临时有事");
  await drawer.getByRole("button", { name: "同意取消", exact: true }).click();
  await page.getByRole("button", { name: "确认取消订单", exact: true }).click();
  await expect.poll(() => state.orders[0].status).toBe("cancelled");
  await expect(drawer).not.toBeVisible();
  await page.getByRole("button", { name: /款项 \/ 配送问题/ }).click();
  await expect(page.locator(".merchant-order")).toHaveCount(2);
  await expect(
    page.locator(".merchant-order").filter({ hasText: "REFUND-ORDER" }),
  ).toContainText("退款异常");
  expect(state.unexpected).toEqual([]);
});

test("delivery keeps payment, dispatch, arrival and handoff as distinct states", async ({
  page,
}) => {
  const state = await fixture(page, "delivery");
  await page.goto("/merchant/orders");
  await expect(page.locator(".merchant-order")).toContainText(
    "等待顾客付款，暂不制作",
  );
  await expect(
    page.getByRole("button", { name: "接单开始做", exact: true }),
  ).toHaveCount(0);
  await stage(page, "待取餐").click();
  await page.getByRole("button", { name: "出发送餐", exact: true }).click();
  await page.getByRole("button", { name: "确认已出发", exact: true }).click();
  await expect.poll(() => state.orders[0].status).toBe("delivering");
  await page.getByRole("button", { name: "已到交接点", exact: true }).click();
  await page.getByRole("button", { name: "确认已到达", exact: true }).click();
  await expect.poll(() => state.orders[0].status).toBe("arrived");
  await expect(
    page.getByRole("button", { name: "核对收餐码", exact: true }),
  ).toBeVisible();
  expect(state.actions.map((row) => row.action)).toEqual([
    "dispatch",
    "arrive",
  ]);
  expect(state.unexpected).toEqual([]);
});
