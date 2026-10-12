import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../helpers";

const user = { id: 991, username: "discovery-window", display_name: "同学", is_merchant: false, is_staff: false };
function stall(id: number, followed = false) {
  return { id, name: `第${id}页煎饼摊`, description: "隔离测试", image: "/images/food-jianbing.jpg", area_id: 1,
    area_name: "测试校园", category: "小吃", address: `南门${id}号`, latitude: 30, longitude: 120,
    status: "open", can_order: true, transaction_enabled: true, last_confirmed_at: new Date().toISOString(),
    prep_minutes: 10, is_followed: followed, products: [], review_count: 0, receiving_status: "unknown" };
}
function meal(id: number) {
  return { product: { id, name: `第${id}页煎饼`, image: "/images/food-jianbing.jpg", price_cents: 900,
    availability: "available", max_order_quantity: 10, is_active: true }, stall: stall(id) };
}
async function fixture(page: Page, kind: "stalls" | "meals" = "stalls") {
  const state = { user: user as typeof user | null, empty: false, followed: false, emptySecond: false,
    failSecond: false, holdSecond: false, thirdPage: false, fresh: false, pending: false, release: () => {},
    detail: { ...stall(1), arrival_note: "绿色棚顶，靠近南门便利店", arrival_image: "/fixtures/stall-recognition.svg",
      closes_at: "", usual_hours: "", reviews: [], contact_phone: "", accepting_orders: true, order_unavailable_reason: "" },
    requests: [] as string[], unexpected: [] as string[] };
  // Synthetic geometry only; never add a food photo as merchant scene evidence.
  await page.route("**/fixtures/stall-recognition.svg", route => route.fulfill({ contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360"><rect width="640" height="360" fill="#f4ebd9"/><path d="M120 150L170 80H470L520 150Z" fill="#477457"/><path d="M145 150V300M495 150V300" stroke="#735f4e" stroke-width="12"/><rect x="160" y="210" width="320" height="90" fill="#bf9468"/></svg>' }));
  await page.route("https://**/*", route => route.abort());
  await page.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace("/api/v1", "");
    const send = (json: unknown) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config") return send({ user: state.user, demo_mode: false, amap_key: "", areas: [{ id: 1, name: "测试校园" }] });
    if (path === "/auth/me") return send(state.user);
    if (path === "/orders/active-summary") return send({ user_id: state.user?.id, counts: { total: 0 }, order: null });
    if (path === "/orders/recent-completed") return send([]);
    if (path === "/events") return send({});
    if (path === "/stalls/1") return send(state.detail);
    if (path === "/stalls" || path === "/products") {
      state.requests.push(path + url.search);
      if (state.empty || (url.searchParams.has("follow") && !state.followed)) return send({ results: [], next: null });
      if (path !== (kind === "stalls" ? "/stalls" : "/products")) return send({ results: [], next: null });
      const second = url.searchParams.has("cursor"), filtered = url.searchParams.has("category");
      const third = url.searchParams.get("cursor") === "third";
      const id = filtered ? 9 : third ? 3 : second ? 2 : 1;
      const row = kind === "stalls" ? stall(id, state.followed) : meal(id);
      if (state.fresh) {
        if ("product" in row) row.product.name += "已更新";
        else row.name += "已更新";
      }
      const result = { results: second && state.emptySecond ? [] : [row],
        next: third || filtered ? null : second ? state.thirdPage ? "third" : null : state.fresh ? "new-second" : "second" };
      if (second && state.holdSecond) {
        state.pending = true;
        await new Promise<void>(resolve => { state.release = resolve; });
      }
      if (second && state.failSecond) return route.fulfill({ status: 503, json: { detail: "后页暂未同步" } });
      try { return await send(result); } catch { /* Delayed response may have been aborted by a filter change. */ }
      return;
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 500, json: { detail: "Unexpected isolated request" } });
  });
  return state;
}

