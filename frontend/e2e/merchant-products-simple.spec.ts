import { test, expect, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Every API is intercepted: these layout contracts never touch a business DB.
async function fixture(page: Page) {
  const user = {
    id: 981,
    username: "menu_merchant",
    display_name: "校园饭摊",
    is_merchant: true,
    is_staff: false,
  };
  const product = (id: number, name: string, extra: any = {}) => ({
    id,
    name,
    description: "商家真实填写的菜品介绍",
    category: "主食",
    image: "",
    price_cents: 1200,
    stock: 8,
    stock_version: 3,
    is_active: true,
    sale_paused: false,
    taste_options: [],
    ...extra,
  });
  const products = [
    product(982, "香菇鸡肉饭"),
    product(983, "红豆汤", { stock: 0 }),
    product(984, "招牌蛋炒饭", { sale_paused: true }),
  ];
  const stall = {
    id: 981,
    name: "校园饭摊",
    products,
    reviews: [],
    image: "",
    area_name: "南门",
    address: "南门橙色棚",
    latitude: 30,
    longitude: 120,
    status: "open",
    session_status: "open",
    prep_minutes: 10,
    accepting_orders: true,
    transaction_enabled: true,
    is_visible: true,
    can_order: true,
    contact_phone: "",
    services: { mode: "live" },
    last_confirmed_at: new Date().toISOString(),
    closes_at: new Date(Date.now() + 7200000).toISOString(),
  };
  const state = {
    products,
    patches: [] as any[],
    corrections: [] as any[],
    restocks: [] as any[],
    unexpected: [] as string[],
  };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config")
      return send({
        user,
        demo_mode: false,
        areas: [],
        amap_key: "",
        stale_minutes: 60,
      });
    if (path === "/auth/me") return send(user);
    if (path === "/events") return send({});
    if (path === "/merchant/stalls") return send([stall]);
    if (path === "/merchant/orders")
      return send({
        results: [],
        next: null,
        counts: { all: 0, active: 0, attention: 0 },
      });
    if (path === "/merchant/metrics")
      return send({
        today: { revenue_cents: 0, orders_created: 0, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    const correction = path.match(
      /^\/merchant\/products\/(\d+)\/stock-correction$/,
    );
    if (correction) {
      const body = request.postDataJSON(),
        row = products.find((p) => p.id === Number(correction[1]))!;
      state.corrections.push(body);
      return send({ product: row, replayed: true });
    }
    if (path === "/merchant/stalls/981/restock") {
      state.restocks.push(request.postDataJSON());
      return send({ replayed: true });
    }
    const patch = path.match(/^\/merchant\/products\/(\d+)$/);
    if (patch && request.method() === "PATCH") {
      const body = request.postDataJSON(),
        row = products.find((p) => p.id === Number(patch[1]))!;
      state.patches.push({ id: row.id, body });
      Object.assign(row, body);
      return send(row);
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  return state;
}

function card(page: Page, name: string) {
  return page
    .locator(".merchant-product")
    .filter({ has: page.getByRole("heading", { name, exact: true }) });
}

test("menu cards show readable price, stock and one supply action without inline editing", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto("/merchant/products");
  const chicken = card(page, "香菇鸡肉饭");
  await expect(chicken).toContainText("¥12");
  await expect(chicken).toContainText("线上可卖 8 份");
  await expect(chicken.locator(".product-status")).toHaveText("销售中");
  await expect(card(page, "红豆汤").locator(".product-status")).toHaveText(
    "已售罄",
  );
  await expect(card(page, "招牌蛋炒饭").locator(".product-status")).toHaveText(
    "暂停供应",
  );
  await expect(chicken.getByRole("spinbutton")).toHaveCount(0);
  await expect(chicken.getByRole("button")).toHaveCount(2);
  await expect(
    chicken.getByRole("button", { name: "编辑商品：香菇鸡肉饭", exact: true }),
  ).toBeVisible();
  await expect(
    chicken.getByRole("button", { name: "暂停供应", exact: true }),
  ).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
    await assertNoHorizontalOverflow(page);
    const box = await chicken
      .getByRole("button", { name: "暂停供应", exact: true })
      .boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    if (width <= 390) {
      const first = await chicken.boundingBox();
      const second = await card(page, "红豆汤").boundingBox();
      const navigation = await page
        .getByRole("navigation", { name: "商家底部导航" })
        .boundingBox();
      expect(first!.height).toBeLessThanOrEqual(165);
      expect(second!.y + second!.height).toBeLessThan(navigation!.y);
      const tabs = await page
        .getByRole("group", { name: "商品状态筛选" })
        .getByRole("button")
        .all();
      const boxes = await Promise.all(tabs.map((tab) => tab.boundingBox()));
      expect(new Set(boxes.map((b) => Math.round(b!.y))).size).toBe(1);
      expect(boxes.every((b) => b!.height >= 44)).toBe(true);
    }
    await page.screenshot({
      path: info.outputPath(`menu-${width}.png`),
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 360, height: 844 });
  const filters = page.getByRole("group", { name: "商品状态筛选" });
  const offShelf = filters.getByRole("button", { name: /^已下架/ });
  await offShelf.click();
  await expect(offShelf).toHaveAttribute("aria-pressed", "true");
  await expect
    .poll(async () => {
      const container = await filters.boundingBox(),
        selected = await offShelf.boundingBox();
      return (
        selected!.x >= container!.x &&
        selected!.x + selected!.width <= container!.x + container!.width + 1
      );
    })
    .toBe(true);
  expect(state.unexpected).toEqual([]);
});

test("editing price and menu visibility preserves current stock and manual supply pause", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant/products");
  const rice = card(page, "招牌蛋炒饭");
  await rice.getByRole("button", { name: /^编辑商品：/ }).click();
  const dialog = page.getByRole("dialog", { name: "编辑商品", exact: true });
  await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("13.50");
  // A concurrent reservation changes stock while the editor is open.
  state.products[2].stock = 6;
  state.products[2].stock_version++;
  await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.patches[0]).toEqual({ id: 984, body: { price_cents: 1350 } });
  await expect(rice).toContainText("线上可卖 6 份");
  await expect(rice.locator(".product-status")).toHaveText("暂停供应");
  await rice.getByRole("button", { name: /^编辑商品：/ }).click();
  await dialog.getByRole("switch", { name: "上架销售", exact: true }).uncheck();
  await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(rice.locator(".product-status")).toHaveText("已下架");
  expect(state.patches[1]).toEqual({ id: 984, body: { is_active: false } });
  expect(state.products[2].stock).toBe(6);
  expect(state.products[2].sale_paused).toBe(true);
  expect(state.unexpected).toEqual([]);
});

test("supply pause is independent of sold-out quantity and never changes stock", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant/products");
  const chicken = card(page, "香菇鸡肉饭"),
    soup = card(page, "红豆汤");
  await chicken.getByRole("button", { name: "暂停供应", exact: true }).click();
  await expect(chicken.locator(".product-status")).toHaveText("暂停供应");
  expect(state.products[0].stock).toBe(8);
  await chicken.getByRole("button", { name: "恢复供应", exact: true }).click();
  await expect(chicken.locator(".product-status")).toHaveText("销售中");
  await soup.getByRole("button", { name: "暂停供应", exact: true }).click();
  await expect(soup.locator(".product-status")).toHaveText("暂停供应");
  await soup.getByRole("button", { name: "恢复供应", exact: true }).click();
  await expect(soup.locator(".product-status")).toHaveText("已售罄");
  expect(state.products[1].stock).toBe(0);
  expect(state.patches.map((p) => p.body)).toEqual([
    { sale_paused: true },
    { sale_paused: false },
    { sale_paused: true },
    { sale_paused: false },
  ]);
  expect(state.unexpected).toEqual([]);
});

test("unconfirmed correction and restock remain automatically visible and reuse original requests", async ({
  page,
}) => {
  const state = await fixture(page);
  const correction = {
    stock: 8,
    expected_stock_version: 2,
    reason: "盘点复核",
    idempotency_key: "menu-correction-original",
  };
  const restock = {
    items: [{ product_id: 984, quantity: 4 }],
    idempotency_key: "menu-restock-original",
  };
  await page.addInitScript(
    ({ correction, restock }) => {
      sessionStorage.setItem(
        "merchant-stock-correction:981:981:982",
        JSON.stringify(correction),
      );
      sessionStorage.setItem(
        "merchant-restock:981:981",
        JSON.stringify(restock),
      );
    },
    { correction, restock },
  );
  await page.goto("/merchant/products");
  const chicken = card(page, "香菇鸡肉饭");
  await expect(
    chicken.getByRole("button", { name: "确认原更正结果", exact: true }),
  ).toBeVisible();
  await expect(chicken.locator(".inventory-correction")).toHaveAttribute(
    "open",
    "",
  );
  await expect(page.locator(".restock")).toHaveAttribute("open", "");
  await chicken
    .getByRole("button", { name: "确认原更正结果", exact: true })
    .click();
  await expect.poll(() => state.corrections.length).toBe(1);
  expect(state.corrections[0]).toEqual(correction);
  await page
    .getByRole("button", { name: "重试确认这批补货", exact: true })
    .click();
  await expect.poll(() => state.restocks.length).toBe(1);
  expect(state.restocks[0]).toEqual(restock);
  expect(state.products[2].sale_paused).toBe(true);
  expect(state.unexpected).toEqual([]);
});
