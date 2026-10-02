import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Isolated UI contracts. All API traffic is intercepted; these fixtures never
// create a live order, change an account, or send a payment/refund to WeChat.
const time = new Date().toISOString();
const user = {
  id: 982,
  username: "delivery_ui_merchant",
  display_name: "配送测试商家",
  is_merchant: true,
  is_staff: false,
};
const point = {
  id: 91,
  name: "图书馆东侧交接点",
  address: "图书馆东门雨棚旁的指定交接处",
  latitude: 30,
  longitude: 120,
  area_id: 1,
};
function makeSettings(overrides: Record<string, unknown> = {}) {
  return {
    enabled: false,
    approved: false,
    available: false,
    reason: "尚未完成运营配送核验，微信支付尚未开通。",
    fee_cents: 300,
    min_order_cents: 1500,
    eta_min_minutes: 25,
    eta_max_minutes: 45,
    starts_at: "11:00",
    ends_at: "14:00",
    capacity: 5,
    points: [point],
    point_ids: [point.id],
    available_points: [point],
    ...overrides,
  };
}
function makeOrder(overrides: Record<string, unknown> = {}) {
  return {
    id: "delivery-ui-order",
    number: "UI-DELIVERY-0001",
    stall_id: 982,
    stall_name: "校园配送测试摊位",
    status: "ready",
    fulfillment_type: "delivery",
    payment_method: "wechat",
    payment_status: "paid",
    payment_review_required: false,
    payment: { id: "ui-payment", status: "paid" },
    refund: null,
    items_total_cents: 1600,
    delivery_fee_cents: 300,
    total_cents: 1900,
    created_at: time,
    accepted_at: time,
    ready_at: time,
    paid_at: time,
    completed_at: null,
    dispatched_at: null,
    arrived_at: null,
    expires_at: new Date(Date.now() + 300_000).toISOString(),
    pickup_address: "测试商家的原取餐位置",
    current_address: "测试商家的原取餐位置",
    delivery_point_name: point.name,
    delivery_point_address: point.address,
    delivery_point_id: point.id,
    delivery_point_latitude: point.latitude,
    delivery_point_longitude: point.longitude,
    delivery_eta_min_at: new Date(Date.now() + 25 * 60000).toISOString(),
    delivery_eta_max_at: new Date(Date.now() + 45 * 60000).toISOString(),
    recipient_name: "测试同学",
    delivery_issue: "",
    location_changed: false,
    note: "",
    contact_phone: "13000000000",
    cancel_requested: false,
    cancel_reason: "",
    review: null,
    items: [
      {
        product_id: 982,
        name: "测试鲜蔬炒面",
        image: "/images/food-cold-noodles.jpg",
        unit_price_cents: 1600,
        quantity: 1,
      },
    ],
    ...overrides,
  };
}
async function stub(
  page: Page,
  options: {
    orders?: any[];
    settings?: any;
    path?: string;
    action?: (body: any, order: any) => { status?: number; body: any };
    patch?: (body: any) => { status?: number; body: any };
  } = {},
) {
  const orders = options.orders || [];
  let settings = options.settings || makeSettings();
  const unexpected: string[] = [];
  const actions: any[] = [];
  const patches: any[] = [];
  const stall = {
    id: 982,
    name: "校园配送测试摊位",
    image: "/images/food-cold-noodles.jpg",
    area_name: "示例校园",
    area_id: 1,
    status: "open",
    last_confirmed_at: time,
    transaction_enabled: true,
    prep_minutes: 10,
    address: "测试商家的原取餐位置",
    latitude: 30,
    longitude: 120,
    description: "隔离界面测试",
    contact_phone: "",
    closes_at: "",
    merchant_name: "测试商家",
    qualification_note: "测试夹具",
    rating: null,
    review_count: 0,
    products: [],
    reviews: [],
    delivery: settings,
  };
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
    if (path === "/merchant/stalls")
      return send([{ ...stall, delivery: settings }]);
    if (path === "/merchant/orders" && request.method() === "GET")
      return send(orders);
    if (path === "/merchant/metrics")
      return send({
        revenue_cents: 1900,
        today: {
          revenue_cents: 1900,
          orders_created: orders.length,
          orders_completed: 0,
        },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    if (path === "/merchant/stalls/982/services") {
      if (request.method() === "PATCH")
        settings = {
          ...settings,
          enabled: request.postDataJSON().delivery_enabled,
        };
      return send({
        mode: "simulation",
        simulation_available: true,
        online_payment_enabled: true,
        delivery_enabled: settings.enabled,
        wechat_payment: { available: true, mode: "simulation" },
        delivery: settings,
      });
    }
    if (path === "/merchant/stalls/982/delivery") {
      if (request.method() === "GET") return send(settings);
      if (request.method() === "PATCH") {
        const body = request.postDataJSON();
        patches.push(body);
        const result = options.patch?.(body);
        if (result) return send(result.body, result.status || 200);
        settings = { ...settings, ...body };
        return send(settings);
      }
    }
    if (
      /^\/merchant\/orders\/[^/]+\/action$/.test(path) &&
      request.method() === "POST"
    ) {
      const body = request.postDataJSON();
      actions.push(body);
      const order = orders.find((o) => o.id === path.split("/")[3]);
      const result = options.action?.(body, order);
      if (result) return send(result.body, result.status || 200);
      if (body.action === "dispatch")
        Object.assign(order, { status: "delivering", dispatched_at: time });
      if (body.action === "arrive")
        Object.assign(order, { status: "arrived", arrived_at: time });
      if (body.action === "complete")
        Object.assign(order, { status: "completed", completed_at: time });
      if (body.action === "report_delivery_issue")
        order.delivery_issue = body.reason;
      if (body.action === "resolve_delivery_issue") order.delivery_issue = "";
      return send(order);
    }
    unexpected.push(`${request.method()} ${path}`);
    return send(
      { detail: "Unexpected request in isolated delivery UI fixture" },
      500,
    );
  });
  await page.goto(options.path || "/merchant/orders?filter=all");
  await expect(page.locator(".m-shell")).toBeVisible();
  if (!options.path?.includes("store"))
    await expect(page.locator(".merchant-order")).toHaveCount(orders.length);
  return { unexpected, actions, patches, orders };
}

