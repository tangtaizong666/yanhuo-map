import { test, expect, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Intercept every API request: these handoff checks never use business data.
async function fixture(page: Page, overrides: Record<string, any> = {}) {
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
    id: 978,
    username: "handoff_fixture",
    display_name: "南门小厨",
    is_merchant: true,
    is_staff: false,
  };
  const stall = {
    id: 978,
    name: "南门小厨",
    status: "open",
    session_status: "open",
    address: "南门橙色棚",
    latitude: 30,
    longitude: 120,
    is_visible: true,
    transaction_enabled: true,
    accepting_orders: true,
    activation: { has_location: true },
    prep_minutes: 10,
    products: [],
    reviews: [],
    last_confirmed_at: new Date().toISOString(),
    services: { mode: "live" },
  };
  const order: any = {
    id: "handoff-order",
    number: "YH-978",
    stall_id: stall.id,
    stall_name: stall.name,
    status: "ready",
    fulfillment_type: "pickup",
    mode: "live",
    payment_method: "offline",
    payment_status: "paid",
    payment: null,
    refund: null,
    total_cents: 2400,
    items_total_cents: 2400,
    pickup_address: stall.address,
    note: "分开装袋",
    contact_phone: "13800000000",
    created_at: new Date().toISOString(),
    ready_at: new Date().toISOString(),
    paid_at: new Date().toISOString(),
    items: [
      {
        product_id: 979,
        name: "香菇鸡肉饭",
        quantity: 2,
        unit_price_cents: 1200,
        portions: [
          { options: { 辣度: "不辣" }, note: "不要香菜" },
          { options: { 辣度: "微辣" }, note: "" },
        ],
      },
    ],
    ...overrides,
  };
  const actions: any[] = [],
    lookups: any[] = [],
    unexpected: string[] = [];
  let loseComplete = false;
  const response = () => ({
    ...order,
    allowed_actions:
      order.payment_review_required || order.refund
        ? ["sync_payment"]
        : order.cancel_requested
          ? ["approve_cancel", "deny_cancel"]
          : order.status === "completed"
            ? []
            : order.fulfillment_type === "delivery" && order.status === "ready"
              ? ["dispatch", "refund"]
              : order.payment_status === "paid"
                ? [
                    "complete",
                    ...(order.payment_method === "wechat" ? ["refund"] : []),
                  ]
                : ["confirm_payment"],
  });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname.replace("/api/v1", "");
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
    if (path === "/merchant/orders")
      return send({
        results: [response()],
        next: null,
        counts: { all: 1, active: order.status === "completed" ? 0 : 1 },
      });
    if (path === "/merchant/metrics")
      return send({
        today: { revenue_cents: 0, orders_created: 1, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    if (path === "/merchant/stalls/978/pickup-lookup") {
      lookups.push(req.postDataJSON());
      return send(response());
    }
    if (path === "/merchant/orders/handoff-order/action") {
      const body = req.postDataJSON();
      actions.push(body);
      if (body.action === "confirm_payment") order.payment_status = "paid";
      if (body.action === "complete") {
        order.status = "completed";
        if (loseComplete) {
          loseComplete = false;
          return route.abort("failed");
        }
      }
      return send(response());
    }
    unexpected.push(`${req.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  return {
    order,
    actions,
    lookups,
    unexpected,
    loseComplete: () => {
      loseComplete = true;
    },
  };
}
async function openReady(page: Page) {
  await page.goto("/merchant/orders");
  await page
    .getByRole("group", { name: "订单阶段", exact: true })
    .getByRole("button", { name: /待取餐/ })
    .click();
  await page.getByRole("button", { name: "核对取餐码", exact: true }).click();
  return page.getByRole("dialog", { name: "订单详情", exact: true });
}

test("handoff facts precede redemption and stay readable at four widths", async ({
  page,
}, info) => {
  const data = await fixture(page);
  await page.setViewportSize({ width: 360, height: 844 });
  const drawer = await openReady(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });
    await expect(
      drawer.getByRole("region", { name: "核对餐点" }),
    ).toContainText("第 1 份 · 辣度：不辣 · 不要香菜");
    await expect(
      drawer.getByRole("region", { name: "核对餐点" }),
    ).toContainText("分开装袋");
    await expect(drawer.locator(".m-order-payment-check")).toContainText(
      "已收金额¥24",
    );
    await expect(drawer.locator(".m-order-payment-check")).toContainText(
      "到摊已收款",
    );
    const meal = await drawer.locator(".m-order-meal-check").boundingBox();
    const payment = await drawer
      .locator(".m-order-payment-check")
      .boundingBox();
    const submit = await drawer
      .getByRole("button", { name: "核销并完成", exact: true })
      .boundingBox();
    expect(meal!.y + meal!.height).toBeLessThanOrEqual(payment!.y + 1);
    expect(payment!.y + payment!.height).toBeLessThanOrEqual(submit!.y);
    expect(submit!.height).toBeGreaterThanOrEqual(44);
    if (width <= 390)
      expect(submit!.y + submit!.height).toBeLessThanOrEqual(844);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`handoff-${width}.png`),
      animations: "disabled",
    });
  }
  await expect(
    drawer.getByRole("list", { name: "订单进度" }),
  ).not.toBeVisible();
  await drawer.getByText("订单记录与联系信息", { exact: true }).click();
  await expect(drawer.getByRole("list", { name: "订单进度" })).toBeVisible();
  await expect(drawer.getByRole("link", { name: /联系顾客/ })).toHaveAttribute(
    "href",
    "tel:13800000000",
  );
  expect(data.actions).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test("pickup lookup keeps amount visible while cash confirmation and redemption remain separate", async ({
  page,
}) => {
  const data = await fixture(page, { payment_status: "unpaid", paid_at: null });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/orders");
  await page.getByRole("button", { name: "取餐码查单", exact: true }).click();
  await page.getByLabel("8 位取餐码", { exact: true }).fill("12345678");
  await page.getByRole("button", { name: "查找待取餐订单" }).click();
  const drawer = page.getByRole("dialog", { name: "订单详情", exact: true });
  await expect(drawer.locator(".m-order-payment-check")).toContainText(
    "应收金额¥24",
  );
  await expect(drawer.getByRole("button", { name: "核销并完成" })).toHaveCount(
    0,
  );
  expect(data.actions).toEqual([]);
  await drawer.getByRole("button", { name: "收款 ¥24", exact: true }).click();
  await page
    .getByRole("dialog", { name: "确认这笔线下收款" })
    .getByRole("button", { name: "确认收款", exact: true })
    .click();
  await expect(drawer.locator(".m-order-payment-check")).toContainText(
    "已收金额¥24",
  );
  await expect(
    drawer.getByPlaceholder("输入取餐码", { exact: true }),
  ).toHaveValue("12345678");
  expect(data.order.status).toBe("ready");
  await drawer.getByRole("button", { name: "核销并完成", exact: true }).click();
  await expect.poll(() => data.order.status).toBe("completed");
  expect(data.actions.map((row) => row.action)).toEqual([
    "confirm_payment",
    "complete",
  ]);
  expect(new Set(data.actions.map((row) => row.idempotency_key)).size).toBe(2);
  expect(data.lookups).toEqual([{ pickup_code: "12345678" }]);
  expect(data.unexpected).toEqual([]);
});

test("moved pickup address remains expanded ahead of redemption", async ({
  page,
}) => {
  await fixture(page, { location_changed: true, current_address: "东门新棚" });
  const drawer = await openReady(page);
  await expect(drawer.getByText("南门橙色棚", { exact: true })).toBeVisible();
  await expect(drawer.getByText(/当前摊位位置为/)).toBeVisible();
  const warning = await drawer
    .locator(".m-order-handoff-location")
    .boundingBox();
  const submit = await drawer
    .getByRole("button", { name: "核销并完成" })
    .boundingBox();
  expect(warning!.y + warning!.height).toBeLessThan(submit!.y);
});

test("cancellation and financial holds remain outside collapsed records without a handoff action", async ({
  page,
}) => {
  const data = await fixture(page, {
    cancel_requested: true,
    cancel_reason: "今天不能来取",
    payment_review_required: true,
    payment_method: "wechat",
    payment: { status: "review" },
    financial_hold_reason: "付款待运营核对",
  });
  await page.goto("/merchant/orders");
  await page.getByRole("button", { name: /1 笔取消申请/ }).click();
  await page.getByRole("button", { name: "处理取消申请", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "订单详情", exact: true });
  await expect(drawer.getByText("今天不能来取", { exact: true })).toBeVisible();
  await expect(
    drawer.getByText("付款待运营核对", { exact: true }),
  ).toBeVisible();
  await expect(
    drawer.getByRole("button", { name: /核销并完成|^收款 ¥/ }),
  ).toHaveCount(0);
  expect(data.actions).toEqual([]);
});

test("a lost redemption result still replays its original code and idempotency key", async ({
  page,
}) => {
  const data = await fixture(page);
  data.loseComplete();
  const drawer = await openReady(page);
  await drawer.getByPlaceholder("输入取餐码", { exact: true }).fill("12345678");
  await drawer.getByRole("button", { name: "核销并完成", exact: true }).click();
  await expect.poll(() => data.actions.length).toBe(1);
  await page.reload();
  await page.getByRole("button", { name: /1 笔操作结果待确认/ }).click();
  await page
    .getByRole("button", { name: "确认原操作结果", exact: true })
    .click();
  await expect.poll(() => data.actions.length).toBe(2);
  expect(data.actions[1]).toEqual(data.actions[0]);
  expect(data.actions[1].pickup_code).toBe("12345678");
  expect(data.unexpected).toEqual([]);
});

test("delivery handoff keeps its destination visible and refund confirmation reachable", async ({
  page,
}) => {
  const data = await fixture(page, {
    fulfillment_type: "delivery",
    status: "arrived",
    payment_method: "wechat",
    payment: { status: "paid" },
    delivery_point_name: "东门交接点",
    delivery_point_address: "门卫亭旁",
    recipient_name: "小林",
    delivery_fee_cents: 200,
    total_cents: 2600,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/orders");
  await page
    .getByRole("group", { name: "订单阶段", exact: true })
    .getByRole("button", { name: /待取餐/ })
    .click();
  await page.getByRole("button", { name: "核对收餐码", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "订单详情", exact: true });
  await expect(drawer.locator(".m-order-handoff-location")).toContainText(
    "东门交接点 · 门卫亭旁",
  );
  await expect(drawer.locator(".m-order-handoff-location")).toContainText(
    "收餐人：小林",
  );
  await expect(drawer.locator(".m-order-payment-check")).toContainText(
    "已收金额¥26",
  );
  await expect(drawer.getByRole("button", { name: /^收款 ¥/ })).toHaveCount(0);
  await drawer.locator(".m-order-more > summary").click();
  await drawer
    .getByRole("button", { name: "全额原路退款", exact: true })
    .click();
  const confirmation = page.getByRole("dialog", {
    name: "确认全额原路退款",
    exact: true,
  });
  await expect(confirmation).toBeVisible();
  expect(data.actions).toEqual([]);
  await confirmation
    .getByRole("button", { name: "关闭操作确认", exact: true })
    .click();
  await drawer.getByPlaceholder("输入收餐码", { exact: true }).fill("12345678");
  await drawer.getByRole("button", { name: "核销并完成", exact: true }).click();
  await expect.poll(() => data.order.status).toBe("completed");
  expect(data.actions.map((row) => row.action)).toEqual(["complete"]);
  expect(data.unexpected).toEqual([]);
});

test("long portion requirements scroll normally without hiding the code input", async ({
  page,
}) => {
  await fixture(page, {
    items: [
      {
        product_id: 979,
        name: "香菇鸡肉饭",
        quantity: 10,
        unit_price_cents: 1200,
        portions: Array.from({ length: 10 }, (_, index) => ({
          options: { 辣度: index % 2 ? "微辣" : "不辣" },
          note: `第${index + 1}份单独装袋，不要香菜，请标注编号`,
        })),
      },
    ],
    total_cents: 12000,
    items_total_cents: 12000,
  });
  await page.setViewportSize({ width: 360, height: 640 });
  const drawer = await openReady(page);
  await expect(drawer.locator(".portion-order-summary li")).toHaveCount(10);
  await drawer.getByPlaceholder("输入取餐码", { exact: true }).fill("12345678");
  const submit = drawer.getByRole("button", {
    name: "核销并完成",
    exact: true,
  });
  await submit.scrollIntoViewIfNeeded();
  const hittable = await submit.evaluate((element) => {
    const r = element.getBoundingClientRect();
    return element.contains(
      document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2),
    );
  });
  expect(hittable).toBe(true);
  await assertNoHorizontalOverflow(page);
});
