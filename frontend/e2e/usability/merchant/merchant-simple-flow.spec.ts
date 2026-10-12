import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../../helpers";

// Synthetic, fully intercepted data: no merchant account or working database is used.
async function fixture(page: Page, discoveryOnly = false) {
  const user = { id: 1971, username: "display_fixture", display_name: "合成商家", is_merchant: true, is_staff: false };
  const product = { id: 1972, name: "香菇鸡肉饭", image: "", price_cents: 1200, stock: 20, stock_version: 1, is_active: true, sale_paused: false, taste_options: [] };
  const stall = {
    id: 1971, name: "校园饭摊", products: [product], reviews: [], image: "",
    address: "南门橙色棚", area_name: "南门", latitude: 30, longitude: 120,
    status: "open", session_status: "open", accepting_orders: true, prep_minutes: 10,
    transaction_enabled: !discoveryOnly, is_visible: true, can_order: !discoveryOnly,
    activation: { has_location: true, has_sellable_products: true, steps: [] },
    services: { mode: "live" }, last_confirmed_at: new Date().toISOString(),
    closes_at: new Date(Date.now() + 7200000).toISOString(),
    stop_orders_at: new Date(Date.now() + 6600000).toISOString(),
  };
  const order = (id: string, status: string, extra: Record<string, unknown> = {}) => ({
    id, number: id.toUpperCase(), stall_id: stall.id, stall_name: stall.name, mode: "live", status,
    fulfillment_type: "pickup", payment_method: "offline", payment_status: "unpaid",
    payment: null, refund: null, review: null, cancel_requested: false, payment_review_required: false,
    financial_hold_reason: "", total_cents: 1200, items_total_cents: 1200,
    pickup_address: stall.address, note: "", contact_phone: "",
    created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 240000).toISOString(),
    items: [{ product_id: product.id, name: product.name, image: "", quantity: 1, unit_price_cents: 1200, portions: [{ options: { 辣度: "不辣" }, note: "" }] }],
    allowed_actions: status === "pending" ? ["accept", "reject"] : status === "preparing" ? ["ready", "update_prep"] : [],
    ...extra,
  });
  const orders = [
    order("new-order", "pending"),
    order("cook-pickup", "preparing"),
    order("cook-delivery", "preparing", { fulfillment_type: "delivery", estimated_ready_at: new Date(Date.now() - 60000).toISOString(), prep_delay_reason: "正在补做，请再等五分钟" }),
    order("ready-pickup", "ready", { allowed_actions: ["confirm_payment"] }),
    order("ready-delivery", "ready", { fulfillment_type: "delivery", payment_method: "wechat", payment_status: "paid", allowed_actions: ["dispatch"], delivery_point_name: "南门交接点", delivery_point_address: "橙色棚旁" }),
    order("finished-order", "completed"),
    order("cancelled-order", "cancelled"),
  ];
  const state = { orders, unexpected: [] as string[] };
  await page.route("https://**/*", route => route.abort());
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    const send = (json: unknown) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config") return send({ user, demo_mode: false, areas: [], amap_key: "", stale_minutes: 60 });
    if (path === "/auth/me") return send(user);
    if (path === "/events") return send({});
    if (path === "/merchant/stalls") return send([stall]);
    if (path === "/merchant/orders") {
      const filter = url.searchParams.get("filter");
      const active = orders.filter(row => !["completed", "cancelled"].includes(row.status));
      const rows = filter === "attention" ? active : filter === "all" ? orders : orders.filter(row => row.status === filter);
      return send({ results: rows, next: null, counts: { all: orders.length, active: active.length, attention: active.length, completed: 1, cancelled: 1 } });
    }
    const detail = path.match(/^\/merchant\/orders\/([^/]+)$/);
    if (detail) return send(orders.find(row => row.id === detail[1]));
    if (path === "/merchant/metrics") return send({ today: { revenue_cents: 0, orders_created: orders.length, orders_completed: 1 }, series: [], top_products: [], recent_payments: [] });
    state.unexpected.push(`${route.request().method()} ${path}`);
    return route.fulfill({ status: 500, json: { detail: "Unexpected fixture request" } });
  });
  return state;
}

function stage(page: Page, name: string) {
  return page.getByRole("group", { name: "订单阶段", exact: true }).getByRole("button", { name: new RegExp(name) });
}

for (const width of [360, 390]) {
  test(`ordinary queues avoid repeated headings and keep the cooking action visible at ${width}`, async ({ page }, info) => {
    const state = await fixture(page);
    // One ordinary cooking order keeps the first-screen fixture focused on its next action.
    state.orders.splice(2, 1);
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/merchant");
    await expect(stage(page, "新订单")).toContainText("1");
    await expect(page.locator(".m-simple-list-label")).not.toBeVisible();
    await expect(page.locator(".merchant-order-status")).toContainText("待接单");
    await stage(page, "制作中").click();
    await expect(stage(page, "制作中")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".m-simple-list-label")).not.toBeVisible();
    await expect(page.getByRole("heading", { name: "接单与出餐" })).toBeAttached();
    await expect(page.getByRole("region", { name: "制作中", exact: true })).toBeVisible();
    await expect(page.locator(".merchant-order-status")).toContainText("制作中");
    await expect(page.locator(".m-prep-status")).toHaveText("尚未设置出餐预估");
    await expect(page.getByText("接单后会显示商家预计出餐时间")).toHaveCount(0);
    const action = await page.getByRole("button", { name: "做好了", exact: true }).boundingBox();
    const navigation = await page.getByRole("navigation", { name: "商家底部导航" }).boundingBox();
    expect(action!.height).toBeGreaterThanOrEqual(48);
    expect(action!.y + action!.height).toBeLessThan(navigation!.y);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: info.outputPath(`merchant-preparing-${width}.png`), animations: "disabled" });
    expect(state.unexpected).toEqual([]);
  });
}

