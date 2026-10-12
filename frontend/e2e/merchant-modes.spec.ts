import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// All APIs are intercepted. These cases do not change merchant or order data.
async function fixture(page: Page, eligible = false) {
  const user = { id: 1051, username: "merchant_modes", display_name: "出摊商家", is_merchant: true, is_staff: false };
  const products = [{ id: 1052, name: "香菇鸡肉饭", description: "商家填写的招牌餐点", category: "主食", image: "", price_cents: 1200, stock: 0, stock_version: 5, is_active: true, sale_paused: false, display_availability: "available", taste_options: [] }];
  const stall: any = {
    id: 1051, name: "南门饭摊", description: "", image: "", area_name: "校园南门", area_id: 1,
    address: "南门路口左侧橙色棚", latitude: 30, longitude: 120,
    status: "closed", session_status: "closed", prep_minutes: 10, products, reviews: [],
    last_confirmed_at: "2026-10-05T09:00:00+08:00", closes_at: "", contact_phone: "",
    is_visible: true, accepting_orders: true,
    // Deliberately conflicting legacy flag verifies authoritative capability precedence.
    transaction_enabled: true, can_order: false,
    capabilities: { mode: "live", public_listing: { available: true, reason: "" }, pickup_orders: { eligible, available: false, reason: eligible ? "当前已收摊" : "仅提供找摊信息服务" } },
    services: { mode: "live", wechat_payment: { available: false }, delivery: { available: false } },
    activation: { has_location: true, steps: [
      { key: "visibility", label: "公开摊位", status: "done", owner: "operator", reason: "" },
      { key: "location", label: "确认实际取餐位置", status: "done", owner: "merchant", reason: "" },
      { key: "transaction", label: "线上接单资格", status: "pending", owner: "operator", reason: "尚未开放线上交易" },
    ] },
  };
  const state = { stall, products, orders: [] as any[], writes: [] as { path: string; body: any }[], unexpected: [] as string[] };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config") return send({ user, areas: [], demo_mode: false, stale_minutes: 60, amap_key: "", public_base_url: "" });
    if (path === "/auth/me") return send(user);
    if (path === "/events") return send({});
    if (path === "/merchant/stalls") return send([stall]);
    if (path === "/merchant/orders") return send(state.orders);
    if (path === "/merchant/stalls/1051/status") {
      const body = request.postDataJSON();
      state.writes.push({ path, body });
      stall.status = body.status; stall.session_status = body.status;
      if (body.confirm_location) stall.last_confirmed_at = new Date().toISOString();
      return send(stall);
    }
    if (path === "/merchant/stalls/1051/location-reports") return send({ unresolved_count: 0, reports: [] });
    if (path === "/merchant/stalls/1051/products") {
      const body = request.postDataJSON(); state.writes.push({ path, body });
      const row = { ...products[0]!, ...body, id: 1053, stock: 0 }; products.push(row);
      return send(row);
    }
    if (path === "/merchant/products/1052" && request.method() === "PATCH") {
      const body = request.postDataJSON(); state.writes.push({ path, body }); Object.assign(products[0]!, body);
      return send(products[0]);
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 500, json: { detail: "Unexpected isolated request" } });
  });
  return state;
}

function oldOrder(id: string, changes: Record<string, any>) {
  return { id, number: `MODE-${id}`, stall_id: 1051, stall_name: "南门饭摊", mode: "live", fulfillment_type: "pickup", status: "preparing", payment_method: "offline", payment_status: "unpaid", payment_review_required: false, cancel_requested: false, total_cents: 1200, items_total_cents: 1200, created_at: new Date().toISOString(), items: [{ product_id: 1052, name: "香菇鸡肉饭", quantity: 1, unit_price_cents: 1200, image: "" }], ...changes };
}

test("找摊首页复用真实出摊操作，暂歇和收摊不续位置时间", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant");
  const home = page.getByRole("region", { name: "今天出摊", exact: true });
  await expect(home).toBeVisible();
  await expect(home).toContainText("南门路口左侧橙色棚");
  await expect(home).toContainText("上次确认");
  await expect(page.getByText("完成开摊准备后，才能接到新订单。")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "暂停接单", exact: true })).toHaveCount(0);
  await expect(home.getByRole("link", { name: "预览学生端" })).toHaveAttribute("href", "/stalls/1051");
  await home.getByRole("button", { name: "在老地方开摊" }).click();
  await expect(home.getByRole("button", { name: "我还在这里，确认位置" })).toBeVisible();
  expect(state.writes.at(-1)?.body).toEqual({ status: "open", confirm_location: true });
  const confirmed = state.stall.last_confirmed_at;
  await home.getByRole("button", { name: "暂时离开摊位" }).click();
  await expect(home.getByRole("button", { name: "回到摊位，恢复出摊" })).toBeVisible();
  expect(state.stall.last_confirmed_at).toBe(confirmed);
  await home.getByRole("button", { name: "回到摊位，恢复出摊" }).click();
  await expect(home.getByRole("button", { name: "我还在这里，确认位置" })).toBeVisible();
  expect(state.writes.at(-1)?.body).toEqual({ status: "open", confirm_location: false });
  expect(state.stall.last_confirmed_at).toBe(confirmed);
  await home.getByRole("button", { name: "今日收摊" }).click();
  await expect(home.getByRole("button", { name: "在老地方开摊" })).toBeVisible();
  expect(state.stall.last_confirmed_at).toBe(confirmed);
  await page.reload();
  await expect(page.getByRole("region", { name: "今天出摊", exact: true })).toBeVisible();
  expect(state.unexpected).toEqual([]);
});

