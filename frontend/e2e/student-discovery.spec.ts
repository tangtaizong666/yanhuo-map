import { publicResponse, mealResponse } from "./public-contracts";
import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

const student = (id = 701) => ({
  id,
  username: `student_${id}`,
  display_name: "小林同学",
  is_merchant: false,
  is_staff: false,
});
function stall(id: number, extra: Record<string, any> = {}): any {
  return {
    id,
    name: ["", "南门煎饼", "树下豆花", "晚课烤串", "收摊小店", "待确认小店"][
      id
    ],
    description: "下课后来一份热乎的好味道",
    category: "小吃",
    image: "/images/food-jianbing.jpg",
    address: "校园南门外第二棵树旁",
    arrival_note: "绿色棚顶，靠近南门便利店",
    arrival_image: "",
    latitude: 31.2,
    longitude: 121.4,
    area_id: 1,
    area_name: "南门生活区",
    status: "open",
    last_confirmed_at: new Date().toISOString(),
    closes_at: new Date(Date.now() + 3600_000).toISOString(),
    prep_minutes: 8,
    accepting_orders: true,
    transaction_enabled: true,
    can_order: true,
    qualification_note: "示例资料",
    merchant_name: "示例商家",
    contact_phone: "13800138000",
    rating: 0,
    review_count: 0,
    order_count: 0,
    distance_m: null,
    is_followed: false,
    products: [
      {
        id: id * 10,
        name: `招牌餐点${id}`,
        description: "商家提供的餐点介绍",
        image: "/images/food-jianbing.jpg",
        price_cents: 1000,
        stock: 10,
      },
    ],
    reviews: [],
    wechat_payment: {
      mode: "simulation",
      available: false,
      reason: "尚未开启",
      channels: [],
    },
    delivery: { enabled: false, available: false, points: [] },
    ...extra,
  };
}
function previousOrder(id: number): any {
  return {
    id: `recent-${id}`,
    number: `RECENT-${id}`,
    stall_id: 1,
    stall_name: `南门煎饼 · 第${id}次`,
    stall_image: "/images/food-jianbing.jpg",
    status: "completed",
    mode: "simulation",
    fulfillment_type: "pickup",
    payment_status: "paid",
    payment_method: "offline",
    total_cents: 800,
    created_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    pickup_address: "南门",
    items: [
      {
        product_id: 10,
        name: "招牌餐点1",
        image: "/images/food-jianbing.jpg",
        unit_price_cents: 800,
        quantity: 1,
      },
    ],
  };
}
async function fixture(page: Page, signedIn = true) {
  const state = {
    user: signedIn ? student() : (null as any),
    loginId: 701,
    stalls: [
      stall(1),
      stall(2, { transaction_enabled: false, can_order: false }),
      stall(3, {
        accepting_orders: false,
        can_order: false,
        order_unavailable_reason: "线上接单暂停，仍可到摊选购。",
      }),
      stall(4, { status: "closed", can_order: false }),
      stall(5, { status: "stale", can_order: false }),
    ],
    recent: [] as any[],
    failList: false,
    failRecent: false,
    failFollow: false,
    amapKey: "",
    csrfReads: 0,
    csrfGate: null as Promise<void> | null,
    followed: new Map<number, Set<number>>([[701, new Set([2, 4])]]),
    queries: [] as URLSearchParams[],
    followWrites: [] as { userId: number; stallId: number }[],
    otherWrites: [] as string[],
    unexpected: [] as string[],
    events: [] as any[],
  };
  const decorate = (s: any) => ({
    ...s,
    is_followed: !!state.user && !!state.followed.get(state.user.id)?.has(s.id),
  });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", "");
    const send = (json: any, status = 200) =>
      route.fulfill({
        json: status >= 400 ? json : publicResponse(path, json),
        status,
      });
    if (path === "/config")
      return send({
        user: state.user,
        brand: "烟火地图",
        demo_mode: true,
        services_simulation_enabled: true,
        stale_minutes: 60,
        public_base_url: "",
        amap_key: state.amapKey,
        areas: [
          { id: 1, name: "南门生活区", latitude: 31.2, longitude: 121.4 },
        ],
      });
    if (path === "/auth/me") return send(state.user);
    if (path === "/auth/csrf") {
      state.csrfReads++;
      if (state.csrfGate) await state.csrfGate;
      return fulfillCsrf(route);
    }
    if (path === "/auth/login") {
      state.user = student(state.loginId);
      return send(state.user);
    }
    if (path === "/auth/logout") {
      state.user = null;
      return send({ detail: "已退出" });
    }
    if (path === "/events") {
      state.events.push(req.postDataJSON());
      return send({ ok: true });
    }
    if (path === "/orders/active-summary")
      return send({
        user_id: state.user?.id,
        counts: { total: 0 },
        order: null,
      });
    if (path === "/orders/recent-completed")
      return state.failRecent ? route.abort("failed") : send(state.recent);
    if (path === "/orders" && req.method() === "GET") return send(state.recent);
    if (path === "/follows")
      return send(
        state.stalls
          .filter((s) => state.followed.get(state.user?.id)?.has(s.id))
          .map(decorate),
      );
    const follow = path.match(/^\/stalls\/(\d+)\/follow$/);
    if (follow) {
      state.followWrites.push({
        userId: state.user?.id,
        stallId: Number(follow[1]),
      });
      if (state.failFollow) return route.abort("failed");
      const ids = state.followed.get(state.user.id) || new Set<number>();
      if (req.method() === "POST") ids.add(Number(follow[1]));
      else ids.delete(Number(follow[1]));
      state.followed.set(state.user.id, ids);
      return send(
        decorate(state.stalls.find((s) => s.id === Number(follow[1]))),
      );
    }
    if (path === "/products")
      return send(mealResponse(state.stalls.map(decorate), url.searchParams));
    if (path === "/stalls" || path === "/stalls/map") {
      state.queries.push(url.searchParams);
      if (state.failList) return route.abort("failed");
      let rows = state.stalls;
      const status = url.searchParams.get("status");
      if (status)
        rows = rows.filter((s) =>
          status === "orderable" ? s.can_order : s.status === status,
        );
      return send(rows.map(decorate));
    }
    const detail = path.match(/^\/stalls\/(\d+)$/);
    if (detail)
      return send(
        decorate(state.stalls.find((s) => s.id === Number(detail[1]))),
      );
    if (req.method() !== "GET")
      state.otherWrites.push(`${req.method()} ${path}`);
    state.unexpected.push(path);
    return send({ detail: "Unexpected fixture request" }, 500);
  });
  return state;
}
async function login(page: Page) {
  await page
    .getByRole("textbox", { name: "账号", exact: true })
    .fill("fixture_student");
  await page.getByLabel("密码", { exact: true }).fill("fixture-password");
  await page
    .getByRole("button", { name: "登录，继续探索", exact: true })
    .click();
  await expect(page).not.toHaveURL(/\/login/);
}

