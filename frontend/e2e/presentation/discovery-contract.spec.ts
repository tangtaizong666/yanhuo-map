import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../helpers";

async function fixture(page: Page) {
  const user = { id: 9901, username: "presentation-fixture", display_name: "同学", is_merchant: false, is_staff: false };
  const detail = {
    id: 1, name: "南门煎饼摊", description: "现摊鸡蛋饼，椒盐调味。", category: "小吃", area_id: 1, area_name: "测试校园",
    image: "/images/food-jianbing.jpg", address: "南门便利店旁", latitude: 30, longitude: 120,
    status: "open", can_order: true, transaction_enabled: true, accepting_orders: true, order_unavailable_reason: "",
    last_confirmed_at: new Date().toISOString(), closes_at: "2026-10-08T12:54:00+08:00", usual_hours: "11:00—14:00",
    prep_minutes: 8, rating: null, review_count: 0, reviews: [], is_followed: false, contact_phone: "",
    arrival_note: "绿色棚顶，靠近南门便利店", arrival_image: "", merchant_name: "隔离测试摊主", qualification_note: "已核验",
    receiving_status: "recent", receiving_valid_for_seconds: 30,
    products: [{ id: 11, name: "椒盐鸡蛋煎饼", description: "现摊鸡蛋饼，椒盐调味。", image: "/images/food-jianbing.jpg",
      price_cents: 800, availability: "available", max_order_quantity: 10, is_active: true }],
    wechat_payment: { available: false, mode: "simulation", channels: [], reason: "当前模拟支付暂停，请到摊付款。" },
    delivery: { available: false, enabled: false, mode: "live", points: [] },
  };
  const state = { detail, unexpected: [] as string[] };
  await page.route("https://**/*", route => route.abort());
  // Every API request is intercepted; this fixture never writes to the development database.
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    const send = (json: unknown) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config") return send({ user, demo_mode: false, amap_key: "", areas: [{ id: 1, name: "测试校园" }] });
    if (path === "/auth/me") return send(user);
    if (path === "/orders/active-summary") return send({ user_id: user.id, counts: { total: 0 }, order: null });
    if (path === "/orders/recent-completed") return send([]);
    if (path === "/events") return send({});
    if (path === "/stalls/1") return send(detail);
    state.unexpected.push(`${route.request().method()} ${path}`);
    return route.fulfill({ status: 500, json: { detail: "Unexpected isolated request" } });
  });
  return state;
}

for (const [status, reason, warning] of [
  ["closed", "商家已收摊，暂时无法点单。", "商家已收摊"],
  ["stale", "位置已过期，暂时无法点单。", "位置已过期"],
]) {
  test(`${status} stall keeps the safety warning without duplicate banners or a current closing promise`, async ({ page }, info) => {
    const state = await fixture(page);
    Object.assign(state.detail, { status, can_order: false, accepting_orders: false, order_unavailable_reason: reason });
    // Capture identical state and widths before assertions, so a red run is also a comparison baseline.
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/stalls/1");
      await expect(page.locator(".stall-overview")).toBeVisible();
      await page.screenshot({ path: info.outputPath(`${status}-${width}.png`), fullPage: true, animations: "disabled" });
      await assertNoHorizontalOverflow(page);
    }
    await expect(page.locator(".overview-facts")).toContainText(reason!);
    await expect(page.locator(".availability-note")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "到摊指引" }).locator(".visit-warning")).toContainText(warning!);
    await expect(page.locator(".menu-intro")).not.toContainText("现做现吃");
    await expect(page.getByRole("button", { name: "添加椒盐鸡蛋煎饼", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "查看椒盐鸡蛋煎饼详情", exact: true })).toHaveAttribute("href", "/stalls/1/products/11");
    const guide = page.getByRole("region", { name: "到摊指引" });
    await expect(guide.locator(".visit-facts")).toContainText("上次预计");
    await expect(guide.locator(".visit-facts")).toContainText(status === "closed" ? "已收摊" : "状态待确认");
    await page.setViewportSize({ width: 360, height: 844 });
    await expect(guide.locator(".visit-facts")).not.toContainText("收摊");
    await expect(guide.getByText(/上次预计/)).toBeHidden();
    await guide.getByRole("button", { name: "联系与更多信息" }).click();
    await expect(guide.getByText(/上次预计/)).toBeVisible();
    await expect(guide.getByText(state.detail.arrival_note, { exact: true })).toBeVisible();
    expect(state.unexpected).toEqual([]);
  });
}

test("an open stall labels its closing estimate as a merchant plan and keeps ordering available", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/stalls/1");
  const facts = page.getByRole("region", { name: "到摊指引" }).locator(".visit-facts");
  await expect(facts).toContainText("商家计划");
  await expect(facts).not.toContainText("上次");
  await expect(page.getByRole("button", { name: "添加椒盐鸡蛋煎饼", exact: true })).toBeEnabled();
  expect(state.unexpected).toEqual([]);
});

for (const mode of ["simulation", "live"]) {
  test(`${mode} unavailable payment uses a neutral status and preserves its actual reason`, async ({ page }, info) => {
    const state = await fixture(page);
    state.detail.wechat_payment.mode = mode;
    state.detail.wechat_payment.reason = mode === "simulation" ? "当前模拟支付暂停，请到摊付款。" : "线上收款暂时不可用，请到摊付款。";
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/stalls/1");
    await expect(page.locator(".stall-payment-status")).toBeVisible();
    await page.screenshot({ path: info.outputPath(`${mode}-payment-1440.png`), fullPage: true, animations: "disabled" });
    await expect(page.locator(".stall-payment-status")).toContainText("当前不可用");
    await expect(page.locator(".stall-payment-status")).toContainText("到摊付款");
    await expect(page.locator(".stall-payment-status")).not.toContainText("尚未开通");
    await expect(page.locator(".stall-payment-status")).not.toContainText("扫码");
    if (mode === "simulation") await expect(page.locator(".stall-payment-status")).toContainText("模拟微信支付");
    await page.getByRole("button", { name: "摊位信息", exact: true }).click();
    await expect(page.locator(".info-panel")).toContainText(state.detail.wechat_payment.reason);
    await expect(page.locator(".info-panel")).not.toContainText("扫码");
    if (mode === "simulation") await expect(page.locator(".info-panel")).toContainText("不会真实扣款");
    expect(state.unexpected).toEqual([]);
  });
}

test("available simulated payment remains explicitly simulated and does not change discovery-only eligibility", async ({ page }) => {
  const state = await fixture(page);
  state.detail.wechat_payment.available = true;
  state.detail.wechat_payment.reason = "";
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/stalls/1");
  await expect(page.locator(".stall-payment-status")).toContainText("模拟微信支付");
  await expect(page.locator(".stall-payment-status")).toContainText("出餐后可线上付款");
  Object.assign(state.detail, { transaction_enabled: false, can_order: false, order_unavailable_reason: "仅提供找摊信息服务" });
  await page.reload();
  await expect(page.locator(".overview-facts")).toContainText("线下到访 · 暂未开通在线点单");
  await expect(page.locator(".stall-payment-status")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "添加椒盐鸡蛋煎饼", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "查看椒盐鸡蛋煎饼详情", exact: true })).toBeVisible();
  expect(state.unexpected).toEqual([]);
});