test("找摊商家保留已有订单、取消和资金异常处理入口", async ({ page }) => {
  const state = await fixture(page);
  state.orders.push(oldOrder("cancel", { cancel_requested: true }), oldOrder("refund", { status: "cancelled", payment_status: "refunding", refund: { id: "refund-1", status: "processing", amount_cents: 1200 } }));
  await page.goto("/merchant");
  await expect(page.getByRole("link", { name: /处理已有订单/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /处理取消申请/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /跟进售后/ })).toBeVisible();
  await page.getByRole("link", { name: /处理已有订单/ }).click();
  await expect(page).toHaveURL(/\/merchant\/orders\?filter=active/);
  await expect(page.locator(".m-orders")).toBeVisible();
  await page.goto("/merchant/more");
  await expect(page.getByRole("link", { name: /已有订单.*处理原有订单/ })).toBeVisible();
  await page.locator("#activation > summary").click();
  await expect(page.locator("#activation")).toContainText("当前提供找摊信息服务");
  await expect(page.locator("#activation")).not.toContainText("线上接单资格");
  expect(state.unexpected).toEqual([]);
});

test("交易资格独立于营业、接单开关和售罄，默认仍进接单台", async ({ page }) => {
  const state = await fixture(page, true);
  state.stall.accepting_orders = false;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant");
  await expect(page.locator(".m-mobile-brand")).toContainText("接单台");
  await expect(page.locator(".m-orders")).toBeVisible();
  await expect(page.getByRole("region", { name: "今天出摊", exact: true })).toHaveCount(0);
  await page.goto("/merchant/products");
  await expect(page.getByText("线上可卖", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("group", { name: "香菇鸡肉饭今日供应" })).toHaveCount(0);
  expect(state.products[0]?.stock).toBe(0);
  expect(state.unexpected).toEqual([]);
});

test("找摊菜品三态不写库存，新增菜品不要求精确份数", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/merchant/products");
  const supply = page.getByRole("group", { name: "香菇鸡肉饭今日供应" });
  await expect(supply.getByRole("button", { name: "今天有", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".stock-readout")).toHaveCount(0);
  await expect(page.getByText("今天补货", { exact: true })).toHaveCount(0);
  for (const [label, value] of [["卖完了", "sold_out"], ["暂时不卖", "paused"], ["今天有", "available"]]) {
    await supply.getByRole("button", { name: label!, exact: true }).click();
    await expect(supply.getByRole("button", { name: label!, exact: true })).toHaveAttribute("aria-pressed", "true");
    expect(state.writes.at(-1)?.body).toEqual({ display_availability: value });
    expect(state.products[0]?.stock).toBe(0);
    expect(state.products[0]?.stock_version).toBe(5);
  }
  await page.getByRole("button", { name: "添加商品", exact: true }).click();
  const editor = page.getByRole("dialog");
  await expect(editor.getByRole("spinbutton", { name: "线上剩余可卖份数", exact: true })).toHaveCount(0);
  await editor.getByRole("textbox", { name: "商品名称" }).fill("红豆汤");
  await editor.getByRole("spinbutton", { name: /单价/ }).fill("6");
  await editor.getByRole("combobox", { name: "今日供应", exact: true }).selectOption("available");
  await editor.getByRole("button", { name: "添加商品", exact: true }).click();
  await expect(editor).toHaveCount(0);
  expect(state.writes.at(-1)?.body.display_availability).toBe("available");
  expect(state.writes.at(-1)?.body).not.toHaveProperty("stock");
  expect(state.unexpected).toEqual([]);
});

test("位置待确认和缺少位置均保留真实状态，换位置展开现有编辑区", async ({ page }) => {
  const state = await fixture(page);
  state.stall.status = "stale"; state.stall.session_status = "paused";
  await page.goto("/merchant");
  const home = page.getByRole("region", { name: "今天出摊", exact: true });
  await expect(home).toContainText("位置待确认");
  await home.getByRole("button", { name: "核对过了，我仍在这里" }).click();
  await expect(home.getByRole("button", { name: "回到摊位，恢复出摊" })).toBeVisible();
  expect(state.writes.at(-1)?.body).toEqual({ status: "paused", confirm_location: true });
  await home.getByRole("button", { name: "换个位置" }).click();
  await expect(page).toHaveURL(/\/merchant\/store#location/);
  await expect(page.locator("#location")).toHaveAttribute("open", "");
  await expect(page.getByText("支付与配送", { exact: true })).toHaveCount(0);
  state.stall.activation.has_location = false; state.stall.latitude = null; state.stall.longitude = null;
  state.stall.status = "closed"; state.stall.session_status = "closed";
  await page.goto("/merchant");
  await page.getByRole("button", { name: "先设置出摊位置" }).click();
  await expect(page).toHaveURL(/\/merchant\/store#location/);
  expect(state.writes).toHaveLength(1);
  expect(state.unexpected).toEqual([]);
});

for (const width of [360, 390, 768, 1440]) {
  test(`找摊首页和三态菜单 ${width}px 可读可操作`, async ({ page }, testInfo) => {
    await fixture(page);
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/merchant");
    const action = page.getByRole("button", { name: "在老地方开摊" });
    await expect(action).toBeVisible();
    const box = await action.boundingBox();
    expect(box?.height).toBeGreaterThanOrEqual(44);
    expect(box!.y + box!.height).toBeLessThan(760);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`merchant-discovery-home-${width}.png`), fullPage: true });
    await page.goto("/merchant/products");
    const group = page.getByRole("group", { name: "香菇鸡肉饭今日供应" });
    await expect(group).toBeVisible();
    for (const button of await group.getByRole("button").all()) {
      const control = await button.boundingBox();
      expect(control?.height).toBeGreaterThanOrEqual(44);
      expect(control?.width).toBeGreaterThanOrEqual(44);
    }
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`merchant-discovery-menu-${width}.png`), fullPage: true });
  });
}
