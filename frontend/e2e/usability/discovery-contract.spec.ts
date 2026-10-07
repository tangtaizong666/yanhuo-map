import { expect, test, type Locator, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../helpers";
import type { User } from "../../src/lib/types";

// All API traffic is isolated, and no fixture grants online ordering eligibility.
async function fixture(page: Page) {
  const rows = ["paused", "stale"].map((status, index) => ({
    id: 1081 + index, name: index ? "东门饭摊" : "南门饼摊", status,
    session_status: status === "stale" ? "open" : status,
    description: "商家提供的摊位资料", category: "小吃", image: "/images/food-jianbing.jpg",
    address: index ? "东门入口蓝色棚" : "南门路口橙色棚", latitude: 30 + index / 10, longitude: 120 + index / 10,
    area_id: 1, area_name: "校园周边", prep_minutes: 10,
    last_confirmed_at: new Date(Date.now() - (index ? 7200000 : 120000)).toISOString(),
    usual_hours: index ? "17:00—21:00" : "", closes_at: new Date(Date.now() + 7200000).toISOString(),
    transaction_enabled: false, can_order: false, accepting_orders: false,
    order_unavailable_reason: "仅提供找摊信息服务", is_followed: false,
    products: [], reviews: [], rating: null, review_count: 0, distance_m: null,
    merchant_name: "隔离找摊商户", qualification_note: "", order_count: 0,
    arrival_note: index ? "蓝色棚旁有一棵银杏树" : "橙色棚旁是校园公告栏",
    arrival_image: "/images/night-market.jpg", contact_phone: "13900001111",
    wechat_payment: { available: false, supported: false, mode: "live", channels: [] },
    delivery: { available: false, enabled: false, points: [] },
  }));
  const state = { rows, user: null as User | null, detailReads: 0, mapReads: 0, detailStatus: 200, beforeDetail: null as null | (() => Promise<void>), unexpected: [] as string[], writes: [] as string[] };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config") return send({ user: state.user, brand: "烟火地图", demo_mode: false, stale_minutes: 60, amap_key: "", areas: [{ id: 1, name: "校园周边", latitude: 30, longitude: 120 }] });
    if (path === "/auth/me") return send(state.user);
    if (path === "/orders/active-summary") return send({ user_id: state.user?.id ?? null, orders: [], count: 0, status_counts: {}, synced_at: new Date().toISOString() });
    if (path === "/events") return send({ id: 1 });
    if (request.method() !== "GET") state.writes.push(`${request.method()} ${path}`);
    if (path === "/products") return send({ results: [], next: null });
    if (path === "/stalls" || path === "/stalls/map") {
      const status = url.searchParams.get("status"), term = url.searchParams.get("q") || "";
      const matches = rows.filter((row) => (!status || status === "all" || row.status === status) && row.name.includes(term));
      if (path === "/stalls") return send({ results: matches, next: null });
      state.mapReads++;
      const fields = ["id", "name", "category", "image", "address", "latitude", "longitude", "area_id", "area_name", "status", "last_confirmed_at", "prep_minutes", "transaction_enabled", "can_order", "is_followed", "accepting_orders", "order_unavailable_reason"];
      return send({ results: matches.map((row) => Object.fromEntries(fields.map((key) => [key, row[key as keyof typeof row]]))), truncated: false, limit: 200 });
    }
    const detail = path.match(/^\/stalls\/(\d+)$/);
    if (detail) {
      state.detailReads++;
      const snapshot = structuredClone(rows.find((row) => row.id === Number(detail[1])));
      const status = state.detailStatus;
      await state.beforeDetail?.();
      return route.fulfill({ status, json: status === 200 ? snapshot : { detail: "暂时无法核对认摊信息" } });
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 500, json: { detail: "Unexpected isolated request" } });
  });
  return state;
}

async function targetSize(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(box!.width).toBeGreaterThanOrEqual(44);
}

async function openMap(page: Page) {
  await page.goto("/map");
  await page.getByRole("button", { name: "全部摊位", exact: true }).click();
  await expect(page.locator(".map-list-item")).toHaveCount(2);
}