test("unpaid delivery cannot be accepted or manually marked paid; pickup remains separate", async ({
  page,
}) => {
  const fixture = await stub(page, {
    orders: [
      makeOrder({
        id: "unpaid",
        status: "pending_payment",
        payment_status: "unpaid",
        payment: null,
        paid_at: null,
      }),
      makeOrder({
        id: "pickup",
        number: "UI-PICKUP",
        fulfillment_type: "pickup",
        payment_method: "offline",
        payment_status: "unpaid",
        payment: null,
        paid_at: null,
        total_cents: 1600,
        delivery_fee_cents: 0,
      }),
    ],
  });
  const delivery = page.locator(".merchant-order", {
    hasText: "UI-DELIVERY-0001",
  });
  await expect(delivery).toContainText("待付款");
  await expect(delivery).toContainText("系统确认后才能接单");
  await expect(
    delivery.getByRole("button", { name: "确认接单", exact: true }),
  ).toHaveCount(0);
  await expect(
    delivery.getByRole("button", { name: /^确认已收到/ }),
  ).toHaveCount(0);
  await expect(
    page
      .locator(".merchant-order", { hasText: "UI-PICKUP" })
      .getByRole("button", { name: "确认已收到 ¥16", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("group", { name: "取餐方式筛选" })
    .getByRole("button", { name: "商家自配送", exact: true })
    .click();
  await expect(page.locator(".merchant-order")).toHaveCount(1);
  await expect(page.locator(".m-attention")).toHaveAttribute(
    "data-pending-count",
    "0",
  );
  expect(fixture.actions).toEqual([]);
  expect(fixture.unexpected).toEqual([]);
});

test("paid delivery advances departure and arrival, then needs receipt code before completion", async ({
  page,
}, testInfo) => {
  const fixture = await stub(page, { orders: [makeOrder()] });
  await expect(page.locator(".merchant-order-status")).toHaveText("待配送");
  await expect(
    page.getByRole("button", { name: "核销并完成", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "出发送餐", exact: true }).click();
  const departure = page.getByRole("dialog", {
    name: "确认开始配送",
    exact: true,
  });
  await expect(departure).toContainText("实际出发");
  await departure
    .getByRole("button", { name: "确认已出发", exact: true })
    .click();
  await expect(page.locator(".merchant-order-status")).toHaveText("配送中");
  await expect(
    page.getByRole("button", { name: "核销并完成", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "已到交接点", exact: true }).click();
  const arrival = page.getByRole("dialog", {
    name: "确认已到达交接点",
    exact: true,
  });
  await expect(arrival).toContainText("仍需顾客确认收餐");
  await arrival
    .getByRole("button", { name: "确认已到达", exact: true })
    .click();
  await expect(page.locator(".merchant-order-status")).toHaveText("已到交接点");
  await expect(page.locator(".m-delivery-destination")).toContainText(
    point.address,
  );
  await expect(page.getByRole("button", { name: /^确认已收到/ })).toHaveCount(
    0,
  );
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  while (
    await page.getByRole("button", { name: "关闭提示", exact: true }).count()
  )
    await page
      .getByRole("button", { name: "关闭提示", exact: true })
      .first()
      .click();
  await page.screenshot({
    path: testInfo.outputPath("merchant-delivery-orders-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.locator(".merchant-order").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("merchant-delivery-arrived-mobile.png"),
  });
  await page.getByRole("button", { name: /^查看订单 .* 详情$/ }).click();
  await page.screenshot({
    path: testInfo.outputPath("merchant-delivery-detail-mobile.png"),
  });
  await page.getByRole("button", { name: "关闭订单详情", exact: true }).click();
  await page
    .getByLabel("核验顾客的 8 位收餐码", { exact: true })
    .fill("12345678");
  await page.getByRole("button", { name: "核销并完成", exact: true }).click();
  await expect(page.locator(".merchant-order-status")).toHaveText("已完成");
  expect(fixture.actions).toEqual([
    { action: "dispatch" },
    { action: "arrive" },
    { action: "complete", pickup_code: "12345678" },
  ]);
  expect(fixture.unexpected).toEqual([]);
});

test("delivery details preserve point and split fees; reporting issues never completes the order", async ({
  page,
}) => {
  const fixture = await stub(page, {
    orders: [makeOrder({ status: "delivering", dispatched_at: time })],
  });
  await page.locator(".merchant-order .m-order-more > summary").click();
  await page.getByRole("button", { name: "配送遇到问题", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "记录配送异常",
    exact: true,
  });
  await dialog
    .getByRole("textbox", { name: "处理说明", exact: true })
    .fill("交接点临时封闭，已联系顾客协商");
  await dialog
    .getByRole("button", { name: "保存并通知顾客", exact: true })
    .click();
  await expect(page.locator(".merchant-order-status")).toHaveText("配送中");
  await expect(page.locator(".merchant-order")).toContainText("交接点临时封闭");
  await expect(
    page.getByRole("button", { name: "已到交接点", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: /^查看订单 .* 详情$/ }).click();
  const drawer = page.getByRole("dialog", { name: "订单详情", exact: true });
  await expect(drawer).toContainText(point.address);
  await expect(
    drawer.locator(".m-orders-amount > div", { hasText: "商品金额" }),
  ).toContainText("¥16");
  await expect(
    drawer.locator(".m-orders-amount > div", { hasText: "配送费" }),
  ).toContainText("¥3");
  await expect(drawer.locator(".m-orders-amount .total")).toContainText("¥19");
  await expect(drawer).not.toContainText("无配送费");
  expect(fixture.actions).toEqual([
    {
      action: "report_delivery_issue",
      reason: "交接点临时封闭，已联系顾客协商",
    },
  ]);
  expect(fixture.unexpected).toEqual([]);
});

test("delivery issue blocks receipt until an explicit resolution is recorded", async ({
  page,
}) => {
  const fixture = await stub(page, {
    orders: [
      makeOrder({
        status: "arrived",
        arrived_at: time,
        delivery_issue: "暂时联系不上顾客",
      }),
    ],
  });
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
  await page
    .getByRole("button", { name: "异常已解决，继续处理", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "确认配送异常已解决",
    exact: true,
  });
  await dialog
    .getByRole("textbox", { name: "处理说明", exact: true })
    .fill("已与顾客电话确认，顾客正在交接点等候");
  await dialog
    .getByRole("button", { name: "记录解决，继续履约", exact: true })
    .click();
  await expect(
    page.getByLabel("核验顾客的 8 位收餐码", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".merchant-order-status")).toHaveText("已到交接点");
  expect(fixture.actions).toEqual([
    {
      action: "resolve_delivery_issue",
      reason: "已与顾客电话确认，顾客正在交接点等候",
    },
  ]);
  expect(fixture.unexpected).toEqual([]);
});

test("new delivery payment confirmation becomes an actionable arrival", async ({
  page,
}) => {
  const order = makeOrder({
    status: "pending_payment",
    payment_status: "unpaid",
    payment: null,
    paid_at: null,
  });
  const fixture = await stub(page, { orders: [order] });
  await expect(page.locator(".m-attention")).toHaveAttribute(
    "data-pending-count",
    "0",
  );
  Object.assign(order, {
    status: "pending",
    payment_status: "paid",
    payment: { status: "paid" },
    paid_at: time,
  });
  await page.getByRole("button", { name: "刷新订单", exact: true }).click();
  await expect(page.locator(".m-attention")).toHaveAttribute(
    "data-pending-count",
    "1",
  );
  await expect(
    page.getByRole("button", { name: "确认接单", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".m-attention-arrival")).toBeVisible();
  expect(fixture.unexpected).toEqual([]);
});

test("paid rejection announces refund in progress without claiming money returned", async ({
  page,
}) => {
  const order = makeOrder({
    status: "pending",
    accepted_at: null,
    ready_at: null,
  });
  const fixture = await stub(page, {
    orders: [order],
    action: (_body, value) => {
      Object.assign(value, {
        status: "rejected",
        payment_status: "refunding",
        refund: {
          status: "processing",
          reason: "无法配送",
          amount_cents: 1900,
        },
      });
      return { body: value };
    },
  });
  await page.locator(".merchant-order .m-order-more > summary").click();
  await page.getByRole("button", { name: "暂时无法接单", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "暂时无法接下这一单？",
    exact: true,
  });
  await expect(dialog).toContainText("含配送费的全额原路退款");
  await dialog.getByRole("button", { name: "确认拒单", exact: true }).click();
  await expect(page.locator(".merchant-payment-info")).toContainText(
    "退款处理中",
  );
  await expect(page.locator(".toast.info")).toContainText("等待微信确认");
  await expect(page.locator(".toast.success")).toHaveCount(0);
  expect(fixture.actions).toHaveLength(1);
  expect(fixture.unexpected).toEqual([]);
});

test("settings save readiness intent and exact cents without granting operator approval", async ({
  page,
}, testInfo) => {
  const fixture = await stub(page, { path: "/merchant/store" });
  await page.getByRole("switch", { name: "外卖配送", exact: true }).click();
  const panel = page.getByRole("region", { name: "外卖基础设置", exact: true });
  await expect(panel).toContainText("暂未开放配送");
  await expect(page.locator(".service-availability")).toContainText(
    "尚未完成运营配送核验",
  );
  await panel.locator(".delivery-advanced > summary").click();
  await panel
    .getByRole("textbox", { name: "配送费（元）", exact: true })
    .fill("2.50");
  await panel
    .getByRole("textbox", { name: "餐品起送金额（元）", exact: true })
    .fill("18.00");
  await panel
    .getByRole("spinbutton", { name: "同时配送容量（单）", exact: true })
    .fill("8");
  await panel
    .getByRole("button", { name: "保存配送设置", exact: true })
    .click();
  await expect.poll(() => fixture.patches.length).toBe(1);
  expect(fixture.patches[0]).toEqual({
    fee_cents: 250,
    min_order_cents: 1800,
    capacity: 8,
  });
  await expect(panel).toContainText("暂未开放配送");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  while (
    await page.getByRole("button", { name: "关闭提示", exact: true }).count()
  )
    await page
      .getByRole("button", { name: "关闭提示", exact: true })
      .first()
      .click();
  await panel.screenshot({
    path: testInfo.outputPath("merchant-delivery-settings-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await panel.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({
    path: testInfo.outputPath("merchant-delivery-settings-mobile.png"),
  });
  expect(fixture.unexpected).toEqual([]);
});

test("settings retain draft and display server rejection without fake success", async ({
  page,
}) => {
  const fixture = await stub(page, {
    path: "/merchant/store",
    patch: () => ({
      status: 409,
      body: { detail: "交接点已暂停，请重新选择。" },
    }),
  });
  await page.getByRole("switch", { name: "外卖配送", exact: true }).click();
  const panel = page.getByRole("region", { name: "外卖基础设置", exact: true });
  await panel
    .getByRole("textbox", { name: "配送费（元）", exact: true })
    .fill("4");
  await panel
    .getByRole("button", { name: "保存配送设置", exact: true })
    .click();
  await expect(panel.getByRole("alert")).toHaveText(
    "交接点已暂停，请重新选择。",
  );
  await expect(
    panel.getByRole("textbox", { name: "配送费（元）", exact: true }),
  ).toHaveValue("4");
  await expect(page.locator(".toast.success")).toHaveCount(0);
  expect(fixture.patches).toHaveLength(1);
  expect(fixture.unexpected).toEqual([]);
});
