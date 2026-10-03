import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

function product(
  id: number,
  name: string,
  price: number,
  extra: Record<string, any> = {},
) {
  return {
    id,
    name,
    price_cents: price,
    stock: 8,
    stock_version: 0,
    is_active: true,
    sale_paused: false,
    description: "现做的小吃，配图为示例",
    image: "/images/food-jianbing.jpg",
    taste_options: [],
    ...extra,
  };
}
function stall(
  id: number,
  products: any[],
  extra: Record<string, any> = {},
): any {
  return {
    id,
    name: id === 1 ? "南门煎饼" : "树下小食",
    description: "学校南门的热乎小摊",
    category: "小吃",
    image: "/images/food-jianbing.jpg",
    address: "南门第二棵树旁",
    area_id: 1,
    area_name: "南门",
    latitude: 31.2,
    longitude: 121.4,
    status: "open",
    session_status: "open",
    last_confirmed_at: new Date().toISOString(),
    closes_at: null,
    prep_minutes: id === 1 ? 8 : 15,
    transaction_enabled: true,
    can_order: true,
    accepting_orders: true,
    receiving_status: "recent",
    receiving_seen_at: new Date().toISOString(),
    contact_phone: "",
    qualification_note: "示例资料",
    merchant_name: "示例商家",
    rating: 0,
    review_count: 0,
    order_count: 0,
    distance_m: null,
    is_followed: false,
    products,
    reviews: [],
    arrival_note: "绿色棚顶",
    arrival_image: "",
    wechat_payment: {
      mode: "simulation",
      available: false,
      reason: "尚未开放",
      channels: [],
    },
    ...extra,
  };
}
async function fixture(page: Page, signedIn = false) {
  const state = {
    user: signedIn
      ? {
          id: 912,
          username: "quick_student",
          display_name: "小林",
          is_merchant: false,
          is_staff: false,
        }
      : null,
    stalls: [
      stall(1, [
        product(11, "原味煎饼", 1000),
        product(12, "双蛋煎饼", 1500),
        product(13, "暂停的芝士饼", 500, { sale_paused: true }),
        product(14, "售罄小食", 300, { stock: 0 }),
        product(15, "豪华煎饼", 1501),
      ]),
      stall(2, [product(21, "热豆花", 800), product(22, "手工丸子", 1001)], {
        can_order: false,
        transaction_enabled: false,
      }),
    ],
    recent: [] as any[],
    writes: [] as { path: string; data: any }[],
    unexpected: [] as string[],
  };
  await page.route("https://**/*", (r) => r.abort());
  await page.route("**/api/v1/**", async (r) => {
    const req = r.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", "");
    const send = (json: any, status = 200) => r.fulfill({ json, status });
    if (path === "/config")
      return send({
        brand: "烟火地图",
        demo_mode: true,
        user: state.user,
        amap_key: "",
        stale_minutes: 60,
        areas: [{ id: 1, name: "南门", latitude: 31.2, longitude: 121.4 }],
      });
    if (path === "/auth/me") return send(state.user);
    if (path === "/auth/csrf") return fulfillCsrf(r);
    if (path === "/events") return send({ ok: true });
    if (path === "/orders/active-summary")
      return send({
        user_id: state.user?.id,
        counts: { total: 0 },
        order: null,
      });
    if (path === "/orders/recent-completed" || path === "/orders")
      return send(state.recent);
    if (path === "/follows") return send([]);
    if (path === "/stalls")
      return send(
        state.stalls.filter(
          (s) =>
            !url.searchParams.get("status") ||
            s.status === url.searchParams.get("status"),
        ),
      );
    const detail = path.match(/^\/stalls\/(\d+)$/);
    if (detail)
      return send(state.stalls.find((s) => s.id === Number(detail[1])));
    if (req.method() !== "GET")
      state.writes.push({ path, data: req.postDataJSON() });
    state.unexpected.push(`${req.method()} ${path}`);
    return send({ detail: "测试未定义接口" }, 404);
  });
  return state;
}