test("stall arrival evidence is immediately visible before ratings at four supported widths", async ({ page }, info) => {
  const state = await fixture(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/stalls/1");
    await expect(page.locator(".stall-hero")).toBeVisible();
    await page.screenshot({ path: info.outputPath(`stall-arrival-${width}.png`), fullPage: true });
    const guide = page.getByRole("region", { name: "到摊指引" });
    await expect(guide).toBeVisible();
    await expect(guide).toContainText("绿色棚顶，靠近南门便利店");
    await expect(guide.locator(".visit-photo img")).toBeVisible();
    await expect(guide).toContainText("非实时画面");
    const address = await guide.locator(".visit-address").boundingBox();
    const confirmed = await guide.locator(".visit-facts").boundingBox();
    const rating = await page.locator(".overview-rating").boundingBox();
    expect(address!.y).toBeLessThan(rating!.y);
    expect(confirmed!.y).toBeLessThan(rating!.y);
    if (width <= 390) expect(confirmed!.y + confirmed!.height).toBeLessThan(700);
    await assertNoHorizontalOverflow(page);
  }
  expect(state.unexpected).toEqual([]);
});

test("stall without merchant recognition data shows an honest missing description and no scene image", async ({ page }) => {
  const state = await fixture(page);
  state.detail.arrival_note = "";
  state.detail.arrival_image = "";
  await page.setViewportSize({ width: 360, height: 844 });
  await page.goto("/stalls/1");
  await expect(page.getByRole("region", { name: "到摊指引" })).toContainText("商家尚未补充认摊说明");
  await expect(page.locator(".visit-photo")).toHaveCount(0);
});

test("a stall without recognition material keeps the first meal action in the mobile viewport", async ({ page }, info) => {
  const state = await fixture(page);
  Object.assign(state.detail, { arrival_note: "", arrival_image: "", usual_hours: "11:00–14:00",
    contact_phone: "13800000000", products: [meal(1).product], receiving_status: "recent", receiving_valid_for_seconds: 30 });
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/stalls/1");
    const guide = page.getByRole("region", { name: "到摊指引" });
    await expect(guide.locator(".visit-address")).toHaveText("南门1号");
    await expect(guide.locator(".visit-facts")).toContainText("商家");
    await expect(guide).toContainText("商家尚未补充认摊说明");
    if (width <= 390) {
      const add = page.getByRole("button", { name: "添加第1页煎饼", exact: true });
      await expect(add).toBeInViewport({ ratio: 1 });
      await expect(page.locator(".product-bottom .price").first()).toBeInViewport({ ratio: 1 });
      expect((await add.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await expect(guide.getByRole("button", { name: "联系与更多信息" })).toHaveAttribute("aria-expanded", "false");
      await expect(guide.getByText(/通常出摊/)).toBeHidden();
    }
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: info.outputPath(`stall-first-meal-${width}.png`) });
  }
  expect(state.unexpected).toEqual([]);
});

