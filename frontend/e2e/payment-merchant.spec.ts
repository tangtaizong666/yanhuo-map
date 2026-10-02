import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Explicit UI contract fixtures: every API request is intercepted. These tests
// never create real orders, call WeChat, or simulate a successful live payment.
const timestamp = "2026-09-26T10:30:00+08:00";
const user = {
  id: 991,
  username: "payment_ui_fixture",
  display_name: "界面测试商家",
  is_merchant: true,
  is_staff: false,
};
const stall = {
  id: 991,
  name: "支付界面测试摊位",
  image: "/images/food-cold-noodles.jpg",
  area_name: "测试校门",
  status: "open",
  last_confirmed_at: new Date().toISOString(),
  transaction_enabled: true,
  rating: null,
  review_count: 0,
  products: [],
  reviews: [],
};

function makeOrder(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    number: "UI-PAYMENT-0001",
    stall_id: stall.id,
    stall_name: stall.name,
    status: "ready",
    payment_method: "wechat",
    payment_status: "paid",
    payment_review_required: false,
    payment: { id: "payment-ui-1", status: "paid" },
    refund: null,
    total_cents: 1450,
    created_at: timestamp,
    accepted_at: timestamp,
    ready_at: timestamp,
    paid_at: timestamp,
    completed_at: null,
    expires_at: null,
    pickup_address: "测试校门取餐点",
    current_address: "测试校门取餐点",
    location_changed: false,
    note: "",
    contact_phone: "",
    cancel_requested: false,
    cancel_reason: "",
    review: null,
    items: [
      {
        product_id: 991,
        name: "测试餐点",
        image: stall.image,
        unit_price_cents: 1450,
        quantity: 1,
      },
    ],
    ...overrides,
  };
}

async function stubWorkbench(
  page: Page,
  orders: any[],
  refund?: (body: any) => Promise<{ status?: number; body: any }>,
) {
  const unexpected: string[] = [];
  const refunds: any[] = [];
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (path === "/config")
      return send({
        demo_mode: true,
        brand: "烟火地图",
        amap_key: "",
        amap_proxy: "",
        areas: [],
        user,
      });
    if (path === "/auth/me") return send(user);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/merchant/stalls") return send([stall]);
    if (path === "/merchant/orders" && request.method() === "GET")
      return send(orders);
    if (path === "/merchant/metrics")
      return send({
        revenue_cents: 1450,
        offline_revenue_cents: 0,
        online_revenue_cents: 1450,
        refund_cents: 0,
        today: { revenue_cents: 1450, orders_created: 1, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    if (
      /^\/merchant\/orders\/[^/]+\/refund$/.test(path) &&
      request.method() === "POST" &&
      refund
    ) {
      refunds.push(request.postDataJSON());
      const result = await refund(request.postDataJSON());
      return send(result.body, result.status || 200);
    }
    unexpected.push(`${request.method()} ${path}`);
    return send(
      { detail: "Unexpected request in isolated payment UI fixture" },
      500,
    );
  });
  await page.goto("/merchant/orders?filter=all");
  await expect(
    page.getByRole("heading", { name: "订单处理.", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".merchant-order")).toHaveCount(orders.length);
  return { unexpected, refunds };
}

test("UI contract: online payment holds hide manual receipts and closed payments allow the offline path", async ({
  page,
}) => {
  const orders = ["creating", "pending", "reconcile"].map((status, index) =>
    makeOrder({
      id: `online-hold-${index}`,
      number: `UI-HOLD-${index}`,
      payment_status: "unpaid",
      payment: { id: `attempt-${index}`, status },
    }),
  );
  orders.push(
    makeOrder({
      id: "closed-attempt",
      number: "UI-CLOSED-0001",
      payment_status: "unpaid",
      payment_method: "offline",
      payment: { id: "closed-payment", status: "closed" },
    }),
  );
  const fixture = await stubWorkbench(page, orders);
  for (const status of ["正在发起微信支付", "等待微信付款", "付款核对中"])
    await expect(
      page.locator(".merchant-payment-info strong", { hasText: status }),
    ).toBeVisible();
  await expect(page.getByRole("button", { name: /^确认已收到/ })).toHaveCount(
    1,
  );
  const closed = page.locator(".merchant-order", { hasText: "UI-CLOSED-0001" });
  await expect(
    closed.getByRole("button", { name: "确认已收到 ¥14.5", exact: true }),
  ).toBeVisible();
  await expect(closed).toContainText("微信支付已关闭");
  await expect(
    page.getByRole("button", { name: "核销并完成", exact: true }),
  ).toHaveCount(0);
  expect(fixture.unexpected).toEqual([]);
});

test("UI contract: full refund needs amount and reason confirmation and processing is not success", async ({
  page,
}, testInfo) => {
  const order = makeOrder();
  let release!: () => void;
  const pendingResponse = new Promise<void>((resolve) => {
    release = resolve;
  });
  const fixture = await stubWorkbench(page, [order], async (body) => {
    await pendingResponse;
    Object.assign(order, {
      payment_status: "refunding",
      refund: {
        id: "refund-ui-1",
        status: "processing",
        reason: body.reason,
        amount_cents: 1450,
        created_at: timestamp,
        completed_at: null,
        error_message: "",
      },
    });
    return { body: order };
  });
  if (
    await page
      .locator(".merchant-order .m-order-more:not([open]) > summary")
      .count()
  )
    await page
      .locator(".merchant-order .m-order-more:not([open]) > summary")
      .click();
  await page.getByRole("button", { name: "全额原路退款", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "确认全额原路退款",
    exact: true,
  });
  await expect(dialog).toContainText("¥14.5");
  await expect(dialog).toContainText("已取餐订单保留履约记录，不回补库存");
  const confirm = dialog.getByRole("button", {
    name: "确认退款 ¥14.5",
    exact: true,
  });
  await expect(confirm).toBeDisabled();
  await dialog
    .getByRole("textbox", { name: "退款原因", exact: true })
    .fill("退".repeat(27));
  await expect(confirm).toBeDisabled();
  await dialog
    .getByRole("textbox", { name: "退款原因", exact: true })
    .fill("顾客申请，已协商");
  await expect(confirm).toBeEnabled();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 390, height: 900 });
  await page.screenshot({
    path: testInfo.outputPath("merchant-refund-confirmation-mobile.png"),
  });
  await confirm.click();
  await expect.poll(() => fixture.refunds.length).toBe(1);
  await expect(
    dialog.getByRole("button", { name: "正在处理…", exact: true }),
  ).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  release();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".merchant-payment-info")).toContainText(
    "退款处理中",
  );
  await expect(page.locator(".toast.info")).toContainText("退款尚未完成");
  await expect(page.locator(".toast.success")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "核销并完成", exact: true }),
  ).toHaveCount(0);
  if (
    await page
      .locator(".merchant-order .m-order-more:not([open]) > summary")
      .count()
  )
    await page
      .locator(".merchant-order .m-order-more:not([open]) > summary")
      .click();
  await page.getByRole("button", { name: "查询退款进度", exact: true }).click();
  await expect.poll(() => fixture.refunds.length).toBe(2);
  expect(fixture.refunds).toEqual([
    { reason: "顾客申请，已协商" },
    { reason: "顾客申请，已协商" },
  ]);
  expect(fixture.unexpected).toEqual([]);
});

