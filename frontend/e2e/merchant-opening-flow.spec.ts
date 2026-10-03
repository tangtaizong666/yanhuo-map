import { expect, test, type Locator, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Every API is intercepted. Opening and relocation never touch a business DB.
async function fixture(page: Page) {
  const user = {
    id: 978,
    username: "opening_fixture",
    display_name: "南门早餐",
    is_merchant: true,
    is_staff: false,
  };
  const delivery = {
    mode: "live",
    enabled: false,
    available: false,
    approved: false,
    reason: "尚未开通配送",
    fee_cents: 200,
    point_ids: [],
    available_points: [],
    points: [],
    min_order_cents: 0,
    capacity: 5,
    starts_at: "00:00",
    ends_at: "23:59",
    eta_min_minutes: 20,
    eta_max_minutes: 40,
  };
  const services = {
    mode: "live",
    online_payment_enabled: false,
    delivery_enabled: false,
    wechat_payment: { mode: "live", available: false },
    delivery,
  };
  const stall: any = {
    id: 978,
    name: "南门早餐摊",
    description: "",
    category: "小吃",
    image: "",
    area_id: 1,
    area_name: "校园南门",
    address: "南门入口左边第二个橙色棚",
    latitude: 30,
    longitude: 120,
    status: "open",
    session_status: "open",
    last_confirmed_at: new Date(Date.now() - 120000).toISOString(),
    closes_at: new Date(Date.now() + 7200000).toISOString(),
    business_session_id: 8,
    accepting_orders: true,
    transaction_enabled: true,
    is_visible: true,
    can_order: true,
    order_unavailable_reason: "",
    prep_minutes: 10,
    prep_capacity: null,
    prep_active_orders: 1,
    stop_orders_at: null,
    contact_phone: "",
    public_phone_enabled: false,
    merchant_name: "南门早餐店",
    activation: { has_location: true },
    services,
    delivery,
    location_draft_address: "",
    arrival_note: "",
    arrival_image: "",
    usual_hours: "",
    products: [
      {
        id: 978,
        name: "杂粮煎饼",
        description: "",
        category: "小吃",
        image: "",
        price_cents: 800,
        stock: 20,
        stock_version: 1,
        is_active: true,
        sale_paused: false,
        taste_options: [],
      },
    ],
    reviews: [],
  };
  const orders = [
    {
      id: "opening-order",
      number: "OPENING001",
      stall_id: 978,
      stall_name: stall.name,
      status: "preparing",
      mode: "live",
      fulfillment_type: "pickup",
      payment_method: "offline",
      payment_status: "unpaid",
      total_cents: 800,
      items_total_cents: 800,
      pickup_address: stall.address,
      created_at: new Date().toISOString(),
      estimated_ready_at: new Date(Date.now() + 600000).toISOString(),
      allowed_actions: ["ready", "update_prep"],
      cancel_requested: false,
      items: [
        {
          product_id: 978,
          name: "杂粮煎饼",
          quantity: 1,
          unit_price_cents: 800,
          image: "",
          portions: [],
        },
      ],
    },
  ];
  const state = {
    stall,
    services,
    orders,
    writes: [] as { path: string; body: any }[],
    reads: [] as string[],
    unexpected: [] as string[],
  };
  await page
    .context()
    .addCookies([
      {
        name: "csrftoken",
        value: "fixture-csrf-token",
        domain: new URL(process.env.E2E_BASE_URL || "http://127.0.0.1:5183")
          .hostname,
        path: "/",
        sameSite: "Lax",
      },
    ]);
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (request.method() === "GET") state.reads.push(path);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config")
      return send({
        user,
        brand: "烟火地图",
        demo_mode: false,
        stale_minutes: 60,
        amap_key: "",
        areas: [{ id: 1, name: "校园南门", latitude: 30, longitude: 120 }],
      });
    if (path === "/auth/me") return send(user);
    if (path === "/events") return send({});
    if (path === "/merchant/stalls") return send([stall]);
    if (path === "/merchant/orders")
      return send({
        results: orders,
        next: null,
        counts: {
          all: 1,
          active: 1,
          attention: 1,
          pending: 0,
          pending_payment: 0,
          preparing: 1,
          ready: 0,
          followup: 0,
          completed: 0,
          cancelled: 0,
        },
      });
    if (path === "/merchant/stalls/978/services") return send(services);
    if (path === "/merchant/stalls/978/delivery") return send(delivery);
    if (path === "/merchant/stalls/978/location-reports")
      return send({ unresolved_count: 0, reports: [] });
    if (path === "/merchant/stalls/978/profile") {
      const body = request.postDataJSON();
      state.writes.push({ path, body });
      Object.assign(stall, body);
      return send(stall);
    }
    if (path === "/merchant/stalls/978/status") {
      const body = request.postDataJSON();
      state.writes.push({ path, body });
      const moved = body.address != null && body.address !== stall.address;
      Object.assign(stall, body);
      if (body.status) stall.session_status = body.status;
      if (body.confirm_location) {
        stall.status = stall.session_status;
        stall.last_confirmed_at = new Date().toISOString();
      }
      if (moved) {
        stall.transaction_enabled = false;
        stall.order_unavailable_reason = "取餐位置已变更，等待运营重新核验";
      }
      return send(stall);
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  return state;
}

async function assertReachable(locator: Locator, page: Page) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  const navigation = page.getByRole("navigation", {
    name: "商家底部导航",
    exact: true,
  });
  const nav = (await navigation.isVisible())
    ? await navigation.boundingBox()
    : null;
  expect(box!.y + box!.height).toBeLessThanOrEqual(
    nav?.y ?? page.viewportSize()!.height,
  );
  expect(
    await locator.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return element.contains(
        document.elementFromPoint(
          rect.left + rect.width / 2,
          rect.top + rect.height / 2,
        ),
      );
    }),
  ).toBe(true);
}

