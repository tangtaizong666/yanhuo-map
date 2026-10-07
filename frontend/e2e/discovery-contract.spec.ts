import { test, expect, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

function product(id: number, name = `餐点${id}`) {
  return {
    id,
    name,
    description: "商家填写的餐点介绍",
    category: "小吃",
    image: name.includes('柠檬') ? '/images/food-lemon-tea.jpg' : '/images/food-jianbing.jpg',
    price_cents: 800,
    availability: "available",
    max_order_quantity: 10,
    sale_paused: false,
    is_active: true,
    taste_options: [],
  };
}
function stall(id: number) {
  return {
    id,
    name: `真实小摊${id}`,
    description: "当天现做",
    category: "小吃",
    image: "/images/food-jianbing.jpg",
    address: "校园东门",
    latitude: 31,
    longitude: 121,
    area_id: 1,
    area_name: "校园",
    status: "open",
    session_status: "open",
    last_confirmed_at: new Date().toISOString(),
    prep_minutes: 10,
    transaction_enabled: true,
    can_order: true,
    rating: null,
    review_count: 0,
    distance_m: null,
    is_followed: false,
    products: [product(id * 10), product(id * 10 + 1)],
    accepting_orders: true,
    order_unavailable_reason: "",
    usual_hours: "11:00—14:00",
    receiving_status: "recent",
    receiving_valid_for_seconds: 30,
  };
}
async function fixture(page: Page) {
  const requested: string[] = [];
  const user = {
    id: 1,
    username: "student",
    display_name: "同学",
    is_merchant: false,
    is_staff: false,
  };
  const first = stall(1),
    second = stall(2);
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname.replace("/api/v1", "");
    requested.push(path);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config")
      return route.fulfill({
        json: {
          demo_mode: true,
          brand: "烟火地图",
          amap_key: "",
          amap_proxy: "",
          stale_minutes: 60,
          areas: [
            { id: 1, name: "校园", subtitle: "", latitude: 31, longitude: 121 },
          ],
          user,
        },
      });
    if (path === "/auth/me") return route.fulfill({ json: user });
    if (path === "/stalls")
      return route.fulfill({
        json: url.searchParams.has("cursor")
          ? { results: [second], next: null }
          : { results: [first], next: "page-two" },
      });
    if (path === "/stalls/map") {
      const { products, ...point } = first;
      return route.fulfill({
        json: { results: [point], truncated: true, limit: 200 },
      });
    }
    if (path === "/products") {
      const budget = Number(url.searchParams.get("budget") || 0);
      return route.fulfill({
        json: {
          results:
            budget && budget < 1200
              ? []
              : [
                  {
                    product: {
                      ...product(99, "菜单深处的柠檬茶"),
                      price_cents: 1200,
                    },
                    stall: first,
                  },
                ],
          next: null,
        },
      });
    }
    const detail = path.match(/^\/stalls\/(\d+)$/);
    if (detail)
      return route.fulfill({
        json: {
          ...first,
          id: Number(detail[1]),
          products: [...first.products, product(99, "菜单深处的柠檬茶")],
          reviews: [],
          contact_phone: "",
          closes_at: null,
          merchant_name: "小摊商家",
          qualification_note: "已核验",
          order_count: 0,
          arrival_note: "东门蓝色招牌",
          arrival_image: "",
          wechat_payment: {
            available: false,
            mode: "live",
            channels: [],
            reason: "未开通",
          },
          delivery: { available: false, reason: "暂不配送", points: [] },
        },
      });
    if (path === "/orders/active-summary")
      return route.fulfill({
        json: {
          orders: [],
          count: 0,
          status_counts: {},
          synced_at: new Date().toISOString(),
        },
      });
    if (path === "/orders/recent-completed") return route.fulfill({ json: [] });
    if (path === "/events") return route.fulfill({ json: { id: 1 } });
    if (path.endsWith("/follow"))
      return route.fulfill({
        json: { id: 1, is_followed: route.request().method() === "POST" },
      });
    return route.fulfill({
      status: 404,
      json: { detail: `测试未定义接口 ${path}` },
    });
  });
  return { requested };
}