test("ready, delivery, search and historical result counts remain visible", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant");
  await stage(page, "待取餐").click();
  await expect(page.locator(".m-simple-list-label")).toContainText("2 单");
  await expect(page.locator(".m-simple-list-label")).toContainText("含配送交付");
  await expect(page.locator(".merchant-order")).toHaveCount(2);
  await expect(page.locator(".merchant-order").filter({ hasText: "READY-DELIVERY" })).toContainText("南门交接点");
  await stage(page, "制作中").click();
  await expect(page.locator(".m-prep-status.overdue")).toContainText("已超过预计时间");
  await expect(page.locator(".m-prep-status.overdue")).toContainText("正在补做，请再等五分钟");
  await page.getByRole("button", { name: "查找 / 历史", exact: true }).click();
  await page.getByRole("searchbox", { name: "搜索订单号" }).fill("PICKUP");
  await expect(page.locator(".m-simple-list-label")).toContainText("制作中1 单");
  await expect(page.locator(".merchant-order")).toHaveCount(1);
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  await page.getByRole("group", { name: "取餐方式筛选" }).getByRole("button", { name: "商家自配送", exact: true }).click();
  await expect(page.locator(".m-simple-list-label")).toContainText("制作中1 单");
  await expect(page.locator(".merchant-order")).toHaveCount(1);
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  const history = page.getByRole("group", { name: "其他订单记录" });
  for (const [label, count] of [["全部记录", 7], ["已完成", 1], ["已取消", 1]] as const) {
    await history.getByRole("button", { name: new RegExp(label) }).click();
    await expect(page.locator(".m-simple-list-label")).toContainText(`${label}${count} 单`);
    await expect(page.locator(".merchant-order")).toHaveCount(count);
  }
  expect(state.unexpected).toEqual([]);
});

test("reminders precede the final logout and retain their mounted state across navigation", async ({ page }, info) => {
  const state = await fixture(page);
  await page.addInitScript(() => {
    class FixtureAudio {
      state = "running"; currentTime = 0; destination = {};
      async resume() {} async close() {}
      createOscillator() { return { frequency: { value: 0 }, connect() {}, disconnect() {}, start() {}, stop() {}, type: "sine", onended: null }; }
      createGain() { return { connect() {}, disconnect() {}, gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} } }; }
    }
    Object.defineProperty(window, "AudioContext", { configurable: true, value: FixtureAudio });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/more");
  const sound = page.getByRole("button", { name: "开启声音提醒", exact: true });
  await sound.click();
  await expect(page.getByRole("button", { name: "关闭声音提醒", exact: true })).toHaveAttribute("aria-pressed", "true");
  const notificationNodes = await page.locator("#notifications").evaluateHandle(element => [...element.querySelectorAll(".m-attention, .session-notifications")]);
  const logout = page.locator(".m-content").getByRole("button", { name: "退出登录", exact: true });
  await expect(logout).toHaveCount(1);
  expect(await logout.evaluate(element => !!(document.querySelector("#notifications")!.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    const bottom = await logout.boundingBox();
    const reminders = await page.locator("#notifications").boundingBox();
    expect(bottom!.y).toBeGreaterThanOrEqual(reminders!.y + reminders!.height);
    expect(bottom!.height).toBeGreaterThanOrEqual(44);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("navigation", { name: "商家底部导航" }).getByRole("link", { name: "接单" }).click();
  await expect(page.locator(".m-orders")).toBeVisible();
  await expect(logout).toHaveCount(0);
  await page.getByRole("navigation", { name: "商家底部导航" }).getByRole("link", { name: "更多", exact: true }).click();
  await expect(page.getByRole("button", { name: "关闭声音提醒", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(await page.evaluate(nodes => nodes.length === 2 && nodes.every(node => node.isConnected && document.querySelector("#notifications")!.contains(node)), notificationNodes)).toBe(true);
  await page.screenshot({ path: info.outputPath("merchant-more-390.png"), fullPage: true, animations: "disabled" });
  await notificationNodes.dispose();
  expect(state.unexpected).toEqual([]);
});

test("discovery merchants retain old-order reminders before logout", async ({ page }) => {
  const state = await fixture(page, true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/more");
  await expect(page.getByRole("heading", { name: "已有订单提醒", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "开启声音提醒", exact: true })).toBeVisible();
  const logout = page.locator(".m-content").getByRole("button", { name: "退出登录", exact: true });
  expect(await logout.evaluate(element => !!(document.querySelector("#notifications")!.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  expect(state.unexpected).toEqual([]);
});
