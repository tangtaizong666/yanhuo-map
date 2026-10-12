import { expect, test, type Page } from "@playwright/test";
import { fulfillCsrf } from "../../helpers";

async function fixture(page: Page, mapEnabled = false) {
  const user = { id: 71, username: "student", display_name: "同学", is_merchant: false, is_staff: false };
  const products = Array.from({ length: 6 }, (_, n) => ({ id: 711 + n, name: `鸡蛋煎饼${n + 1}`, description: "现摊现做", category: "小吃", image: "/images/food-jianbing.jpg", price_cents: 800, stock: 20, availability: "available", max_order_quantity: 10, is_active: true, sale_paused: false, taste_options: [] }));
  const stall = { id: 71, name: "东门煎饼摊", description: "现摊现做", category: "小吃", image: "/images/food-jianbing.jpg", address: "东门蓝色雨棚旁", latitude: 31, longitude: 121, area_id: 1, area_name: "校园", status: "open", session_status: "open", last_confirmed_at: new Date().toISOString(), prep_minutes: 8, transaction_enabled: true, can_order: true, rating: null, review_count: 0, is_followed: true, products, accepting_orders: true, order_unavailable_reason: "", usual_hours: "11:00—14:00", receiving_status: "recent", receiving_valid_for_seconds: 30, reviews: [], contact_phone: "13800000000", closes_at: null, merchant_name: "摊主", qualification_note: "已核验", arrival_note: "认准蓝色雨棚", arrival_image: "", wechat_payment: { supported: true, available: false, mode: "live", channels: [], reason: "尚未配置" }, delivery: { available: false, enabled: false, mode: "live", reason: "不配送", points: [] } };
  const state = { user, stall, reads: [] as string[], stallReads: [] as string[], details: 0, removed: false, failPage: false, delay: 0, detailStatus: 200, writes: [] as string[], paginatedStalls: false, firstStallDelay: 0, beforeStallAppend: null as null | (() => Promise<void>), beforeDetail: null as null | (() => Promise<void>), mapStallCount: 1 };
  await page.addInitScript(({ food, mapEnabled }) => {
    localStorage.setItem("yanhuo-cart-v2:user:71", JSON.stringify({ 71: [{ product: food, quantity: 1, portions: [{ options: {}, note: "少油" }], portionKeys: ["a"] }] }));
    if (mapEnabled) {
      (window as any).__maps = [];
      (window as any).__mapInstances = [];
      (window as any).AMap = {
        Map: class {
          center: number[]; zoom: number;
          constructor(_node: any, options: any) { this.center = options.center; this.zoom = options.zoom; (window as any).__maps.push(options); (window as any).__mapInstances.push(this); }
          getCenter() { return { lng: this.center[0], lat: this.center[1] }; }
          getZoom() { return this.zoom; }
          getBounds() { return { getSouthWest: () => ({ lng: 120, lat: 30 }), getNorthEast: () => ({ lng: 122, lat: 32 }) }; }
          addControl() {} on() {} add() {} remove() {} destroy() {}
          panTo(center: number[]) { this.center = center; }
          setZoomAndCenter(zoom: number, center: number[]) { this.zoom = zoom; this.center = center; }
        },
        Marker: class { on() {} }, Scale: class {},
      };
    }
  }, { food: products[0], mapEnabled });
  await page.route("https://**/*", route => route.abort());
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url()), path = url.pathname.replace("/api/v1", "");
    const send = (json: unknown) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/events") return send({ id: 1 });
    if (route.request().method() !== "GET") state.writes.push(path);
    if (path === "/config") return send({ user: state.user, brand: "烟火地图", demo_mode: false, stale_minutes: 60, amap_key: mapEnabled ? "isolated-map" : "", areas: [{ id: 1, name: "校园", latitude: 31, longitude: 121 }, { id: 2, name: "另一校园", latitude: 32, longitude: 122 }] });
    if (path === "/auth/me") return send(state.user);
    if (path === "/orders/active-summary") return send({ user_id: state.user.id, orders: [], count: 0, status_counts: {}, synced_at: new Date().toISOString() });
    if (path === "/orders/recent-completed") return send([]);
    if (path === "/products") {
      state.reads.push(url.search);
      if (state.delay) await new Promise(resolve => setTimeout(resolve, state.delay));
      const cursor = url.searchParams.get("cursor");
      if (cursor && state.failPage) return route.fulfill({ status: 503, json: { detail: "暂时无法读取后续餐点" } });
      const rows = cursor ? products.slice(3) : products.slice(0, 3);
      return send({ results: rows.filter(p => !state.removed || p.id !== 716).map(product => ({ product, stall })), next: cursor ? null : "fresh-page-2" });
    }
    if (path === "/stalls" && state.paginatedStalls) {
      state.stallReads.push(url.search);
      const cursor = url.searchParams.get("cursor");
      if (cursor) await state.beforeStallAppend?.();
      else if (state.firstStallDelay) await new Promise(resolve => setTimeout(resolve, state.firstStallDelay));
      const rows = cursor
        ? [81, 82, 71].map(id => id === 71 ? stall : { ...stall, id, name: `校园煎饼摊${id}` })
        : [78, 79, 80].map(id => ({ ...stall, id, name: `校园煎饼摊${id}` }));
      return send({ results: state.removed ? [] : rows, next: cursor ? null : "fresh-stall-page-2" });
    }
    if (path === "/stalls/map" && state.mapStallCount > 1) {
      const rows = [...Array.from({ length: state.mapStallCount - 1 }, (_, index) => ({
        ...stall, id: 100 + index, name: `校园小食摊${index + 1}`, products: [],
        latitude: 31 + index / 1000, longitude: 121 + index / 1000,
      })), stall];
      return send({ results: state.removed ? [] : rows, next: null, truncated: false });
    }
    if (path === "/stalls" || path === "/stalls/map") return send({ results: state.removed ? [] : [stall], next: null, truncated: false });
    if (path === "/stalls/71") {
      state.details++;
      await state.beforeDetail?.();
      return route.fulfill({ status: state.detailStatus, json: state.detailStatus === 200 ? state.stall : { detail: "无法核验位置" } });
    }
    return route.fulfill({ status: 500, json: { detail: `Unexpected isolated request: ${path}` } });
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  return state;
}

