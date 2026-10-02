import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Isolated UI fixtures: all API calls intercepted. No real settings, money or delivery.
const user = {
  id: 994,
  username: "merchant_simulation_ui",
  display_name: "模拟商家",
  is_merchant: true,
  is_staff: false,
};
const point = {
  id: 91,
  name: "模拟南门交接点",
  address: "示例校园南门雨棚",
  area_id: 1,
};
const timestamp = new Date().toISOString();
function delivery() {
  return {
    mode: "simulation",
    enabled: false,
    approved: false,
    available: false,
    reason: "请先开启模拟配送",
    fee_cents: 200,
    min_order_cents: 0,
    capacity: 5,
    starts_at: "00:00",
    ends_at: "23:59",
    eta_min_minutes: 20,
    eta_max_minutes: 40,
    point_ids: [91],
    points: [point],
    available_points: [point],
  };
}
function order() {
  return {
    id: "sim-order",
    number: "SIM-UI-ORDER",
    mode: "simulation",
    stall_id: 994,
    stall_name: "模拟好味摊",
    status: "ready",
    fulfillment_type: "delivery",
    payment_method: "wechat",
    payment_status: "paid",
    payment_review_required: false,
    payment: { id: "sim-payment", status: "paid", mode: "simulation" },
    refund: null,
    total_cents: 1800,
    items_total_cents: 1600,
    delivery_fee_cents: 200,
    created_at: timestamp,
    paid_at: timestamp,
    accepted_at: timestamp,
    ready_at: timestamp,
    delivery_point_name: point.name,
    delivery_point_address: point.address,
    delivery_issue: "",
    recipient_name: "模拟同学",
    contact_phone: "13000000000",
    cancel_requested: false,
    cancel_reason: "",
    pickup_address: "示例摊位",
    current_address: "示例摊位",
    note: "",
    review: null,
    items: [
      {
        product_id: 9,
        name: "模拟炒面",
        quantity: 1,
        unit_price_cents: 1600,
        image: "/images/food-cold-noodles.jpg",
      },
    ],
  };
}
async function setup(
  page: Page,
  options: {
    live?: boolean;
    approved?: boolean;
    orders?: any[];
    path?: string;
    failToggle?: boolean;
  } = {},
) {
  let settings: any = {
    ...delivery(),
    approved: !!options.approved,
    mode: options.live ? "live" : "simulation",
  };
  let services: any = {
    mode: settings.mode,
    simulation_available: !options.live,
    online_payment_enabled: false,
    delivery_enabled: false,
    wechat_payment: {
      mode: settings.mode,
      available: false,
      reason: "尚未开通",
    },
    delivery: settings,
  };
  const orders = options.orders || [];
  const calls: { path: string; method: string; body: any; search: string }[] =
      [],
    unexpected: string[] = [];
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname.replace("/api/v1", ""),
      method = request.method(),
      body = request.postDataJSON();
    calls.push({ path, method, body, search: url.search });
    const send = (value: any, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(value),
      });
    if (path === "/config")
      return send({
        brand: "烟火地图",
        demo_mode: !options.live,
        services_simulation_enabled: !options.live,
        user,
        areas: [],
        amap_key: "",
        amap_proxy: "",
      });
    if (path === "/auth/me") return send(user);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/merchant/stalls")
      return send([
        {
          id: 994,
          name: "模拟好味摊",
          image: "/images/food-cold-noodles.jpg",
          area_name: "示例校园",
          status: "open",
          last_confirmed_at: timestamp,
          transaction_enabled: true,
          prep_minutes: 10,
          address: "南门示例摊位",
          latitude: 30,
          longitude: 120,
          description: "模拟资料",
          contact_phone: "",
          closes_at: "",
          merchant_name: "模拟商户",
          services,
          delivery: settings,
          products: [],
          reviews: [],
        },
      ]);
    if (path === "/merchant/orders") return send(orders);
    if (path === "/merchant/metrics")
      return send({
        mode: settings.mode,
        revenue_cents: 1800,
        offline_revenue_cents: 0,
        online_revenue_cents: 1800,
        refund_cents: 0,
        orders_created: 1,
        orders_completed: 0,
        average_order_cents: 1800,
        followers: 0,
        today: { revenue_cents: 1800, orders_created: 1, orders_completed: 0 },
        series: [
          {
            date: "2026-09-27",
            orders_created: 1,
            orders_completed: 0,
            revenue_cents: 1800,
          },
        ],
        top_products: [],
        recent_payments: [],
      });
    if (path === "/merchant/stalls/994/services") {
      if (method === "PATCH") {
        if (options.failToggle)
          return send({ detail: "保存失败，请重试" }, 409);
        services = { ...services, ...body };
        if ("delivery_enabled" in body)
          settings = { ...settings, enabled: body.delivery_enabled };
        services.wechat_payment = {
          ...services.wechat_payment,
          available: services.online_payment_enabled,
        };
        settings.available =
          services.online_payment_enabled && settings.enabled;
        settings.reason = settings.available ? "" : "请先开启模拟支付";
        services.delivery = settings;
      }
      return send(services);
    }
    if (path === "/merchant/stalls/994/delivery") {
      if (method === "PATCH") {
        settings = { ...settings, ...body };
        services.delivery = settings;
        services.delivery_enabled = settings.enabled;
      }
      return send(settings);
    }
    if (path === "/merchant/orders/sim-order/refund") {
      Object.assign(orders[0], {
        status: "cancelled",
        payment_status: "refunding",
        refund: {
          id: "sim-refund",
          mode: "simulation",
          status: "processing",
          amount_cents: 1800,
          reason: body.reason,
        },
      });
      return send(orders[0]);
    }
    if (path === "/merchant/orders/sim-order/refunds/simulate") {
      Object.assign(orders[0], {
        payment_status: "refunded",
        refund: { ...orders[0].refund, status: "success" },
      });
      return send(orders[0]);
    }
    unexpected.push(`${method} ${path}`);
    return send({ detail: "unexpected isolated UI request" }, 500);
  });
  await page.goto(options.path || "/merchant/store");
  await expect(page.locator(".m-stall-bar")).toBeVisible();
  return { calls, unexpected };
}

