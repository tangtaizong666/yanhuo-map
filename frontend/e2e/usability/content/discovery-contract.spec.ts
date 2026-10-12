import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../../helpers";

async function fixture(page: Page) {
  const product = {
    id: 911,
    name: "鸡蛋煎饼",
    description: "",
    price_cents: 800,
    image: "",
    availability: "available",
    max_order_quantity: 10,
    sale_paused: false,
    is_active: true,
    taste_options: [],
  };
  const stall = {
    id: 91,
    name: "东门现摊煎饼",
    image: "/images/food-jianbing.jpg",
    category: "小吃",
    status: "open",
    session_status: "open",
    address: "校园东门蓝色雨棚旁",
    area_id: 1,
    area_name: "校园",
    last_confirmed_at: new Date().toISOString(),
    prep_minutes: 8,
    transaction_enabled: true,
    can_order: true,
    accepting_orders: true,
    rating: null,
    review_count: 0,
    products: [product],
  };
  const meals = [
    { product, stall },
    {
      product: {
        ...product,
        id: 912,
        name: "双份鸡蛋配新鲜蔬菜的手作杂粮煎饼",
        image: "/images/food-jianbing.jpg",
        description: "现摊鸡蛋饼，配菜清爽。",
        price_cents: 1800,
      },
      stall,
    },
    {
      product: {
        ...product,
        id: 913,
        name: "原味杂粮饼",
        description: "   ",
        display_only: true,
      },
      stall: { ...stall, id: 92, transaction_enabled: false, can_order: false },
    },
  ];
  const unexpected: string[] = [];
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    const send = (json: unknown) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/events") return send({ id: 1 });
    if (request.method() === "GET") {
      if (path === "/config")
        return send({ user: null, demo_mode: false, amap_key: "", areas: [] });
      if (path === "/auth/me") return send(null);
      if (path === "/stalls") return send({ results: [stall], next: null });
      if (path === "/products") {
        const budget = Number(url.searchParams.get("budget") || 0);
        return send({
          results: meals.filter(
            (meal) => !budget || meal.product.price_cents <= budget,
          ),
          next: null,
        });
      }
    }
    unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  return { unexpected };
}

test("dish cards prioritize names and prices without inventing descriptions at four widths", async ({
  page,
}, info) => {
  const state = await fixture(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/search?type=dishes");
    const cards = page.locator(".dish-card");
    await expect(cards).toHaveCount(3);
    const first = cards.nth(0),
      described = cards.nth(1),
      offline = cards.nth(2);
    await expect(first.locator(".dish-description")).toHaveCount(0);
    await expect(offline.locator(".dish-description")).toHaveCount(0);
    await expect(described.locator(".dish-description")).toHaveText(
      "现摊鸡蛋饼，配菜清爽。",
    );
    await expect(first).toContainText("暂无餐点照片");
    await expect(first).toContainText("可以点单 · 自取");
    await expect(offline).toContainText("到摊选购");
    await expect(first).toContainText("通常备餐约 8 分钟");
    await expect(first).toContainText("接单后更新");
    await expect(first).not.toContainText("点开看看这份好味道");
    await expect(first).not.toContainText("查看餐点");
    await expect(first).toHaveAttribute("href", "/stalls/91/products/911");
    await expect(described).toHaveAccessibleName(
      "查看双份鸡蛋配新鲜蔬菜的手作杂粮煎饼详情，东门现摊煎饼",
    );
    for (const card of [first, described, offline]) {
      const box = (await card.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    await first.focus();
    await expect(first).toBeFocused();
    await first.blur();
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`dish-content-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
  expect(state.unexpected).toEqual([]);
});

test("mobile search separates selected filters from sorting without pushing the first price below navigation", async ({
  page,
}, info) => {
  const state = await fixture(page);
  for (const width of [360, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/search?q=煎饼&meal_budget=1000&meal_sort=price");
    await page.getByRole("button", { name: "筛选", exact: true }).click();
    await page
      .getByRole("navigation", { name: "美食品类" })
      .getByRole("button", { name: "街头小吃" })
      .click();
    await page.getByLabel("摊位排序", { exact: true }).selectOption("rating");
    await page.getByRole("button", { name: "收起筛选", exact: true }).click();
    await expect(page.locator(".dish-card")).toHaveCount(2);
    await expect(page.locator(".search-filter-summary")).toContainText(
      "小吃 · 正在出摊 · 摊位评分优先 · 餐点 10 元以内 · 餐点价格从低到高",
    );
    const primary = (await page
      .locator(".search-filter-primary")
      .boundingBox())!;
    const details = (await page
      .locator(".search-filter-details")
      .boundingBox())!;
    const toggle = (await page.locator(".search-filter-toggle").boundingBox())!;
    expect(details.y).toBeGreaterThanOrEqual(primary.y + primary.height);
    expect(details.width).toBeGreaterThan(primary.width);
    expect(toggle.height).toBeGreaterThanOrEqual(44);
    const price = (await page
      .locator(".dish-card-bottom strong")
      .first()
      .boundingBox())!;
    const nav = (await page.locator(".mobile-nav").boundingBox())!;
    expect(price.y + price.height).toBeLessThan(nav.y);
    await expect(page).toHaveURL(/meal_budget=1000/);
    await expect(page).toHaveURL(/meal_sort=price/);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`search-summary-${width}.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
  expect(state.unexpected).toEqual([]);
});