test("UI contract: payment review blocks manual receipt, refund and pickup verification", async ({
  page,
}) => {
  const fixture = await stubWorkbench(page, [
    makeOrder({
      payment_review_required: true,
      payment: { id: "review-ui", status: "review" },
    }),
  ]);
  await expect(page.locator(".merchant-payment-info")).toContainText(
    "付款需人工核对",
  );
  await expect(page.locator(".merchant-payment-info")).toContainText(
    "联系运营核对",
  );
  await expect(page.getByRole("button", { name: /^确认已收到/ })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "全额原路退款", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "核销并完成", exact: true }),
  ).toHaveCount(0);
  expect(fixture.unexpected).toEqual([]);
});

test("UI contract: completed order refund retains its completion state and displays confirmed refund only", async ({
  page,
}) => {
  const order = makeOrder({ status: "completed", completed_at: timestamp });
  const fixture = await stubWorkbench(page, [order], async (body) => {
    Object.assign(order, {
      payment_status: "refunded",
      refund: {
        id: "refund-done-ui",
        status: "success",
        reason: body.reason,
        amount_cents: 1450,
        created_at: timestamp,
        completed_at: timestamp,
        error_message: "",
      },
    });
    return { body: order };
  });
  if (
    await page
      .locator(".merchant-order .m-order-more:not([open]) > summary")
      .count()
  )
    await page
      .locator(".merchant-order .m-order-more:not([open]) > summary")
      .click();
  await page.getByRole("button", { name: "全额原路退款", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "确认全额原路退款",
    exact: true,
  });
  await dialog
    .getByRole("textbox", { name: "退款原因", exact: true })
    .fill("餐点问题，协商退款");
  await dialog
    .getByRole("button", { name: "确认退款 ¥14.5", exact: true })
    .click();
  await expect(page.locator(".merchant-payment-info")).toContainText(
    "已原路退款",
  );
  await expect(page.locator(".merchant-order-status")).toHaveText("已完成");
  await expect(page.locator(".toast.success")).toContainText(
    "微信已确认全额原路退款",
  );
  await expect(
    page.getByRole("button", { name: "全额原路退款", exact: true }),
  ).toHaveCount(0);
  expect(fixture.refunds).toHaveLength(1);
  expect(fixture.unexpected).toEqual([]);
});

test("UI contract: a rejected refund request preserves the dialog error and never reports success", async ({
  page,
}) => {
  const fixture = await stubWorkbench(page, [makeOrder()], async () => ({
    status: 409,
    body: {
      detail: "支付结果需人工核对，请联系运营。",
      code: "payment_review_required",
    },
  }));
  if (
    await page
      .locator(".merchant-order .m-order-more:not([open]) > summary")
      .count()
  )
    await page
      .locator(".merchant-order .m-order-more:not([open]) > summary")
      .click();
  await page.getByRole("button", { name: "全额原路退款", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "确认全额原路退款",
    exact: true,
  });
  await dialog
    .getByRole("textbox", { name: "退款原因", exact: true })
    .fill("顾客申请退款");
  await dialog
    .getByRole("button", { name: "确认退款 ¥14.5", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toHaveText(
    "支付结果需人工核对，请联系运营。",
  );
  await expect(page.locator(".toast.success")).toHaveCount(0);
  await expect(
    dialog.getByRole("button", { name: "确认退款 ¥14.5", exact: true }),
  ).toBeEnabled();
  expect(fixture.refunds).toHaveLength(1);
  expect(fixture.unexpected).toEqual([]);
});