test("compact opening and expired-location confirmation show the actual address before any write", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 360, height: 844 });
  const originalOrders = JSON.stringify(state.orders);
  for (const status of ["closed", "stale"]) {
    state.stall.status = status;
    state.stall.session_status = status === "closed" ? "closed" : "open";
    await page.goto("/merchant");
    const trigger = page.getByRole("button", {
      name: status === "closed" ? "开始营业" : "确认仍在这里",
      exact: true,
    });
    await expect(trigger).toBeVisible();
    const business = page.getByRole("region", {
      name: "营业与接单",
      exact: true,
    });
    await expect(business).toContainText(state.stall.address);
    await expect(
      business.getByRole("link", { name: /更换位置/ }),
    ).toBeVisible();
    expect(state.writes).toHaveLength(status === "closed" ? 0 : 1);
    await assertReachable(trigger, page);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`opening-confirm-${status}-360.png`),
      animations: "disabled",
    });
    await trigger.click();
    await expect
      .poll(() => state.writes.length)
      .toBe(status === "closed" ? 1 : 2);
    expect(state.writes.at(-1)!.body).toEqual({
      status: "open",
      confirm_location: true,
    });
  }
  expect(JSON.stringify(state.orders)).toBe(originalOrders);
  expect(state.unexpected).toEqual([]);
});