for (const width of [360, 390]) {
  test(`mobile arrival utilities expand by keyboard while recognition stays visible at ${width}px`, async ({ page }, info) => {
    const state = await fixture(page);
    Object.assign(state.detail, { usual_hours: "11:00–14:00", contact_phone: "13800000000" });
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/stalls/1");
    const guide = page.getByRole("region", { name: "到摊指引" });
    const toggle = guide.getByRole("button", { name: "联系与更多信息" });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(guide.getByText(/通常出摊/)).toBeHidden();
    await expect(guide.getByRole("link", { name: "联系商家" })).toBeHidden();
    await expect(guide.getByRole("button", { name: "分享小摊" })).toBeHidden();
    await expect(guide.locator(".visit-photo img")).toBeVisible();
    await expect(guide.getByText("绿色棚顶，靠近南门便利店", { exact: true })).toBeVisible();
    await expect(guide.getByRole("link", { name: "查看路线" })).toBeVisible();
    await toggle.focus();
    await page.keyboard.press("Enter");
    const collapse = guide.getByRole("button", { name: "收起辅助信息" });
    await expect(collapse).toHaveAttribute("aria-expanded", "true");
    await expect(guide.getByText(/通常出摊/)).toBeVisible();
    await expect(guide.getByRole("link", { name: "联系商家" })).toHaveAttribute("href", "tel:13800000000");
    await expect(guide.getByRole("button", { name: "复制地址" })).toBeVisible();
    await guide.getByRole("button", { name: /没找到摊位/ }).click();
    await expect(page.getByRole("dialog", { name: "没找到 第1页煎饼摊？" })).toBeVisible();
    await page.getByRole("button", { name: "关闭位置反馈" }).click();
    await guide.getByRole("button", { name: "分享小摊" }).click();
    await expect(page.getByRole("dialog", { name: "把好味道分享出去" })).toBeVisible();
    await page.getByRole("button", { name: "关闭分享" }).click();
    await page.screenshot({ path: info.outputPath(`stall-utilities-expanded-${width}.png`), fullPage: true });
    await collapse.focus();
    await page.keyboard.press("Space");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(guide.getByText(/通常出摊/)).toBeHidden();
    await expect(guide.locator(".visit-photo img")).toBeVisible();
    await expect(guide.getByText("绿色棚顶，靠近南门便利店", { exact: true })).toBeVisible();
    await page.setViewportSize({ width: 768, height: 844 });
    await expect(toggle).toHaveCount(0);
    await expect(guide.getByRole("link", { name: "联系商家" })).toBeVisible();
    await expect(guide.getByRole("button", { name: "分享小摊" })).toBeVisible();
    await page.setViewportSize({ width, height: 844 });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await assertNoHorizontalOverflow(page);
    expect(state.unexpected).toEqual([]);
  });
}

for (const [status, reason] of [["stale", "位置已过期"], ["closed", "商家已收摊"], ["open", "备餐已满"]]) {
  test(`a qualified ${status} stall describes current unavailability instead of lost ordering eligibility`, async ({ page }) => {
    const state = await fixture(page);
    Object.assign(state.detail, { status, can_order: false, accepting_orders: false, order_unavailable_reason: reason });
    await page.setViewportSize({ width: 360, height: 844 });
    await page.goto("/stalls/1");
    await expect(page.getByRole("region", { name: "到摊指引" }).locator(".visit-warning")).toContainText(reason!);
    await expect(page.getByRole("button", { name: "联系与更多信息" })).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator(".overview-facts")).not.toContainText("暂未开通在线点单");
    await expect(page.locator(".overview-facts")).toContainText(reason!);
  });
}

for (const area of [0, 1]) {
  test(`empty follows describe the signed-in account scope in area ${area}`, async ({ page }) => {
    const state = await fixture(page);
    await page.addInitScript(area => localStorage.setItem("yanhuo-area", String(area)), area);
    await page.goto("/?follow=1");
    await expect(page.locator(".discovery-empty h3")).toHaveText(area ? "当前区域还没有关注的摊位" : "还没有关注的摊位");
    await expect(page.locator(".discovery-empty")).toContainText("先看看全部摊位");
    await expect(page.locator(".discovery-empty")).not.toContainText("还没有资料");
    await page.getByRole("button", { name: "看看全部摊位", exact: true }).click();
    await expect(page.getByRole("heading", { name: "第1页煎饼摊", exact: true })).toBeVisible();
    expect(state.unexpected).toEqual([]);
  });
}

test("anonymous follows request sign-in and public empty results keep the collection explanation", async ({ page }) => {
  const state = await fixture(page);
  state.user = null;
  await page.goto("/?follow=1");
  await expect(page.getByRole("heading", { name: "登录后，看看你关注的小摊" })).toBeVisible();
  await expect(page.locator(".discovery-empty")).toHaveCount(0);
  state.empty = true;
  await page.goto("/");
  await expect(page.locator(".discovery-empty h3")).toHaveText("这里还没有收录摊位");
  expect(state.unexpected).toEqual([]);
});