async function openSecondPage(page: Page) {
  await page.goto("/search?q=煎饼&type=dishes&meal_budget=1500&meal_sort=price");
  await expect(page.locator(".dish-card")).toHaveCount(3);
  await page.getByRole("button", { name: "加载更多餐点" }).click();
  await expect(page.locator(".dish-card")).toHaveCount(6);
  const target = page.locator('.dish-card[href="/stalls/71/products/716"]');
  await target.scrollIntoViewIfNeeded();
  const y = (await target.boundingBox())!.y;
  await target.click();
  await expect(page).toHaveURL(/\/stalls\/71\/products\/716$/);
  await expect(page.getByRole("heading", { name: "鸡蛋煎饼6", exact: true })).toBeVisible();
  return y;
}

test("navigation return reloads paginated search and restores the same item and filters", async ({ page }, info) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const y = await openSecondPage(page);
  state.reads = [];
  await page.getByRole("link", { name: "返回搜索结果", exact: true }).click();
  await expect(page).toHaveURL(/type=dishes&meal_budget=1500&meal_sort=price/);
  const target = page.locator('.dish-card[href="/stalls/71/products/716"]');
  await expect(target).toBeFocused();
  expect(Math.abs((await target.boundingBox())!.y - y)).toBeLessThanOrEqual(2);
  expect(state.reads).toHaveLength(2);
  expect(state.reads[0]).not.toContain("cursor=");
  expect(state.reads[1]).toContain("cursor=fresh-page-2");
  await page.screenshot({ path: info.outputPath("restored-search-390.png") });
  expect(state.writes).toEqual([]);
});