async function selectedMapFixture(page: Page) {
  await fixture(page);
  const state = {
    detail: {
      ...stall(1), transaction_enabled: false, can_order: false, accepting_orders: false,
      arrival_note: "东门蓝色招牌", arrival_image: "/images/food-jianbing.jpg",
      contact_phone: "13900001111", closes_at: "2026-10-08T12:00:00Z",
      business_session_id: 1, reviews: [],
    },
    beforeDetail: null as null | (() => Promise<void>),
    detailStatus: 200,
    completedDetails: 0,
  };
  await page.route("**/api/v1/stalls/map?**", route => {
    // Mirror the real lightweight contract: arrival guidance, phone and closing
    // time are deliberately absent, so a summary cannot refresh a detail card.
    const fields = ["id", "name", "category", "image", "address", "latitude", "longitude",
      "area_id", "area_name", "status", "last_confirmed_at", "prep_minutes",
      "transaction_enabled", "can_order", "is_followed", "accepting_orders", "order_unavailable_reason"];
    const point = Object.fromEntries(fields.map(key => [key, state.detail[key as keyof typeof state.detail]]));
    const q = new URL(route.request().url()).searchParams.get("q") || "";
    return route.fulfill({ json: { results: state.detail.name.includes(q) ? [point] : [], truncated: false, limit: 200 } });
  });
  await page.route("**/api/v1/stalls/1", async route => {
    const snapshot = structuredClone(state.detail);
    await state.beforeDetail?.();
    await route.fulfill({ status: state.detailStatus, json: state.detailStatus === 200 ? snapshot : { detail: "暂时无法核对认摊信息" } });
    state.completedDetails++;
  });
  return state;
}

test("bounded discovery pages and independent meal search keep every meal reachable", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "真实小摊1", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "加载更多摊位", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "真实小摊2", exact: true }),
  ).toBeVisible();
  await page.goto("/search?q=柠檬&type=dishes");
  await page
    .getByRole("link", { name: "查看菜单深处的柠檬茶详情，真实小摊1" })
    .click();
  await expect(page).toHaveURL("/stalls/1/products/99");
  await expect(
    page.getByRole("heading", { name: "菜单深处的柠檬茶", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/线上剩余 \d+ 份/)).toHaveCount(0);
});

test("dish budget is sent to the independent search and retained in the URL", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/search?type=dishes&meal_budget=1000&meal_sort=price");
  await expect(
    page.getByRole("heading", { name: "还没有找到这道餐点" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "清除餐费预算", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "查看菜单深处的柠檬茶详情，真实小摊1" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/meal_sort=price/);
});

test("lightweight map explicitly reports truncation and loads details only after selection", async ({
  page,
}) => {
  const { requested } = await fixture(page);
  await page.goto("/map");
  await expect(
    page.getByText(
      "当前范围摊位较多，仅展示前 200 个。请放大地图或选择校园区域缩小范围。",
    ),
  ).toBeVisible();
  expect(requested).not.toContain("/stalls/1");
  await page.getByRole("button", { name: "在地图中查看", exact: true }).click();
  await expect(page.getByText("东门蓝色招牌")).toBeVisible();
  expect(requested).toContain("/stalls/1");
});

test("selected map refresh replaces the entire arrival guidance after relocation", async ({ page }, info) => {
  const state = await selectedMapFixture(page);
  await page.goto("/map");
  await page.getByRole("button", { name: "在地图中查看", exact: true }).click();
  await expect(page.getByText("东门蓝色招牌", { exact: true })).toBeVisible();
  const selected = page.locator(".selected-stall");
  await expect(selected.locator(".selected-info p")).toHaveText("线下到访");
  Object.assign(state.detail, {
    address: "校园西门", latitude: 31.1, longitude: 121.1,
    arrival_note: "西门红色棚子", arrival_image: "/images/food-lemon-tea.jpg",
    contact_phone: "13900002222", closes_at: "2026-10-08T13:00:00Z", business_session_id: 2,
  });
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve });
  state.beforeDetail = () => gate;
  state.detailStatus = 503;
  try {
    await page.getByRole("button", { name: "刷新摊位", exact: true }).click();
    await expect(selected.getByRole("status")).toHaveText("正在核对最新出摊信息…");
    await expect(selected.locator(".stall-visit")).toHaveCount(0);
    await expect(selected.getByRole("link", { name: "查看路线", exact: true })).toHaveCount(0);
    release();
    await expect(selected.getByRole("alert")).toContainText("暂未同步最新到摊指引");
    await expect(selected.locator(".stall-visit")).toHaveCount(0);
    await expect(selected.getByRole("link", { name: "查看路线", exact: true })).toHaveCount(0);
    state.beforeDetail = null;
    state.detailStatus = 200;
    await selected.getByRole("button", { name: "重新核对", exact: true }).click();
    await expect(selected.getByText("西门红色棚子", { exact: true })).toBeVisible();
    await expect(selected.getByText("东门蓝色招牌", { exact: true })).toHaveCount(0);
    await expect(selected.locator(".visit-address")).toHaveText("校园西门");
    await expect(selected.locator(".visit-photo img")).toHaveAttribute("src", state.detail.arrival_image);
    await expect(selected.getByRole("link", { name: "联系商家", exact: true })).toHaveAttribute("href", "tel:13900002222");
    await expect(selected.getByRole("link", { name: "查看路线", exact: true })).toHaveAttribute("href", /121\.1,31\.1/);
    const closing = await page.evaluate(value => new Date(value).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }), state.detail.closes_at);
    await expect(selected.locator(".visit-facts")).toContainText(`预计 ${closing} 收摊`);
  } finally {
    release();
    await page.screenshot({ path: info.outputPath("map-relocated-arrival.png"), fullPage: true });
  }
});