test("merchant service switches save immediately, defaults are ready and advanced controls stay grouped", async ({
  page,
}, testInfo) => {
  const fixture = await setup(page);
  const services = page.getByRole("region", { name: "经营服务", exact: true });
  await expect(services).toContainText("不会真实扣款");
  const payment = page.getByRole("switch", { name: "线上支付", exact: true }),
    dispatch = page.getByRole("switch", { name: "外卖配送", exact: true });
  await payment.click();
  await expect(payment).toBeChecked();
  await dispatch.click();
  await expect(dispatch).toBeChecked();
  await expect(page.getByLabel("配送费（元）", { exact: true })).toHaveValue(
    "2",
  );
  await expect(
    page.getByLabel("同时配送容量（单）", { exact: true }),
  ).toBeHidden();
  await expect(page.getByLabel("店铺简介", { exact: true })).toBeHidden();
  await expect(page.getByRole("button", { name: /开始出摊/ })).toHaveCount(0);
  await expect(page.getByText("新订单到达后会在这里显示")).toBeVisible();
  expect(
    fixture.calls
      .filter((c) => c.path.endsWith("/services") && c.method === "PATCH")
      .map((c) => c.body),
  ).toEqual([{ online_payment_enabled: true }, { delivery_enabled: true }]);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    expect((await payment.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  while (
    await page.getByRole("button", { name: "关闭提示", exact: true }).count()
  )
    await page
      .getByRole("button", { name: "关闭提示", exact: true })
      .first()
      .click();
  await services.screenshot({
    path: testInfo.outputPath("merchant-services-desktop.png"),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await services.evaluate((element) =>
    element.scrollIntoView({ block: "start" }),
  );
  await page.screenshot({
    path: testInfo.outputPath("merchant-services-mobile.png"),
  });
  await page.locator(".delivery-advanced > summary").click();
  await page.getByLabel("同时配送容量（单）", { exact: true }).fill("7");
  await page.getByRole("button", { name: "保存配送设置", exact: true }).click();
  await expect
    .poll(
      () =>
        fixture.calls.filter(
          (c) => c.path.endsWith("/delivery") && c.method === "PATCH",
        ).length,
    )
    .toBe(1);
  expect(
    fixture.calls.find(
      (c) => c.path.endsWith("/delivery") && c.method === "PATCH",
    )!.body.capacity,
  ).toBe(7);
  expect(fixture.unexpected).toEqual([]);
});

test("rejected service toggles retain actual state and report errors without optimistic success", async ({
  page,
}) => {
  const fixture = await setup(page, { failToggle: true });
  const payment = page.getByRole("switch", { name: "线上支付", exact: true });
  await payment.click();
  await expect(page.getByRole("alert")).toContainText("保存失败");
  await expect(payment).not.toBeChecked();
  await expect(page.locator(".toast.success")).toHaveCount(0);
  expect(fixture.unexpected).toEqual([]);
});

test("real merchant sees administrator payment setup and cannot accidentally use simulation switches", async ({
  page,
}) => {
  const fixture = await setup(page, { live: true });
  await expect(
    page.getByRole("switch", { name: "线上支付", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("switch", { name: "外卖配送", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("region", { name: "经营服务", exact: true }),
  ).toContainText("无需填写密钥");
  await expect(page.locator(".service-simulation-note")).toHaveCount(0);
  await expect(page.getByLabel("配送费（元）", { exact: true })).toBeVisible();
  expect(fixture.calls.filter((c) => c.method === "PATCH")).toEqual([]);
  expect(fixture.unexpected).toEqual([]);
});

test("simulated refund pending can recover using the original refund; low-frequency actions are collapsed", async ({
  page,
}) => {
  const fixture = await setup(page, {
    orders: [order()],
    path: "/merchant/orders?filter=all",
  });
  await expect(
    page.getByRole("button", { name: "模拟出发送餐", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "全额原路退款", exact: true }),
  ).toBeHidden();
  await expect(page.locator(".m-simulation-badge")).toContainText(
    "不扣款、不配送",
  );
  await page.locator(".merchant-order .m-order-more > summary").click();
  await page.getByRole("button", { name: "全额原路退款", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "确认模拟全额退款",
    exact: true,
  });
  await dialog.getByLabel("退款原因", { exact: true }).fill("模拟订单取消");
  await dialog.locator(".simulation-faults > summary").click();
  await dialog
    .getByRole("combobox", { name: "退款测试结果", exact: true })
    .selectOption("pending");
  await dialog
    .getByRole("button", { name: "模拟退款 ¥18", exact: true })
    .click();
  await expect(page.locator(".merchant-payment-info")).toContainText(
    "模拟退款尚未完成",
  );
  expect(
    fixture.calls.find(
      (c) => c.path.endsWith("/refund") && c.method === "POST",
    )!.body,
  ).toEqual({ reason: "模拟订单取消", simulation_outcome: "pending" });
  await page.getByRole("button", { name: "模拟退款成功", exact: true }).click();
  await expect(page.locator(".merchant-payment-info")).toContainText(
    "模拟退款已完成",
  );
  expect(
    fixture.calls.find((c) => c.path.endsWith("/refunds/simulate"))!.body,
  ).toEqual({ refund_id: "sim-refund", outcome: "success" });
  await expect(
    page.getByRole("button", { name: "模拟退款成功", exact: true }),
  ).toHaveCount(0);
  expect(fixture.unexpected).toEqual([]);
});

test("simulation analytics explicitly requests simulation records and labels exported data", async ({
  page,
}) => {
  const fixture = await setup(page, { path: "/merchant/analytics" });
  await expect(page.locator(".m-analytics .m-info-banner")).toContainText(
    "模拟经营数据",
  );
  expect(
    fixture.calls
      .filter((c) => c.path === "/merchant/metrics")
      .every((c) => c.search.includes("mode=simulation")),
  ).toBe(true);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出日报", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toContain("模拟数据");
  expect(fixture.unexpected).toEqual([]);
});

test("approved live delivery uses existing delivery API and never the simulation switch API", async ({
  page,
}) => {
  const fixture = await setup(page, { live: true, approved: true });
  const deliverySwitch = page.getByRole("switch", {
    name: "外卖配送",
    exact: true,
  });
  await expect(deliverySwitch).toBeEnabled();
  await deliverySwitch.click();
  await expect(deliverySwitch).toBeChecked();
  expect(
    fixture.calls
      .filter((c) => c.method === "PATCH")
      .map((c) => ({ path: c.path, body: c.body })),
  ).toEqual([
    { path: "/merchant/stalls/994/delivery", body: { enabled: true } },
  ]);
  expect(fixture.unexpected).toEqual([]);
});