test("missing and placeholder locations lead to the location editor without an opening request", async ({
  page,
}) => {
  const state = await fixture(page);
  for (const status of ["closed", "stale"]) {
    state.stall.status = status;
    state.stall.session_status = status === "closed" ? "closed" : "open";
    state.stall.activation.has_location = false;
    state.stall.address = status === "closed" ? "" : "位置尚未确认";
    state.stall.latitude = null;
    state.stall.longitude = null;
    await page.goto("/merchant");
    await page.getByRole("button", { name: "设置位置", exact: true }).click();
    await expect(page).toHaveURL(/\/merchant\/store#location$/);
    await expect(page.locator("#location")).toHaveAttribute("open", "");
    await expect(page.getByLabel("详细取餐地址")).toHaveValue("");
    expect(state.writes).toEqual([]);
  }
  expect(state.unexpected).toEqual([]);
});

test("daily store controls stay prominent while mounted settings are collapsed at four widths", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto("/merchant/store");
  await expect(page.locator("#location")).not.toHaveAttribute("open", "");
  await expect(page.locator(".queue-settings")).toHaveCount(1);
  await expect(page.locator(".merchant-services")).toHaveCount(1);
  await expect(page.locator(".queue-settings .queue-counts")).toBeHidden();
  await expect(page.locator(".queue-settings summary")).toBeVisible();
  await expect(page.locator(".merchant-services")).toBeHidden();
  await expect
    .poll(() => state.reads.includes("/merchant/stalls/978/services"))
    .toBe(true);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 1000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await assertNoHorizontalOverflow(page);
    await assertReachable(
      page.getByRole("button", { name: /暂停接单/, exact: false }).first(),
      page,
    );
    await page.screenshot({
      path: info.outputPath(`store-daily-${width}.png`),
      animations: "disabled",
      fullPage: true,
    });
  }
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("direct location link opens and reaches the editor; relocation keeps existing order snapshots", async ({
  page,
}, info) => {
  const state = await fixture(page);
  const oldAddress = state.orders[0]!.pickup_address;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/store#location");
  await expect(page.locator("#location")).toHaveAttribute("open", "");
  await expect
    .poll(async () => (await page.locator("#location").boundingBox())!.y)
    .toBeLessThan(300);
  await expect(page.getByLabel("详细取餐地址")).toHaveValue(
    state.stall.address,
  );
  await page.getByLabel("详细取餐地址").fill("南门入口右边第四个蓝色棚");
  expect(state.writes).toEqual([]);
  await page
    .getByRole("button", { name: "确认并保存位置与时间", exact: true })
    .click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]!.body).toEqual({
    status: "open",
    confirm_location: true,
    address: "南门入口右边第四个蓝色棚",
  });
  await expect(
    page.getByText("取餐位置已变更，等待运营重新核验", { exact: true }),
  ).toBeVisible();
  expect(state.stall.transaction_enabled).toBe(false);
  expect(state.orders[0]!.pickup_address).toBe(oldAddress);
  expect(state.orders[0]!.status).toBe("preparing");
  await page.screenshot({
    path: info.outputPath("store-relocation-390.png"),
    animations: "disabled",
    fullPage: true,
  });
  expect(state.unexpected).toEqual([]);
});

test("resume and pause preserve position time and unfinished orders; explicit stale confirmation updates it", async ({
  page,
}) => {
  const state = await fixture(page);
  const confirmed = state.stall.last_confirmed_at;
  const originalOrders = JSON.stringify(state.orders);
  state.stall.status = state.stall.session_status = "paused";
  await page.goto("/merchant");
  await page.getByRole("button", { name: "恢复营业", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]!.body).toEqual({
    status: "open",
    confirm_location: false,
  });
  expect(state.stall.last_confirmed_at).toBe(confirmed);
  await page.getByRole("button", { name: "暂停接单", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2);
  expect(state.writes[1]!.body).toEqual({ accepting_orders: false });
  expect(state.stall.status).toBe("open");
  expect(state.stall.last_confirmed_at).toBe(confirmed);
  state.stall.status = "stale";
  await page.reload();
  await page.getByRole("button", { name: "确认仍在这里", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(3);
  expect(state.stall.last_confirmed_at).not.toBe(confirmed);
  expect(state.stall.accepting_orders).toBe(false);
  expect(JSON.stringify(state.orders)).toBe(originalOrders);
  expect(state.unexpected).toEqual([]);
});

test("collapsed settings retain unsaved capacity and profile drafts across a background refresh", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant/store");
  const queue = page.locator(".queue-settings");
  const inner = queue.locator("details");
  await inner.locator(":scope > summary").click();
  await page.getByLabel("限制同时备餐订单", { exact: true }).check();
  await page.getByLabel("最多占位（单）", { exact: true }).fill("7");
  await inner.locator(":scope > summary").click();
  const profile = page
    .locator("details")
    .filter({ has: page.getByLabel("店铺简介", { exact: true }) });
  await profile.locator(":scope > summary").click();
  await page.getByLabel("店铺简介", { exact: true }).fill("手作煎饼，现摊现做");
  await profile.locator(":scope > summary").click();
  state.stall.prep_capacity = 9;
  state.stall.description = "另一台设备的新简介";
  await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
  await inner.locator(":scope > summary").click();
  await expect(page.getByLabel("最多占位（单）", { exact: true })).toHaveValue(
    "7",
  );
  await page.getByRole("button", { name: "保存备餐上限", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]!.body).toEqual({ prep_capacity: 7 });
  await profile.locator(":scope > summary").click();
  await expect(page.getByLabel("店铺简介", { exact: true })).toHaveValue(
    "手作煎饼，现摊现做",
  );
  await page.getByRole("button", { name: "保存店铺信息", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2);
  expect(state.writes[1]!.body).toEqual({ description: "手作煎饼，现摊现做" });
  expect(state.unexpected).toEqual([]);
});