test("budget filters only dishes, retains offline stalls, and never promotes paused or sold out food", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/");
  const section = page.getByRole("region", { name: "餐点灵感" });
  await expect(section.locator(".dish-card")).toHaveCount(4);
  await section.getByRole("button", { name: "10 元以内", exact: true }).click();
  await expect(page).toHaveURL(/meal_budget=1000/);
  await expect(section.locator(".dish-card")).toHaveCount(2);
  await expect(page.locator(".stall-card")).toHaveCount(2);
  await expect(
    page.locator(".stall-card").filter({ hasText: "树下小食" }),
  ).toContainText("到摊选购");
  await expect(section).not.toContainText("暂停的芝士饼");
  await expect(section).not.toContainText("售罄小食");
  await expect(section).not.toContainText("手工丸子");
  expect(state.unexpected).toEqual([]);
});

test("meal budget boundary, price sorting and browser return retain URL state", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/search?type=dishes&meal_budget=1500&meal_sort=price");
  const section = page.getByRole("region", { name: "餐点搜索结果" });
  await expect(section.locator(".dish-card h3")).toHaveText([
    "热豆花",
    "原味煎饼",
    "手工丸子",
    "双蛋煎饼",
  ]);
  const switches = page.locator(".result-switch");
  await switches.getByRole("button", { name: /^摊位/ }).click();
  await expect(page).toHaveURL(/meal_budget=1500.*meal_sort=price/);
  await expect(page.locator(".stall-card")).toHaveCount(2);
  await switches.getByRole("button", { name: /^餐点/ }).click();
  await expect(section.getByLabel("餐点排序")).toHaveValue("price");
  await expect(
    section.getByRole("button", { name: "15 元以内", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await section
    .getByRole("link", { name: "查看双蛋煎饼详情，南门煎饼" })
    .click();
  await expect(page).toHaveURL(/\/stalls\/1\/products\/12$/);
  await expect(
    page.getByRole("heading", { name: "双蛋煎饼", exact: true }).first(),
  ).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/meal_budget=1500.*meal_sort=price/);
  await expect(
    section.getByRole("button", { name: "15 元以内", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(section.getByLabel("餐点排序")).toHaveValue("price");
  await page.reload();
  await expect(section.locator(".dish-card h3")).toHaveText([
    "热豆花",
    "原味煎饼",
    "手工丸子",
    "双蛋煎饼",
  ]);
});

test("an empty budget preserves controls and can be cleared without changing stall discovery", async ({
  page,
}) => {
  const state = await fixture(page);
  for (const s of state.stalls)
    for (const p of s.products) p.price_cents += 2000;
  await page.goto("/?meal_budget=1000");
  await expect(
    page.getByRole("heading", { name: "当前预算内暂时没有可售餐点" }),
  ).toBeVisible();
  await expect(page.locator(".stall-card")).toHaveCount(2);
  await page.getByRole("button", { name: "清除餐费预算" }).click();
  await expect(
    page.getByRole("region", { name: "餐点灵感" }).locator(".dish-card"),
  ).toHaveCount(4);
  await expect(page).not.toHaveURL(/meal_budget/);
});

test("invalid URL filters fall back to unrestricted dishes and starting a search retains valid budget", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/?meal_budget=-5&meal_sort=invalid");
  const region = page.getByRole("region", { name: "餐点灵感" });
  await expect(
    region.getByRole("button", { name: "不限", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await region.getByRole("button", { name: "20 元以内", exact: true }).click();
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page).toHaveURL(/\/search\?.*meal_budget=2000/);
});

test("paused product remains readable but cannot be added in detail, menu or saved cart", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.addInitScript(
    (p) =>
      localStorage.setItem(
        "yanhuo-cart-v2:guest",
        JSON.stringify({ "1": [{ product: p, quantity: 1 }] }),
      ),
    state.stalls[0].products[2],
  );
  await page.goto("/stalls/1/products/13");
  await expect(
    page.getByRole("button", { name: "暂停供应", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".dish-unavailable")).toContainText(
    "商家已暂停供应",
  );
  await page.goto("/stalls/1");
  const row = page.locator(".product-row").filter({
    has: page.getByRole("link", {
      name: "查看暂停的芝士饼详情",
      exact: true,
    }),
  });
  await expect(row).toContainText("暂停供应");
  await expect(
    row.getByRole("button", { name: "添加暂停的芝士饼" }),
  ).toHaveCount(0);
  await page.goto("/cart");
  await expect(
    page.getByText("商家已暂停供应这道餐点", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /去结算/ })).toHaveCount(0);
  expect(state.writes).toEqual([]);
});

test("reorder skips paused food while retaining available items and their existing choices", async ({
  page,
}) => {
  const state = await fixture(page, true);
  state.recent = [
    {
      id: "previous-order",
      number: "PREVIOUS",
      stall_id: 1,
      stall_name: "南门煎饼",
      stall_image: "/images/food-jianbing.jpg",
      status: "completed",
      total_cents: 1500,
      created_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      items: [11, 13].map((id) => {
        const p = state.stalls[0].products.find((x: any) => x.id === id);
        return {
          product_id: id,
          name: p.name,
          image: p.image,
          unit_price_cents: p.price_cents,
          quantity: 1,
          portions: [{ options: {}, note: "不要葱" }],
        };
      }),
    },
  ];
  await page.goto("/");
  await page.getByRole("button", { name: /再来一单/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("商家已暂停供应这道餐点，本次跳过");
  await expect(dialog).toContainText("原味煎饼");
  await expect(
    dialog.getByRole("button", { name: /加入.*餐袋/ }),
  ).toBeEnabled();
});

test("unknown and stale receiving activity only explain risk and never disable ordering", async ({
  page,
}) => {
  const state = await fixture(page);
  state.stalls[0].receiving_seen_at = null;
  state.stalls[0].receiving_status = "unknown";
  await page.goto("/stalls/1/products/11");
  await expect(
    page.getByText("暂无法确认接单页面状态", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeEnabled();
  state.stalls[0].receiving_seen_at = new Date(
    Date.now() - 100_000,
  ).toISOString();
  state.stalls[0].receiving_status = "stale";
  await page.reload();
  await expect(
    page.getByText("商家接单页面近期未更新，可能回复较慢", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeEnabled();
  expect(state.writes).toEqual([]);
});

test("receiving warning ages while viewing without changing business or position state", async ({
  page,
}) => {
  await page.clock.install();
  const state = await fixture(page);
  await page.goto("/stalls/1/products/11");
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeEnabled();
  await expect(page.locator(".receiving-notice")).toHaveCount(0);
  await page.clock.runFor(105_000);
  await expect(page.locator(".receiving-notice")).toContainText("近期未更新");
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeEnabled();
  expect(state.stalls[0].status).toBe("open");
  expect(state.writes).toEqual([]);
});

test("receiving age follows the server despite a wrong phone clock", async ({
  page,
}) => {
  await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
  const state = await fixture(page);
  Object.assign(state.stalls[0], {
    receiving_status: "stale",
    receiving_seen_at: "2026-09-30T00:00:00Z",
    receiving_age_seconds: 100,
  });
  await page.goto("/stalls/1/products/11");
  await expect(page.locator(".receiving-notice")).toContainText("近期未更新");
  Object.assign(state.stalls[0], {
    receiving_status: "recent",
    receiving_seen_at: "2025-01-01T00:00:00Z",
    receiving_age_seconds: 75,
  });
  await page.reload();
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeEnabled();
  await expect(page.locator(".receiving-notice")).toHaveCount(0);
  await page.clock.runFor(30_000);
  await expect(page.locator(".receiving-notice")).toContainText("近期未更新");
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeEnabled();
  expect(state.writes).toEqual([]);
});

test("budget and preparation comparison fits four widths with 44px touch controls", async ({
  page,
}, info) => {
  await fixture(page);
  await page.goto("/?meal_budget=1500&meal_sort=price");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator(".stall-card").first()).toContainText(
      "通常备餐约 8 分钟",
    );
    await expect(page.locator(".stall-card").last()).toContainText(
      "通常备餐约 15 分钟",
    );
    await assertNoHorizontalOverflow(page);
    const sizes = await page
      .locator(".meal-budget button, .meal-budget select")
      .evaluateAll((elements) =>
        elements.map((el) => el.getBoundingClientRect().height),
      );
    expect(sizes.every((size) => size >= 44)).toBe(true);
    if ([390, 1440].includes(width))
      await page.screenshot({
        path: info.outputPath(`budget-${width}.png`),
        fullPage: true,
      });
  }
});
