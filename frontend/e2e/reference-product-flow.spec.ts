import { test, expect, type Locator, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

const cartKey = "yanhuo-cart-v2:user:7";
function product(id = 770, name = "椒盐鸡蛋煎饼") {
  return {
    id,
    name,
    description: "现摊鸡蛋饼，椒盐调味。",
    category: "小吃",
    image: "/images/food-jianbing.jpg",
    price_cents: 800,
    availability: "available",
    max_order_quantity: 10,
    sale_paused: false,
    is_active: true,
    taste_options: [{ name: "辣度", choices: ["不辣", "微辣"] }],
  };
}
function stall() {
  return {
    id: 77,
    name: "东门煎饼摊",
    description: "现摊现做",
    category: "小吃",
    image: "/images/food-jianbing.jpg",
    address: "校园东门蓝色雨棚旁",
    latitude: 31,
    longitude: 121,
    area_id: 1,
    area_name: "校园",
    status: "open",
    session_status: "open",
    last_confirmed_at: new Date().toISOString(),
    prep_minutes: 8,
    transaction_enabled: true,
    can_order: true,
    rating: null,
    review_count: 0,
    is_followed: false,
    products: [product(), product(771, "另一份餐点")],
    accepting_orders: true,
    order_unavailable_reason: "",
    usual_hours: "11:00—14:00",
    receiving_status: "recent",
    receiving_valid_for_seconds: 30,
    reviews: [],
    contact_phone: "13800000000",
    closes_at: null,
    merchant_name: "摊主",
    qualification_note: "已核验",
    order_count: 0,
    arrival_note: "认准蓝色雨棚",
    arrival_image: "",
    wechat_payment: {
      available: false,
      mode: "live",
      channels: [],
      reason: "未开通",
    },
    delivery: { available: false, reason: "暂不配送", points: [] },
  };
}
async function fixture(page: Page, rows: unknown[] = []) {
  const detail = stall();
  const user = {
    id: 7,
    username: "student",
    display_name: "同学",
    is_merchant: false,
    is_staff: false,
  };
  await page.addInitScript(
    ({ key, saved }) => {
      if (!localStorage.getItem(key))
        localStorage.setItem(key, JSON.stringify({ 77: saved }));
    },
    { key: cartKey, saved: rows },
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config")
      return route.fulfill({
        json: {
          demo_mode: true,
          brand: "烟火地图",
          amap_key: "",
          amap_proxy: "",
          stale_minutes: 60,
          areas: [{ id: 1, name: "校园", latitude: 31, longitude: 121 }],
          user,
        },
      });
    if (path === "/auth/me") return route.fulfill({ json: user });
    if (path === "/stalls/77") return route.fulfill({ json: detail });
    if (path === "/orders/active-summary")
      return route.fulfill({
        json: {
          orders: [],
          count: 0,
          status_counts: {},
          synced_at: new Date().toISOString(),
        },
      });
    if (path === "/events") return route.fulfill({ json: { id: 1 } });
    return route.fulfill({
      status: 404,
      json: { detail: `测试未定义接口 ${path}` },
    });
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  return detail;
}
async function cartRows(page: Page) {
  return page.evaluate(
    (key) => JSON.parse(localStorage.getItem(key) || "{}")[77] || [],
    cartKey,
  );
}
async function unobstructed(locator: Locator) {
  await expect(locator).toBeInViewport();
  const box = (await locator.boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(
    await locator.evaluate((element) => {
      const box = element.getBoundingClientRect();
      return element.contains(
        document.elementFromPoint(
          box.x + box.width / 2,
          box.y + box.height / 2,
        ),
      );
    }),
  ).toBe(true);
}

for (const width of [360, 390]) {
  test(`product facts and fixed action preserve distinct portions at ${width}px`, async ({
    page,
  }, info) => {
    await fixture(page, [
      {
        product: product(),
        quantity: 1,
        portions: [{ options: { 辣度: "微辣" }, note: "原餐袋备注" }],
        portionKeys: ["original-portion"],
      },
    ]);
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/stalls/77/products/770");
    await expect(
      page.getByRole("heading", { name: "椒盐鸡蛋煎饼", exact: true }),
    ).toBeVisible();
    const add = page.getByRole("button", { name: /加入餐袋/ });
    await unobstructed(add);
    await unobstructed(
      page.getByRole("button", { name: "增加份数", exact: true }),
    );
    await expect(page.locator(".dish-decision-facts")).toBeInViewport();
    await expect(page.locator(".dish-decision-facts")).toContainText(
      "预计 8 分钟备餐",
    );
    await expect(page.locator(".dish-decision-facts")).toContainText(
      "到摊自取",
    );
    await expect(
      page.getByRole("navigation", { name: "底部导航" }),
    ).toHaveCount(0);
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`product-${width}-top.png`),
      animations: "disabled",
    });
    const barHeight = (await page.locator(".dish-action-bar").boundingBox())!
      .height;

    await page.getByRole("button", { name: "增加份数", exact: true }).click();
    await page
      .getByRole("button", {
        name: "设置椒盐鸡蛋煎饼每份口味与备注",
        exact: true,
      })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "椒盐鸡蛋煎饼每份口味与备注",
      exact: true,
    });
    const first = dialog.getByRole("group", { name: "第1份", exact: true });
    const second = dialog.getByRole("group", { name: "第2份", exact: true });
    await first.getByRole("combobox").selectOption("不辣");
    await first.getByRole("textbox").fill("第一份不要香菜");
    await second.getByRole("combobox").selectOption("微辣");
    await second.getByRole("textbox").fill("第二份切开");
    await unobstructed(second.getByRole("textbox"));
    await page.screenshot({
      path: info.outputPath(`product-${width}-portion-dialog.png`),
      animations: "disabled",
    });
    await dialog
      .getByRole("button", { name: "保存口味与备注", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await expect(page.locator(".dish-action-bar .portion-editor")).toHaveCount(
      0,
    );
    await expect(
      page.locator(".dish-purchase-panel > .portion-editor"),
    ).toContainText("第 1 份 · 辣度：不辣 · 第一份不要香菜");
    await expect(
      page.locator(".dish-purchase-panel > .portion-editor"),
    ).toContainText("第 2 份 · 辣度：微辣 · 第二份切开");
    expect((await page.locator(".dish-action-bar").boundingBox())!.height).toBe(
      barHeight,
    );
    await page
      .locator(".dish-contact-note")
      .evaluate((element) => element.scrollIntoView({ block: "center" }));
    const contact = (await page.locator(".dish-contact-note").boundingBox())!;
    const bar = (await page.locator(".dish-action-bar").boundingBox())!;
    expect(contact.y + contact.height).toBeLessThanOrEqual(bar.y);
    await page.screenshot({
      path: info.outputPath(`product-${width}-requirements.png`),
      animations: "disabled",
    });
    await unobstructed(add);
    await add.click();
    await expect.poll(async () => (await cartRows(page))[0]?.quantity).toBe(3);
    expect((await cartRows(page))[0].portions).toEqual([
      { options: { 辣度: "微辣" }, note: "原餐袋备注" },
      { options: { 辣度: "不辣" }, note: "第一份不要香菜" },
      { options: { 辣度: "微辣" }, note: "第二份切开" },
    ]);
    await expect(
      page.getByRole("link", { name: "去结算", exact: true }),
    ).toHaveAttribute("href", "/checkout/77");
    await page.getByRole("link", { name: "返回小摊", exact: true }).click();
    await expect(page).toHaveURL("/stalls/77");
    await expect(
      page.getByRole("heading", { name: "东门煎饼摊", exact: true }),
    ).toBeVisible();
    expect((await cartRows(page))[0].quantity).toBe(3);
  });
}