for (const kind of ["stalls", "meals"] as const) {
  const noun = kind === "stalls" ? "摊位" : "餐点";
  const heading = (id: number, fresh = false) => `第${id}页煎饼${kind === "stalls" ? "摊" : ""}${fresh ? "已更新" : ""}`;
  async function openTwoPages(page: Page) {
    await page.goto(`/search?type=${kind === "stalls" ? "stalls" : "dishes"}`);
    await page.getByRole("button", { name: `加载更多${noun}`, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading(2), exact: true })).toBeVisible();
  }

  test(`${kind} refresh preserves the loaded window while pending and follows fresh cursors`, async ({ page }) => {
    const state = await fixture(page, kind);
    await page.clock.install();
    await openTwoPages(page);
    state.fresh = true;
    state.holdSecond = true;
    if (kind === "stalls") await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    else await page.clock.fastForward(61000);
    await expect.poll(() => state.pending).toBe(true);
    await expect(page.getByRole("heading", { name: heading(2), exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: heading(1, true), exact: true })).toHaveCount(0);
    state.release();
    await expect(page.getByRole("heading", { name: heading(2, true), exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: heading(1, true), exact: true })).toBeVisible();
    expect(state.requests.some(url => url.includes("cursor=new-second"))).toBe(true);
    await expect(page.getByRole("button", { name: `加载更多${noun}`, exact: true })).toHaveCount(0);
    expect(state.unexpected).toEqual([]);
  });

  test(`${kind} later-page refresh failure keeps prior rows and a successful empty page removes stale rows`, async ({ page }) => {
    const state = await fixture(page, kind);
    await openTwoPages(page);
    state.fresh = true;
    state.failSecond = true;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.locator(".discovery-stale")).toContainText("暂未同步");
    await expect(page.getByRole("heading", { name: heading(1), exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: heading(2), exact: true })).toBeVisible();
    state.failSecond = false;
    state.emptySecond = true;
    state.holdSecond = true;
    state.pending = false;
    await page.locator(".discovery-stale button").click();
    await expect.poll(() => state.pending).toBe(true);
    await expect(page.locator(".discovery-stale")).toContainText("暂未同步");
    await expect(page.locator(".discovery-stale button")).toBeDisabled();
    await expect(page.getByRole("heading", { name: heading(2), exact: true })).toBeVisible();
    state.release();
    await expect(page.getByRole("heading", { name: heading(1, true), exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: heading(2), exact: true })).toHaveCount(0);
    await expect(page.locator(".discovery-stale")).toHaveCount(0);
    expect(state.unexpected).toEqual([]);
  });

  test(`${kind} changing query clears the previous warning while the new query is pending`, async ({ page }) => {
    const state = await fixture(page, kind);
    await openTwoPages(page);
    state.failSecond = true;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.locator(".discovery-stale")).toContainText("暂未同步");
    let release!: () => void;
    let pending = false;
    const held = new Promise<void>(resolve => { release = resolve; });
    await page.route(`**/api/v1/${kind === "stalls" ? "stalls" : "products"}?*`, async route => {
      if (!new URL(route.request().url()).searchParams.has("category")) return route.fallback();
      pending = true;
      await held;
      await route.fulfill({ json: { results: [kind === "stalls" ? stall(9) : meal(9)], next: null } });
    });
    if (!(await page.locator(".search-categories").isVisible()))
      await page.locator(".search-filter-toggle").click();
    await page.locator(".search-categories button").filter({ hasText: "小吃" }).click();
    await expect.poll(() => pending).toBe(true);
    await expect(page.locator(".discovery-stale")).toHaveCount(0);
    release();
    await expect(page.getByRole("heading", { name: heading(9), exact: true })).toBeVisible();
    expect(state.unexpected).toEqual([]);
  });

  test(`${kind} loading more cannot clear a failed refresh of earlier pages`, async ({ page }) => {
    const state = await fixture(page, kind);
    state.thirdPage = true;
    await openTwoPages(page);
    state.failSecond = true;
    state.fresh = true;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.locator(".discovery-stale")).toContainText("暂未同步");
    state.failSecond = false;
    await page.getByRole("button", { name: `加载更多${noun}`, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading(3, true), exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: heading(1), exact: true })).toBeVisible();
    await expect(page.locator(".discovery-stale")).toContainText("暂未同步");
    await page.locator(".discovery-stale button").click();
    await expect(page.getByRole("heading", { name: heading(1, true), exact: true })).toBeVisible();
    await expect(page.locator(".discovery-stale")).toHaveCount(0);
    expect(state.unexpected).toEqual([]);
  });

  test(`${kind} changing account clears the prior warning while its new list is pending`, async ({ page }) => {
    const state = await fixture(page, kind);
    await openTwoPages(page);
    state.failSecond = true;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.locator(".discovery-stale")).toContainText("暂未同步");
    let release!: () => void;
    let pending = false;
    const held = new Promise<void>(resolve => { release = resolve; });
    await page.route(`**/api/v1/${kind === "stalls" ? "stalls" : "products"}?*`, async route => {
      pending = true;
      await held;
      await route.fulfill({ json: { results: [], next: null } }).catch(() => {});
    });
    state.user = { ...user, id: 992 };
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => pending).toBe(true);
    await expect(page.locator(".discovery-stale")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: heading(1), exact: true })).toHaveCount(0);
    release();
    expect(state.unexpected).toEqual([]);
  });

  test(`${kind} a filter change discards a delayed old window and resets pagination`, async ({ page }) => {
    const state = await fixture(page, kind);
    await openTwoPages(page);
    state.holdSecond = true;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => state.pending).toBe(true);
    if (!(await page.locator(".search-categories").isVisible()))
      await page.locator(".search-filter-toggle").click();
    await page.locator(".search-categories button").filter({ hasText: "小吃" }).click();
    await expect(page.getByRole("heading", { name: heading(9), exact: true })).toBeVisible();
    state.release();
    await expect(page.getByRole("heading", { name: heading(2), exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: `加载更多${noun}`, exact: true })).toHaveCount(0);
    expect(state.requests.filter(url => url.includes("category=")).every(url => !url.includes("cursor="))).toBe(true);
    expect(state.unexpected).toEqual([]);
  });
}