for (const dismiss of ["filter", "close"] as const) {
  test(`late map detail cannot restore a selection after ${dismiss}`, async ({ page }, info) => {
    const state = await selectedMapFixture(page);
    await page.goto("/map");
    await page.getByRole("button", { name: "在地图中查看", exact: true }).click();
    await expect(page.getByText("东门蓝色招牌", { exact: true })).toBeVisible();
    let release!: () => void, started!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve });
    const reading = new Promise<void>(resolve => { started = resolve });
    state.beforeDetail = async () => { started(); await gate };
    await page.getByRole("button", { name: "已选中", exact: true }).click();
    await reading;
    if (dismiss === "filter") {
      await page.getByRole("textbox", { name: "地图搜索" }).fill("没有这个摊位");
      await expect(page.getByText("换个条件，找找其他好味道。", { exact: true })).toBeVisible();
    } else {
      await page.getByRole("button", { name: "关闭选中摊位" }).click();
    }
    await expect(page.locator(".selected-stall")).toHaveCount(0);
    release();
    await expect.poll(() => state.completedDetails).toBe(2);
    // Capture a rendered frame after the delayed response completes, including
    // the old card that the unfixed implementation incorrectly resurrects.
    await page.screenshot({ path: info.outputPath(`map-late-${dismiss}.png`), fullPage: true });
    await expect(page.locator(".selected-stall")).toHaveCount(0);
  });
}

test("all dishes share a ten-portion cart allowance", async ({ page }) => {
  await fixture(page);
  await page.goto("/stalls/1/products/10");
  const increase = page.getByRole("button", { name: /增加/ }).first();
  for (let index = 0; index < 6; index++) await increase.click();
  await page
    .getByRole("button", { name: /加入餐袋/ })
    .first()
    .click();
  await page.goto("/stalls/1/products/11");
  await page.getByRole("button", { name: /增加/ }).first().click();
  await page.getByRole("button", { name: /增加/ }).first().click();
  await expect(
    page.getByRole("button", { name: /增加/ }).first(),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: /加入餐袋/ })
    .first()
    .click();
  await expect(
    page
      .getByRole("button", { name: "每单合计最多 10 份", exact: true })
      .first(),
  ).toBeDisabled();
});

test("discovery and map remain readable at four supported widths", async ({
  page,
}, info) => {
  await fixture(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "真实小摊1", exact: true }),
    ).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`discovery-${width}.png`),
      fullPage: true,
    });
    await page.goto("/map");
    await expect(
      page.getByRole("button", { name: "在地图中查看", exact: true }),
    ).toBeVisible();
    await assertNoHorizontalOverflow(page);
  }
});

test('dish, stall menu and remaining cart are usable at phone and desktop sizes', async ({ page }, info) => {
  await fixture(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/stalls/1/products/99');
    await expect(page.getByRole('heading', { name: '菜单深处的柠檬茶', exact: true })).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: info.outputPath(`product-${width}-top.png`) });
    await page.screenshot({ path: info.outputPath(`product-${width}-full.png`), fullPage: true });
    const add = page.getByRole('button', { name: /加入餐袋/ });
    expect((await add.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await page.goto('/stalls/1');
    await expect(page.getByRole('button', { name: '小摊菜单', exact: true })).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: info.outputPath(`stall-${width}-top.png`) });
    if (width < 768) {
      await page.locator('.stall-arrival summary').click();
      await expect(page.getByRole('region', { name: '到摊指引', exact: true })).toBeVisible();
      await page.locator('.stall-arrival summary').click();
    }
    await page.getByRole('link', { name: '查看餐点10详情', exact: true }).click();
    await expect(page.getByRole('heading', { name: '餐点10', exact: true })).toBeVisible();
    await page.getByRole('button', { name: /加入餐袋/ }).click();
    await page.goto('/cart');
    const checkout = page.getByRole('link', { name: '去结算真实小摊1的餐点', exact: true });
    await expect(checkout).toBeVisible();
    await checkout.scrollIntoViewIfNeeded();
    expect((await checkout.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: info.outputPath(`cart-${width}.png`) });
  }
});