for (const outcome of ["removed", "failed", "timeout", "user-scroll"] as const) {
  test(`navigation return stops safely when paginated source is ${outcome}`, async ({ page }) => {
    const state = await fixture(page);
    await openSecondPage(page);
    state.reads = [];
    if (outcome === "removed") state.removed = true;
    if (outcome === "failed") state.failPage = true;
    if (outcome === "timeout") state.delay = 5500;
    if (outcome === "user-scroll") state.delay = 800;
    await page.getByRole("link", { name: "返回搜索结果", exact: true }).click();
    await expect(page).toHaveURL(/\/search\?/);
    if (outcome === "user-scroll") {
      await page.mouse.wheel(0, 80);
      await expect(page.locator(".dish-card")).toHaveCount(3);
      expect(state.reads).toHaveLength(1);
      await expect(page.locator('.dish-card[href="/stalls/71/products/716"]')).toHaveCount(0);
    } else {
      await expect(page.getByText("列表已更新，暂时无法回到原位置，请查看当前结果。", { exact: true })).toBeVisible({ timeout: 8000 });
      expect(state.reads.length).toBeLessThanOrEqual(2);
    }
    expect(state.writes).toEqual([]);
  });
}

test("navigation return keeps nested menu history and cart and checkout drafts", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/search?type=stalls");
  const stallTitle = page.locator('.stall-title[href="/stalls/71"]');
  await stallTitle.scrollIntoViewIfNeeded();
  await stallTitle.evaluate(element => element.focus({ preventScroll: true }));
  await expect(stallTitle).toBeFocused();
  const stallY = (await stallTitle.boundingBox())!.y;
  await stallTitle.click();
  await expect(page.getByRole("link", { name: "返回搜索结果", exact: true })).toBeVisible();
  const productTitle = page.locator('.product-detail-link[href="/stalls/71/products/711"]');
  await productTitle.scrollIntoViewIfNeeded();
  await productTitle.evaluate(element => element.focus({ preventScroll: true }));
  await expect(productTitle).toBeFocused();
  const productY = (await productTitle.boundingBox())!.y;
  await productTitle.click();
  await page.getByRole("link", { name: "返回菜单", exact: true }).click();
  await expect(productTitle).toBeFocused();
  await expect(page.locator('.product-image[href="/stalls/71/products/711"]')).not.toBeFocused();
  expect(Math.abs((await productTitle.boundingBox())!.y - productY)).toBeLessThanOrEqual(2);
  await page.getByRole("link", { name: "返回搜索结果", exact: true }).click();
  await expect(page).toHaveURL(/\/search\?type=stalls/);
  await expect(stallTitle).toBeFocused();
  await expect(page.locator('.stall-photo[href="/stalls/71"]')).not.toBeFocused();
  expect(Math.abs((await stallTitle.boundingBox())!.y - stallY)).toBeLessThanOrEqual(2);
  for (const [source, label] of [["/cart", "返回餐袋"], ["/checkout/71", "返回订单确认"]]) {
    await page.goto(source);
    await page.locator('main a[href="/stalls/71/products/711"]').first().click();
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${source}$`));
    await expect(page.locator('main a[href="/stalls/71/products/711"]').first()).toBeFocused();
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem("yanhuo-cart-v2:user:71")!)[71][0].quantity)).toBe(1);
  }
  expect(state.writes).toEqual([]);
});

for (const mapEnabled of [false, true]) {
  test(`navigation return revalidates selected map directions with map ${mapEnabled}`, async ({ page }) => {
    const state = await fixture(page, mapEnabled);
    await page.goto("/map");
    await page.locator(".map-select").first().click();
    const panel = page.getByRole("region", { name: "东门煎饼摊的位置与路线", exact: true });
    await expect(panel).toContainText("认准蓝色雨棚");
    await panel.locator('a[href="/stalls/71"]').click();
    state.stall.arrival_note = "商家刚更新的认摊说明";
    const reads = state.details;
    await page.getByRole("link", { name: "返回地图找摊", exact: true }).click();
    await expect(panel).toContainText("商家刚更新的认摊说明");
    expect(state.details).toBeGreaterThan(reads);
    await expect(panel).not.toContainText("认准蓝色雨棚");
    await panel.locator('a[href="/stalls/71"]').click();
    state.detailStatus = 503;
    await page.getByRole("link", { name: "返回地图找摊", exact: true }).click();
    await expect(page.getByText("无法核验位置", { exact: true })).toBeVisible();
    await expect(page.getByRole("region", { name: "到摊指引", exact: true })).toBeHidden();
    expect(state.writes).toEqual([]);
  });
}

test("navigation context cannot survive refresh identity changes or a forged external source", async ({ page }) => {
  const state = await fixture(page);
  await openSecondPage(page);
  await page.reload();
  await expect(page.getByRole("link", { name: "返回小摊", exact: true })).toBeVisible();
  await page.evaluate(() => history.replaceState({ ...history.state, back: "https://example.com", yanhuoBrowse: "fake" }, ""));
  await page.getByRole("link", { name: "返回小摊", exact: true }).click();
  await expect(page).toHaveURL(/\/stalls\/71$/);
  await openSecondPage(page);
  state.user = { ...state.user, id: 72, username: "another" };
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("link", { name: "返回小摊", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "返回小摊", exact: true }).click();
  await expect(page).toHaveURL(/\/stalls\/71$/);
  expect(state.writes).toEqual([]);
});

test("navigation return remains responsive when a focus refresh interrupts stall pagination", async ({ page }) => {
  const state = await fixture(page);
  state.paginatedStalls = true;
  await page.goto("/search?type=stalls");
  await expect(page.locator(".food-grid > .stall-card")).toHaveCount(3);
  await page.getByRole("button", { name: "加载更多摊位", exact: true }).click();
  await expect(page.locator(".food-grid > .stall-card")).toHaveCount(6);
  const target = page.locator('.stall-title[href="/stalls/71"]');
  await target.click();
  await expect(page).toHaveURL(/\/stalls\/71$/);
  await expect(page.getByRole("link", { name: "返回搜索结果", exact: true })).toBeVisible();

  state.stallReads = [];
  let releaseAppend = () => {};
  let appendWaiting = false;
  state.beforeStallAppend = () => {
    appendWaiting = true;
    return new Promise<void>(resolve => { releaseAppend = resolve; });
  };
  try {
    await page.getByRole("link", { name: "返回搜索结果", exact: true }).click();
    await expect(page).toHaveURL(/\/search\?type=stalls$/);
    await expect.poll(() => appendWaiting).toBe(true);
    // Focus restarts the first-page read while the recovery append is pending.
    // The old append aborts before this slow replacement read can complete.
    state.firstStallDelay = 350;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => state.stallReads.filter(query => !new URLSearchParams(query).has("cursor")).length).toBe(2);
    state.beforeStallAppend = null;
    releaseAppend();

    await expect(target).toBeFocused({ timeout: 6000 });
    await expect(page.locator(".food-grid > .stall-card")).toHaveCount(6);
    expect(state.stallReads).toHaveLength(4);
    expect(state.stallReads.filter(query => new URLSearchParams(query).get("cursor") === "fresh-stall-page-2")).toHaveLength(2);
    await expect(page.getByText("列表已更新，暂时无法回到原位置，请查看当前结果。", { exact: true })).toHaveCount(0);
    // A timer must still run; a resolved-promise retry loop would starve it.
    expect(await page.evaluate(() => new Promise(resolve => setTimeout(() => resolve("responsive"), 0)))).toBe("responsive");
  } finally {
    state.beforeStallAppend = null;
    releaseAppend();
  }
  expect(state.writes).toEqual([]);
});

for (const mapEnabled of [false, true]) {
  test(`navigation return distinguishes selected-panel and list links with map ${mapEnabled}`, async ({ page }) => {
    const state = await fixture(page, mapEnabled);
    await page.setViewportSize({ width: mapEnabled ? 1440 : 390, height: 844 });
    for (const source of ["panel", "list"] as const) {
      await page.goto("/map");
      await page.locator(".map-select").first().click();
      const panel = page.getByRole("region", { name: "东门煎饼摊的位置与路线", exact: true });
      await expect(panel).toContainText("认准蓝色雨棚");
      const panelLink = panel.locator('a[href="/stalls/71"]');
      const listLink = page.locator('.map-results a[href="/stalls/71"]').first();
      const target = source === "panel" ? panelLink : listLink;
      const other = source === "panel" ? listLink : panelLink;
      await target.scrollIntoViewIfNeeded();
      await expect(target).toBeInViewport({ ratio: 1 });
      const y = (await target.boundingBox())!.y;
      // Match platforms where pointer activation does not focus a link. Source
      // identity must come from the click, not the previously focused control.
      await target.evaluate(element => element.addEventListener("mousedown", event => event.preventDefault(), { once: true }));
      await target.click();
      await expect(page).toHaveURL(/\/stalls\/71$/);
      await page.getByRole("link", { name: "返回地图找摊", exact: true }).click();
      await expect(page).toHaveURL(/\/map$/);
      await expect(target).toBeFocused();
      await expect(other).not.toBeFocused();
      expect(Math.abs((await target.boundingBox())!.y - y)).toBeLessThanOrEqual(2);
      await expect(panel).toContainText("认准蓝色雨棚");
    }
    expect(state.writes).toEqual([]);
  });
}

test("navigation return preserves its source across browser forward and back", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await openSecondPage(page);
  await page.getByRole("link", { name: "返回搜索结果", exact: true }).click();
  const target = page.locator('.dish-card[href="/stalls/71/products/716"]');
  await expect(target).toBeFocused();
  await page.goForward();
  await expect(page).toHaveURL(/\/stalls\/71\/products\/716$/);
  await expect(page.getByRole("link", { name: "返回搜索结果", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/type=dishes&meal_budget=1500&meal_sort=price/);
  await expect(target).toBeFocused();
  expect(state.writes).toEqual([]);
});

test("navigation return uses the safe default after a campus change", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await openSecondPage(page);
  await page.getByRole("combobox", { name: "选择校园", exact: true }).selectOption("2");
  await expect(page.getByRole("link", { name: "返回小摊", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "返回搜索结果", exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "返回小摊", exact: true }).click();
  await expect(page).toHaveURL(/\/stalls\/71$/);
  await expect(page.getByRole("combobox", { name: "选择校园", exact: true })).toHaveValue("2");
  expect(state.writes).toEqual([]);
});

for (const outcome of ["fresh", "failed"] as const) {
  test(`navigation return waits for current cart data before focus when the read is ${outcome}`, async ({ page }) => {
    const state = await fixture(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/cart");
    const target = page.locator('.cart-food-photo[href="/stalls/71/products/711"]');
    await expect(page.locator(".cart-stall-alert")).toHaveCount(0);
    await target.click();
    await expect(page).toHaveURL(/\/stalls\/71\/products\/711$/);
    await expect(page.getByRole("heading", { name: "鸡蛋煎饼1", exact: true })).toBeVisible();

    let detailWaiting = false;
    let releaseDetail = () => {};
    state.beforeDetail = () => {
      detailWaiting = true;
      return new Promise<void>(resolve => { releaseDetail = resolve; });
    };
    if (outcome === "failed") state.detailStatus = 503;
    else state.stall.products[0]!.name = "商家更新后的鸡蛋煎饼";
    try {
      await page.getByRole("link", { name: "返回餐袋", exact: true }).click();
      await expect(page).toHaveURL(/\/cart$/);
      await expect.poll(() => detailWaiting).toBe(true);
      // The saved cart can render its links immediately. Their existence alone
      // must not signal that the current menu and the final layout are ready.
      await expect(target).toBeVisible();
      await expect(page.locator(".cart-stall-alert")).toContainText("核对");
      await expect(target).not.toBeFocused();
      state.beforeDetail = null;
      releaseDetail();
      if (outcome === "fresh") {
        await expect(target).toBeFocused();
        await expect(page.locator(".cart-food-content h3")).toHaveText("商家更新后的鸡蛋煎饼");
      } else {
        await expect(page.getByText("列表已更新，暂时无法回到原位置，请查看当前结果。", { exact: true })).toBeVisible();
        await expect(target).not.toBeFocused();
        await expect(page.locator(".cart-stall-alert")).toContainText("无法核验位置");
      }
      expect(await page.evaluate(() => JSON.parse(localStorage.getItem("yanhuo-cart-v2:user:71")!)[71][0].quantity)).toBe(1);
    } finally {
      state.beforeDetail = null;
      releaseDetail();
    }
    expect(state.writes).toEqual([]);
  });
}

test("navigation return restores a scrolled desktop map list and the chosen map viewport", async ({ page }, info) => {
  const state = await fixture(page, true);
  state.mapStallCount = 14;
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/map");
  await expect(page.locator(".map-list-item")).toHaveCount(14);
  const row = page.locator(".map-list-item").filter({ has: page.locator('a[href="/stalls/71"]') });
  await row.locator(".map-select").click();
  await expect(page.getByRole("region", { name: "东门煎饼摊的位置与路线", exact: true })).toContainText("认准蓝色雨棚");

  const desiredView = { center: [121.075, 31.042], zoom: 17 };
  const beforeMaps = await page.evaluate(view => {
    const instances = (window as any).__mapInstances;
    instances.at(-1).setZoomAndCenter(view.zoom, view.center);
    return instances.length;
  }, desiredView);
  const target = row.locator('a[href="/stalls/71"]');
  await target.scrollIntoViewIfNeeded();
  await target.evaluate(element => element.focus({ preventScroll: true }));
  await expect(target).toBeFocused();
  await expect(target).toBeInViewport({ ratio: 1 });
  const before = await target.evaluate(element => {
    const list = element.closest<HTMLElement>(".map-results")!;
    return { scrollTop: list.scrollTop, offset: element.getBoundingClientRect().top - list.getBoundingClientRect().top };
  });
  expect(before.scrollTop).toBeGreaterThan(500);
  await target.click();
  await expect(page).toHaveURL(/\/stalls\/71$/);
  const readsBeforeReturn = state.details;
  await page.getByRole("link", { name: "返回地图找摊", exact: true }).click();
  await expect(page).toHaveURL(/\/map$/);
  await expect(target).toBeFocused();
  await expect(target).toBeInViewport({ ratio: 1 });
  const after = await target.evaluate(element => {
    const list = element.closest<HTMLElement>(".map-results")!;
    return { scrollTop: list.scrollTop, offset: element.getBoundingClientRect().top - list.getBoundingClientRect().top };
  });
  expect(Math.abs(after.scrollTop - before.scrollTop)).toBeLessThanOrEqual(2);
  expect(Math.abs(after.offset - before.offset)).toBeLessThanOrEqual(2);
  const restoredMap = await page.evaluate(() => {
    const instances = (window as any).__mapInstances, options = (window as any).__maps.at(-1), current = instances.at(-1);
    return { count: instances.length, initial: { center: options.center, zoom: options.zoom }, current: { center: current.center, zoom: current.zoom } };
  });
  expect(restoredMap).toEqual({ count: beforeMaps + 1, initial: desiredView, current: desiredView });
  expect(state.details).toBeGreaterThan(readsBeforeReturn);
  expect(state.writes).toEqual([]);
  await page.screenshot({ path: info.outputPath("map-list-scroll-and-viewport-restored.png"), animations: "disabled" });
});