test("map retry preserves its warning until success and clears it for a new query", async ({ page }) => {
  const state = await fixture(page);
  const map = { fail: false, hold: false, pending: false, fresh: false, release: () => {} };
  await page.route("**/api/v1/stalls/map?*", async route => {
    const row = stall(new URL(route.request().url()).searchParams.has("category") ? 9 : 1);
    if (map.fresh) row.name += "已更新";
    if (map.hold) {
      map.pending = true;
      await new Promise<void>(resolve => { map.release = resolve; });
    }
    if (map.fail) return route.fulfill({ status: 503, json: { detail: "地图摊位暂未同步" } });
    return route.fulfill({ json: { results: [row], next: null, truncated: false } });
  });
  await page.goto("/map");
  const warning = page.locator(".map-results > .error-message");
  await expect(page.locator(".map-list-item")).toContainText("第1页煎饼摊");
  map.fail = true;
  await page.getByRole("button", { name: "刷新摊位", exact: true }).click();
  await expect(warning).toContainText("暂未同步");
  map.fail = false; map.hold = true; map.fresh = true;
  await warning.getByRole("button", { name: "刷新摊位", exact: true }).click();
  await expect.poll(() => map.pending).toBe(true);
  await expect(warning).toContainText("暂未同步");
  await expect(warning.getByRole("button")).toBeDisabled();
  await expect(page.locator(".map-list-item")).not.toContainText("已更新");
  map.release(); map.hold = false;
  await expect(warning).toHaveCount(0);
  await expect(page.locator(".map-list-item")).toContainText("第1页煎饼摊已更新");
  map.fail = true;
  await page.getByRole("button", { name: "刷新摊位", exact: true }).click();
  await expect(warning).toContainText("暂未同步");
  map.fail = false; map.hold = true; map.pending = false;
  await page.getByRole("combobox", { name: "地图品类" }).selectOption("小吃");
  await expect.poll(() => map.pending).toBe(true);
  await expect(warning).toHaveCount(0);
  map.release();
  await expect(page.locator(".map-list-item")).toContainText("第9页煎饼摊已更新");
  expect(state.unexpected).toEqual([]);
});