test("product decision layout remains readable on tablet and desktop", async ({
  page,
}, info) => {
  await fixture(page);
  for (const width of [768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/stalls/77/products/770");
    await expect(
      page.getByRole("heading", { name: "椒盐鸡蛋煎饼", exact: true }),
    ).toBeVisible();
    await assertNoHorizontalOverflow(page);
    const add = page.getByRole("button", { name: /加入餐袋/ });
    await add.scrollIntoViewIfNeeded();
    await unobstructed(add);
    await page.screenshot({
      path: info.outputPath(`product-${width}-full.png`),
      fullPage: true,
      animations: "disabled",
    });
  }
});

test("sold out, paused and stale product states never add food", async ({
  page,
}) => {
  const detail = await fixture(page);
  await page.setViewportSize({ width: 360, height: 844 });
  for (const state of ["sold_out", "sale_paused", "stale"]) {
    detail.products[0].availability =
      state === "sold_out" ? "sold_out" : "available";
    detail.products[0].sale_paused = state === "sale_paused";
    detail.status = state === "stale" ? "stale" : "open";
    detail.can_order = state !== "stale";
    detail.order_unavailable_reason =
      state === "stale" ? "摊位位置需要重新确认" : "";
    await page.goto("/stalls/77/products/770");
    await expect(page.locator(".dish-add-button")).toBeDisabled();
    await expect(page.locator(".dish-unavailable")).toBeVisible();
    await expect(
      page.locator(".dish-purchase-panel > .portion-editor"),
    ).toHaveCount(0);
    await page
      .locator(".dish-purchase-panel")
      .evaluate((form) => (form as HTMLFormElement).requestSubmit());
    expect(await cartRows(page)).toEqual([]);
  }
});

test("other dishes count toward the ten-portion limit in the product action", async ({
  page,
}) => {
  await fixture(page, [{ product: product(771, "另一份餐点"), quantity: 9 }]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/stalls/77/products/770");
  await expect(
    page.getByRole("button", { name: "增加份数", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  await expect(
    page.getByRole("button", { name: "每单合计最多 10 份", exact: true }),
  ).toBeDisabled();
  expect(
    (await cartRows(page)).map(
      (row: { product: { id: number }; quantity: number }) => [
        row.product.id,
        row.quantity,
      ],
    ),
  ).toEqual([
    [771, 9],
    [770, 1],
  ]);
});

test("changed taste options cannot bypass validation through the fixed submit button", async ({
  page,
}) => {
  const detail = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/stalls/77/products/770");
  await page
    .getByRole("button", {
      name: "设置椒盐鸡蛋煎饼每份口味与备注",
      exact: true,
    })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox").selectOption("微辣");
  await dialog
    .getByRole("button", { name: "保存口味与备注", exact: true })
    .click();
  detail.products[0].taste_options = [{ name: "辣度", choices: ["不辣"] }];
  const refreshed = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/stalls/77"),
  );
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await refreshed;
  await expect(
    page.getByText("口味选项已变更，请重新选择后再结算。"),
  ).toBeVisible();
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  expect(await cartRows(page)).toEqual([]);
});