test("display-only stall keeps visit information and every meal reachable without checkout prompts", async ({ page }, info) => {
  const state = await fixture(page);
  const offline = state.stalls[1];
  offline.accepting_orders = false;
  offline.products = ["available", "sold_out", "paused"].map((supply, index) => ({
    id: 20 + index, name: `找摊餐点${index + 1}`, description: "商家填写的今日供应",
    image: "/images/food-jianbing.jpg", price_cents: 1000, stock: 0,
    display_only: true, display_availability: supply, is_active: true,
  }));
  await page.addInitScript((product) => {
    localStorage.setItem("yanhuo-cart-v2:user:701", JSON.stringify({
      2: [{ product, quantity: 1, portions: [{ options: {}, note: "少辣" }] }],
    }));
  }, offline.products[0]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/stalls/2");
  await expect(page.getByRole("heading", { name: "树下豆花", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("display-only-desktop.png"), fullPage: true });
  await expect(page.locator(".pickup-aside")).toHaveCount(0);
  await expect(page.locator(".mobile-cart")).toHaveCount(0);
  await expect(page.locator(".stall-overview")).toContainText("线下到访");
  await expect(page.locator(".stall-overview")).not.toContainText("线上接单暂停");
  await expect(page.getByRole("region", { name: "到摊指引" })).not.toContainText("暂停线上接单");
  await expect(page.locator(".product-row").first()).toContainText("今天有，到摊选购");
  await expect(page.getByText("可选餐，提交时核对余量", { exact: true })).toHaveCount(0);
  await expect(page.locator(".qty-control")).toHaveCount(0);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await assertNoHorizontalOverflow(page);
    if (width === 390 || width === 1440)
      await page.screenshot({ path: info.outputPath(`display-only-${width}.png`), fullPage: true });
  }
  for (const item of offline.products) {
    await page.getByRole("link", { name: `查看${item.name}详情`, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/stalls/2/products/${item.id}$`));
    await expect(page.getByRole("heading", { name: item.name, exact: true })).toBeVisible();
    await page.goBack();
    await expect(page.locator(".product-row")).toHaveCount(3);
  }
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("yanhuo-cart-v2:user:701") || "{}")[2][0].quantity)).toBe(1);
  expect(state.otherWrites).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test("temporarily paused transaction stall keeps pickup and its existing bag", async ({ page }) => {
  const state = await fixture(page);
  const paused = state.stalls[2];
  await page.addInitScript((product) => {
    localStorage.setItem("yanhuo-cart-v2:user:701", JSON.stringify({ 3: [{ product, quantity: 1 }] }));
  }, paused.products[0]);
  await page.goto("/stalls/3");
  await expect(page.locator(".pickup-aside")).toBeVisible();
  await expect(page.locator(".cart-preview")).toContainText("招牌餐点3");
  await expect(page.locator(".stall-overview")).toContainText("线上接单暂停");
  await expect(page.getByRole("link", { name: "去结算", exact: true })).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("open-first discovery includes offline stalls, offers all/follows and fits four widths", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto("/");
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(3);
  expect(state.queries.at(-1)?.get("sort")).toBe("freshness");
  expect(state.queries.at(-1)?.has("lat")).toBe(false);
  await expect(
    page.getByRole("button", { name: "正在出摊", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".food-grid")).toContainText("到摊选购");
  await expect(page.locator(".food-grid")).toContainText("线上接单暂停");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width < 768 ? 844 : 1000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await assertNoHorizontalOverflow(page);
    if (width === 390) {
      const first = await page
        .locator(".food-grid .stall-title")
        .first()
        .boundingBox();
      expect(first!.y + first!.height).toBeLessThan(774);
    }
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`student-discovery-${width}.png`),
        fullPage: true,
      });
  }
  await page.getByRole("button", { name: "全部摊位", exact: true }).click();
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(5);
  await expect(page.locator(".meal-inspiration .dish-card")).toHaveCount(3);
  await expect(page.locator(".meal-inspiration")).not.toContainText(
    "招牌餐点4",
  );
  await expect(page.locator(".meal-inspiration")).not.toContainText(
    "招牌餐点5",
  );
  await page.getByRole("button", { name: "我的关注", exact: true }).click();
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(2);
  await expect(page.locator(".food-grid")).toContainText("收摊小店");
  expect(state.unexpected).toEqual([]);
});

test("focus refresh retains clearly stale data on failure and visibility refresh removes closed stalls", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/");
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(3);
  state.failList = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    page.getByText(
      "暂未同步最新状态，下面是上次加载的摊位。出发前请刷新确认。",
    ),
  ).toBeVisible();
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(3);
  state.failList = false;
  state.stalls[0].status = "closed";
  state.stalls[0].can_order = false;
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(2);
  await expect(page.locator(".discovery-stale")).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("guest follow completes after login once and cannot migrate to a later account", async ({
  page,
}) => {
  const state = await fixture(page, false);
  await page.goto("/");
  await page.getByRole("button", { name: "关注南门煎饼", exact: true }).click();
  await expect(page).toHaveURL(/followIntent=/);
  const originalLogin = page.url();
  await login(page);
  await expect(
    page.getByRole("button", { name: "取消关注南门煎饼", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(state.followWrites).toEqual([{ userId: 701, stallId: 1 }]);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("yanhuo-pending-follow-v1"),
    ),
  ).toBeNull();
  state.user = null;
  state.loginId = 702;
  await page.goto(originalLogin);
  await login(page);
  await expect(
    page.getByRole("button", { name: "关注南门煎饼", exact: true }),
  ).toBeVisible();
  expect(state.followWrites).toHaveLength(1);
  expect(state.unexpected).toEqual([]);
});

test("expired guest follow asks for a fresh action without a write", async ({
  page,
}) => {
  const state = await fixture(page, false);
  await page.goto("/");
  await page.getByRole("button", { name: "关注南门煎饼", exact: true }).click();
  await expect(page).toHaveURL(/followIntent=/);
  await page.evaluate(() => {
    const key = "yanhuo-pending-follow-v1";
    const saved = JSON.parse(sessionStorage.getItem(key)!);
    saved.createdAt = Date.now() - 601_000;
    sessionStorage.setItem(key, JSON.stringify(saved));
  });
  await login(page);
  await expect(
    page.getByText("之前的关注操作已过期，请回到摊位重新点关注。"),
  ).toBeVisible();
  expect(state.followWrites).toEqual([]);
});

test("failed follow after login is explicit and is not replayed automatically", async ({
  page,
}) => {
  const state = await fixture(page, false);
  state.failFollow = true;
  await page.goto("/");
  await page.getByRole("button", { name: "关注南门煎饼", exact: true }).click();
  await expect(page).toHaveURL(/followIntent=/);
  await login(page);
  await expect(page.getByText(/关注未确认：/)).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "关注南门煎饼", exact: true }),
  ).toBeVisible();
  expect(state.followWrites).toHaveLength(1);
});

test("pending guest intent aborts before a follow write if identity changes during CSRF", async ({
  page,
  context,
}) => {
  const state = await fixture(page);
  await page.goto("/");
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(3);
  await expect.poll(() => state.events.length).toBeGreaterThan(0);
  await context.clearCookies();
  let release!: () => void;
  state.csrfGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const previousReads = state.csrfReads;
  await page.evaluate(async () => {
    const modulePath = "/src/lib/discovery.ts";
    const { completePendingFollow } = await import(modulePath);
    sessionStorage.setItem(
      "yanhuo-pending-follow-v1",
      JSON.stringify({
        stallId: 1,
        returnTo: "/",
        token: "identity-fixture",
        createdAt: Date.now(),
      }),
    );
    (window as any).pendingFollowResult = completePendingFollow(
      701,
      "/",
      "identity-fixture",
    );
  });
  await expect.poll(() => state.csrfReads).toBe(previousReads + 1);
  state.user = student(702);
  await page.evaluate(async () => {
    const modulePath = "/src/stores/session.ts";
    const { useSession } = await import(modulePath);
    await useSession().refreshUser();
  });
  release();
  await page.evaluate(() => (window as any).pendingFollowResult);
  expect(state.followWrites).toEqual([]);
  expect(
    await page.evaluate(() =>
      sessionStorage.getItem("yanhuo-pending-follow-v1"),
    ),
  ).toBeNull();
  expect(state.unexpected).toEqual([]);
});

test("location denial keeps manual browsing and successful location selects distance sorting", async ({
  page,
}) => {
  const state = await fixture(page, false);
  state.amapKey = "fixture-map-key";
  await page.addInitScript(() => {
    (window as any).geoAllowed = false;
    (window as any).AMap = {
      Map: class {
        addControl() {}
        add() {}
        remove() {}
        panTo() {}
        destroy() {}
        setZoomAndCenter() {}
      },
      Scale: class {},
      Marker: class {
        on() {}
      },
      Geolocation: class {
        getCurrentPosition(callback: any) {
          callback((window as any).geoAllowed ? "complete" : "error", {
            position: { lat: 31.2, lng: 121.4 },
          });
        }
      },
    };
  });
  await page.goto("/map");
  await expect(page.locator(".map-list-item")).toHaveCount(3);
  await page
    .getByRole("button", { name: "定位我的位置", exact: true })
    .first()
    .click();
  await expect(
    page.getByText("未能获取位置，可在上方手动选择校园，继续浏览附近摊位。"),
  ).toBeVisible();
  await expect(page.getByRole("combobox", { name: "选择校园" })).toBeEnabled();
  expect(state.queries.at(-1)?.has("lat")).toBe(false);
  await page.evaluate(() => {
    (window as any).geoAllowed = true;
  });
  await page
    .getByRole("button", { name: "定位我的位置", exact: true })
    .first()
    .click();
  await expect.poll(() => state.queries.at(-1)?.get("sort")).toBe("distance");
  expect(state.queries.at(-1)?.get("lat")).toBe("31.2");
  await expect(page.getByRole("combobox", { name: "摊位排序" })).toHaveValue(
    "distance",
  );
  expect(state.unexpected).toEqual([]);
});

test("recent completed orders stay limited and reorder reviews current price without submitting", async ({
  page,
}) => {
  const state = await fixture(page);
  state.recent = [1, 2, 3, 4].map(previousOrder);
  await page.goto("/");
  await expect(page.locator(".recent-order-card")).toHaveCount(3);
  await expect(page.locator(".recent-meals")).toContainText("模拟订单");
  await page
    .getByRole("button", { name: "再来一单，南门煎饼 · 第1次", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "再来一单" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("现价 ¥10");
  await expect(dialog).toContainText("上次 ¥8");
  await dialog.getByRole("button", { name: /确认加入餐袋/ }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.otherWrites).toEqual([]);
  expect(
    state.events.some(
      (event) =>
        event.type === "reorder" && event.metadata.source === "recent_order",
    ),
  ).toBe(true);
  expect(state.unexpected).toEqual([]);
});

test("failed recent history is recoverable without hiding open stalls", async ({
  page,
}) => {
  const state = await fixture(page);
  state.failRecent = true;
  await page.goto("/");
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(3);
  await expect(page.locator(".recent-meals")).toContainText(
    "最近吃过的订单暂未同步",
  );
  state.failRecent = false;
  state.recent = [previousOrder(1)];
  await page
    .locator(".recent-meals")
    .getByRole("button", { name: "重新加载" })
    .click();
  await expect(page.locator(".recent-order-card")).toHaveCount(1);
});

test("stall and fallback map expose real arrival notes, dual status and four-width actions", async ({
  page,
}, info) => {
  const state = await fixture(page, false);
  await page.goto("/stalls/3");
  await expect(page.getByRole("region", { name: "到摊指引" })).toContainText(
    "绿色棚顶，靠近南门便利店",
  );
  await expect(page.getByRole("region", { name: "到摊指引" })).toContainText(
    "暂停线上接单",
  );
  await expect(
    page.getByRole("region", { name: "到摊指引" }).locator(".visit-photo"),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "查看招牌餐点3照片与详情", exact: true }),
  ).toHaveAttribute("href", "/stalls/3/products/30");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width < 768 ? 844 : 1000 });
    await assertNoHorizontalOverflow(page);
  }
  await page.goto("/map");
  await expect(
    page.getByRole("heading", { name: "地图服务暂未开放" }),
  ).toBeVisible();
  await page
    .locator(".map-list-item")
    .filter({ hasText: "晚课烤串" })
    .getByRole("button", { name: "在地图中查看" })
    .click();
  await expect(page.locator(".selected-stall")).toContainText(
    "绿色棚顶，靠近南门便利店",
  );
  await expect(page.locator(".selected-stall")).toContainText("暂停线上接单");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width < 768 ? 844 : 1000 });
    await assertNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .locator(".selected-stall")
    .getByRole("button", { name: /没找到摊位/ })
    .click();
  await expect(page.getByRole("dialog")).toContainText("无需登录");
  await assertNoHorizontalOverflow(page);
  await page.screenshot({
    path: info.outputPath("student-arrival-report-390.png"),
    fullPage: true,
  });
  expect(state.unexpected).toEqual([]);
});