test("resting homepage candidates preserve status address and optional planned hours at four widths", async ({ page }, info) => {
  const state = await fixture(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/");
    const empty = page.getByRole("region", { name: "附近出摊提示", exact: true });
    await expect(empty.getByRole("heading")).toHaveText("出摊位置正在等待商家确认");
    const candidates = empty.locator(".resting-stalls > a");
    await expect(candidates).toHaveCount(2);
    await expect(candidates.nth(0)).toContainText("暂歇");
    await expect(candidates.nth(0)).toContainText(state.rows[0]!.address);
    await expect(candidates.nth(0)).not.toContainText("通常");
    await expect(candidates.nth(1)).toContainText("状态待确认");
    await expect(candidates.nth(1)).toContainText(state.rows[1]!.address);
    await expect(candidates.nth(1)).toContainText("通常 17:00—21:00 · 商家计划");
    await expect(empty).not.toContainText("可线上点单");
    await targetSize(candidates.nth(0));
    await targetSize(candidates.nth(1));
    await targetSize(empty.getByRole("button", { name: "看看全部摊位", exact: true }));
    if (width <= 390) expect((await candidates.nth(1).boundingBox())!.height).toBeLessThan(150);
    await assertNoHorizontalOverflow(page);
    await empty.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`resting-candidates-${width}.png`), animations: "disabled" });
    await candidates.nth(1).click();
    await expect(page).toHaveURL(/\/stalls\/1082$/);
    await expect(page.getByRole("heading", { name: "东门饭摊", exact: true })).toBeVisible();
  }
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("map fallback stays compact and exposes complete guidance with selection focus restored at four widths", async ({ page }, info) => {
  const state = await fixture(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await openMap(page);
    await expect(page.getByRole("button", { name: "定位我的位置", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "重新加载地图", exact: true })).toHaveCount(0);
    await expect(page.locator(".map-unavailable")).toContainText("可从列表查看摊位地址与到摊指引");
    if (width <= 390) expect((await page.locator(".map-unavailable").boundingBox())!.height).toBeLessThan(150);
    const trigger = page.locator(".map-list-item").filter({ hasText: "东门饭摊" }).locator(".map-select");
    await expect(trigger).toHaveText("查看位置与路线");
    await targetSize(trigger);
    await trigger.click();
    const panel = page.getByRole("region", { name: "东门饭摊的位置与路线", exact: true });
    await expect(panel).toBeFocused();
    await expect(panel.getByRole("region", { name: "到摊指引", exact: true })).toBeVisible();
    await expect(panel).toContainText("位置已过期，请先联系商家确认再出发");
    await expect(panel).toContainText(state.rows[1]!.arrival_note);
    await expect(panel).toContainText("通常出摊：17:00—21:00");
    await expect(panel.locator(".visit-photo img")).toHaveAttribute("src", state.rows[1]!.arrival_image);
    await expect(panel.locator(".visit-facts")).toContainText("预计");
    await expect(panel.getByRole("link", { name: "联系商家", exact: true })).toHaveAttribute("href", "tel:13900001111");
    await expect(panel.getByRole("link", { name: "查看路线", exact: true })).toHaveAttribute("href", /120\.1,30\.1/);
    await expect(panel).not.toContainText("可线上点单");
    await targetSize(panel.getByRole("link", { name: "查看路线", exact: true }));
    await targetSize(panel.getByRole("button", { name: "关闭选中摊位", exact: true }));
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: info.outputPath(`map-guidance-${width}.png`), animations: "disabled" });
    await panel.getByRole("button", { name: "关闭选中摊位", exact: true }).click();
    await expect(panel).toHaveCount(0);
    await expect(trigger).toBeFocused();
  }
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("a background refresh preserves pending explicit selection then leaves focus and reading position alone", async ({ page }, info) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openMap(page);
  const panel = page.getByRole("region", { name: "东门饭摊的位置与路线", exact: true });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  state.beforeDetail = () => state.detailReads === 1 ? gate : Promise.resolve();
  try {
    await page.locator(".map-list-item").filter({ hasText: "东门饭摊" }).locator(".map-select").click();
    await expect.poll(() => state.detailReads).toBe(1);
    const previousMaps = state.mapReads;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => state.mapReads).toBeGreaterThan(previousMaps);
    // Let the summary watcher run while the explicit detail request is pending.
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    release();
    await expect(panel).toBeFocused();
  } finally {
    release();
    state.beforeDetail = null;
  }
  const search = page.getByRole("textbox", { name: "地图搜索", exact: true });
  await search.focus();
  const previousTop = await page.evaluate(() => window.scrollY), beforeReads = state.detailReads;
  state.rows[1]!.arrival_note = "蓝色棚旁有银杏树，请先联系商家确认";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.detailReads).toBeGreaterThan(beforeReads);
  await expect(panel).toContainText(state.rows[1]!.arrival_note);
  await expect(search).toBeFocused();
  await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - previousTop)).toBeLessThanOrEqual(1);
  await assertNoHorizontalOverflow(page);
  await page.screenshot({ path: info.outputPath("map-background-focus-390.png"), animations: "disabled" });
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("background guidance refresh preserves a focused route action while hiding unverified guidance", async ({ page }, info) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openMap(page);
  await page.locator(".map-list-item").filter({ hasText: "东门饭摊" }).locator(".map-select").click();
  const panel = page.getByRole("region", { name: "东门饭摊的位置与路线", exact: true });
  await expect(panel).toBeFocused();
  const directions = panel.getByRole("link", { name: "查看路线", exact: true });
  await directions.focus();
  const previousTop = await page.evaluate(() => window.scrollY), beforeReads = state.detailReads;
  state.rows[1]!.arrival_note = "银杏树旁蓝棚，出发前请联系确认";
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  state.beforeDetail = () => gate;
  try {
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => state.detailReads).toBeGreaterThan(beforeReads);
    await expect(panel.getByRole("status")).toHaveText("正在核对最新出摊信息…");
    await expect(directions).toBeHidden();
    await expect(panel.locator(".visit-address")).toBeHidden();
    release();
    await expect(panel).toContainText(state.rows[1]!.arrival_note);
  } finally {
    release();
    state.beforeDetail = null;
  }
  await expect(directions).toBeFocused();
  await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - previousTop)).toBeLessThanOrEqual(1);
  state.detailStatus = 503;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(panel.getByRole("alert")).toContainText("暂未同步最新到摊指引");
  await expect(directions).toBeHidden();
  await expect(panel.locator(".visit-address")).toBeHidden();
  await page.screenshot({ path: info.outputPath("map-refresh-unverified-390.png"), animations: "disabled" });
  state.detailStatus = 200;
  await panel.getByRole("button", { name: "重新核对", exact: true }).click();
  await expect(directions).toBeVisible();
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("same-account identity refresh preserves selection and focus while an account switch clears the card", async ({ page }, info) => {
  const state = await fixture(page);
  state.user = { id: 81, username: "student81", display_name: "看摊同学", is_merchant: false, is_staff: false };
  await page.setViewportSize({ width: 390, height: 844 });
  await openMap(page);
  await expect(page.locator(".header-user")).toContainText("看摊同学");
  await page.locator(".map-list-item").filter({ hasText: "东门饭摊" }).locator(".map-select").click();
  const panel = page.getByRole("region", { name: "东门饭摊的位置与路线", exact: true });
  await expect(panel).toBeFocused();
  const directions = panel.getByRole("link", { name: "查看路线", exact: true });
  await directions.focus();
  const previousTop = await page.evaluate(() => window.scrollY), beforeReads = state.detailReads;

  // A new response object and changed profile text still represent the same account.
  state.user = { ...state.user, display_name: "更新昵称的看摊同学" };
  state.rows[1]!.arrival_note = "蓝色棚旁有银杏树，位置资料已重新核对";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator(".header-user")).toContainText(state.user.display_name);
  await expect.poll(() => state.detailReads).toBeGreaterThan(beforeReads);
  await expect(panel).toContainText(state.rows[1]!.arrival_note);
  await expect(directions).toBeFocused();
  await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - previousTop)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath("map-same-account-refresh-390.png"), animations: "disabled" });

  state.user = { ...state.user, id: 82, username: "student82", display_name: "另一位同学" };
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator(".header-user")).toContainText(state.user.display_name);
  await expect(panel).toHaveCount(0);
  await expect(page.locator(".map-list-item")).toHaveCount(2);
  await expect(page.getByRole("region", { name: "到摊指引", exact: true })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("map-switched-account-390.png"), animations: "disabled" });
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});
