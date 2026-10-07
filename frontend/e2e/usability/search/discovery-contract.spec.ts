import { expect, test, type Locator, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../../helpers";

async function fixture(page: Page) {
  const user = {
    id: 71,
    username: "search-student",
    display_name: "同学",
    is_merchant: false,
    is_staff: false,
  };
  const product = {
    id: 711,
    name: "鸡蛋煎饼",
    description: "现摊鸡蛋饼，配菜清爽。",
    price_cents: 800,
    image: "/images/food-jianbing.jpg",
    availability: "available",
    max_order_quantity: 10,
    sale_paused: false,
    is_active: true,
    taste_options: [],
  };
  const stall = {
    id: 71,
    name: "东门煎饼摊",
    description: "现摊现做的校园小摊",
    image: "/images/food-jianbing.jpg",
    category: "小吃",
    status: "open",
    session_status: "open",
    address: "校园东门蓝色雨棚旁",
    area_id: 1,
    area_name: "校园",
    latitude: 31,
    longitude: 121,
    last_confirmed_at: new Date().toISOString(),
    prep_minutes: 8,
    transaction_enabled: false,
    can_order: false,
    accepting_orders: false,
    rating: null,
    review_count: 0,
    is_followed: true,
    products: [product],
    order_unavailable_reason: "仅供线下到访",
  };
  const meals = [
    { product, stall },
    {
      product: { ...product, id: 712, name: "双份鸡蛋煎饼", price_cents: 1800 },
      stall,
    },
  ];
  const state = {
    emptyMeals: false,
    emptyStalls: false,
    failMeals: false,
    failStalls: false,
    mealReads: [] as string[],
    stallReads: [] as string[],
    writes: [] as string[],
    unexpected: [] as string[],
  };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname.replace("/api/v1", "");
    const send = (json: unknown) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/events") return send({ id: 1 });
    if (request.method() !== "GET")
      state.writes.push(`${request.method()} ${path}`);
    if (path === "/config")
      return send({
        user,
        brand: "烟火地图",
        demo_mode: false,
        amap_key: "",
        areas: [{ id: 1, name: "校园", latitude: 31, longitude: 121 }],
      });
    if (path === "/auth/me") return send(user);
    if (path === "/orders/active-summary")
      return send({
        user_id: user.id,
        orders: [],
        count: 0,
        status_counts: {},
        synced_at: new Date().toISOString(),
      });
    if (path === "/orders/recent-completed") return send([]);
    if (path === "/products") {
      state.mealReads.push(url.search);
      if (state.failMeals)
        return route.fulfill({
          status: 503,
          json: { detail: "餐点读取暂时失败" },
        });
      const budget = Number(url.searchParams.get("budget") || 0),
        category = url.searchParams.get("category");
      const rows =
        state.emptyMeals || (category && category !== "小吃")
          ? []
          : meals.filter((row) => !budget || row.product.price_cents <= budget);
      return send({ results: rows, next: null });
    }
    if (path === "/stalls") {
      state.stallReads.push(url.search);
      if (state.failStalls)
        return route.fulfill({
          status: 503,
          json: { detail: "摊位读取暂时失败" },
        });
      const category = url.searchParams.get("category");
      return send({
        results:
          state.emptyStalls || (category && category !== "小吃") ? [] : [stall],
        next: null,
      });
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  return state;
}

async function targetSize(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(box!.width).toBeGreaterThanOrEqual(44);
}

test("search results stay above the mobile fold while desktop filters start expanded", async ({
  page,
}, info) => {
  const state = await fixture(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/search?q=煎饼");
    await expect(page.locator(".dish-card")).toHaveCount(2);
    const toggle = page.locator(".search-filter-toggle");
    await expect(toggle).toHaveAttribute(
      "aria-expanded",
      width < 768 ? "false" : "true",
    );
    await expect(page.locator(".search-filter-summary")).toContainText(
      "正在出摊",
    );
    await targetSize(toggle);
    await assertNoHorizontalOverflow(page);
    if (width < 768) {
      await expect(page.locator(".search-categories")).toBeHidden();
      const card = page.locator(".dish-card").first(),
        title = card.locator("h3"),
        price = card.locator(".dish-card-bottom strong");
      const nav = await page.locator(".mobile-nav").boundingBox();
      expect(nav).not.toBeNull();
      expect((await title.boundingBox())!.y).toBeGreaterThan(0);
      expect(
        (await price.boundingBox())!.y + (await price.boundingBox())!.height,
      ).toBeLessThan(nav!.y);
    }
    await page.screenshot({
      path: info.outputPath(`search-results-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("search filters apply immediately preserve values across types and clear without clearing the query", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/search?q=煎饼");
  await expect(page.locator(".dish-card")).toHaveCount(2);
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await page
    .getByRole("navigation", { name: "美食品类" })
    .getByRole("button", { name: "街头小吃" })
    .click();
  await page.getByRole("button", { name: "10 元以内", exact: true }).click();
  await page.getByLabel("餐点排序", { exact: true }).selectOption("price");
  await page.getByLabel("摊位排序", { exact: true }).selectOption("rating");
  await expect(page.locator(".dish-card")).toHaveCount(1);
  await expect(page.locator(".food-grid > .stall-card")).toHaveCount(1);
  await expect(page).toHaveURL(/meal_budget=1000/);
  await expect(page).toHaveURL(/meal_sort=price/);
  await page.getByRole("button", { name: "收起筛选", exact: true }).click();
  await expect(page.locator(".search-filter-summary")).toContainText(
    "小吃 · 正在出摊 · 摊位评分优先 · 餐点 10 元以内 · 餐点价格从低到高",
  );
  await expect(page.locator(".search-filter-controls")).toBeHidden();
  await page
    .locator(".result-switch")
    .getByRole("button", { name: /摊位/ })
    .click();
  await expect(page.locator(".food-grid > .stall-card")).toHaveCount(1);
  await expect(page.locator(".search-filter-summary")).not.toContainText(
    "餐点 10",
  );
  await page
    .locator(".result-switch")
    .getByRole("button", { name: /餐点/ })
    .click();
  await expect(page.locator(".dish-card")).toHaveCount(1);
  await expect(page.locator(".search-filter-summary")).toContainText(
    "餐点 10 元以内",
  );
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "10 元以内", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("餐点排序", { exact: true })).toHaveValue(
    "price",
  );
  await expect(page.getByLabel("摊位排序", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  await expect(page.locator(".dish-card")).toHaveCount(2);
  await expect(
    page.getByRole("textbox", { name: "搜索摊位或美食" }),
  ).toHaveValue("煎饼");
  await expect(page).not.toHaveURL(/meal_budget|meal_sort|follow=/);
  expect(state.mealReads.some((value) => value.includes("budget=1000"))).toBe(
    true,
  );
  expect(
    state.stallReads.every(
      (value) => !new URLSearchParams(value).has("budget"),
    ),
  ).toBe(true);
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("mixed search uses a compact empty category without displacing available results at four widths", async ({
  page,
}, info) => {
  const state = await fixture(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    state.emptyMeals = true;
    state.emptyStalls = false;
    await page.goto("/search");
    await expect(page.locator(".food-grid > .stall-card")).toHaveCount(1);
    await expect(
      page.locator(".meal-search-section .search-empty-inline"),
    ).toContainText("暂未找到匹配的可售餐点");
    await expect(page.locator(".meal-search-section .empty-state")).toHaveCount(
      0,
    );
    if (width < 768) {
      const title = await page
        .locator(".food-grid .stall-title")
        .first()
        .boundingBox();
      expect(title!.y + title!.height).toBeLessThan(
        (await page.locator(".mobile-nav").boundingBox())!.y,
      );
    }
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`search-only-stalls-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    state.emptyMeals = false;
    state.emptyStalls = true;
    await page.goto("/search");
    await expect(page.locator(".dish-card")).toHaveCount(2);
    await expect(
      page.locator(".nearby-section .search-empty-inline"),
    ).toContainText("没有匹配摊位");
    await expect(page.locator(".nearby-section .empty-state")).toHaveCount(0);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`search-only-meals-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("search failures remain explicit with independent retries instead of compact empty states", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  state.failMeals = true;
  await page.goto("/search");
  await expect(page.locator(".food-grid > .stall-card")).toHaveCount(1);
  await expect(page.locator(".meal-search-section")).toContainText(
    "餐点读取暂时失败",
  );
  await expect(
    page.locator(".meal-search-section .search-empty-inline"),
  ).toHaveCount(0);
  state.failMeals = false;
  await page.getByRole("button", { name: "重新加载餐点", exact: true }).click();
  await expect(page.locator(".dish-card")).toHaveCount(2);
  state.failStalls = true;
  await page.goto("/search");
  await expect(page.locator(".dish-card")).toHaveCount(2);
  await expect(page.locator(".nearby-section")).toContainText(
    "摊位读取暂时失败",
  );
  await expect(
    page.locator(".nearby-section .search-empty-inline"),
  ).toHaveCount(0);
  state.failStalls = false;
  await page
    .locator(".nearby-section")
    .getByRole("button", { name: "重新加载", exact: true })
    .click();
  await expect(page.locator(".food-grid > .stall-card")).toHaveCount(1);
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("homepage keeps the existing hero photography and visible category navigation", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator(".home-hero img")).toHaveAttribute(
    "src",
    "/images/night-market.jpg",
  );
  await expect(page.locator(".home-hero")).toBeVisible();
  expect((await page.locator(".home-hero").boundingBox())!.height).toBe(112);
  await expect(
    page.getByRole("navigation", { name: "美食品类" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "美食品类" }).getByRole("button"),
  ).toHaveCount(6);
  await expect(page.getByRole("region", { name: "搜索筛选" })).toHaveCount(0);
  await expect(page.locator(".meal-inspiration .meal-budget")).toBeVisible();
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});
