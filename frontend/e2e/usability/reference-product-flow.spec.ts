import { test, expect, type Page, type Locator } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../helpers";

const cartKey = "yanhuo-cart-v2:user:7";
const product = {
  id: 770,
  name: "椒盐鸡蛋煎饼",
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
async function fixture(page: Page, withCart = false) {
  const user = {
    id: 7,
    username: "student",
    display_name: "同学",
    is_merchant: false,
    is_staff: false,
  };
  const detail = {
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
    products: [product],
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
    arrival_note: "认准蓝色雨棚",
    arrival_image: "",
    wechat_payment: {
      supported: true,
      available: false,
      mode: "live",
      channels: [] as string[],
      reason: "商家尚未配置微信支付",
    },
    delivery: {
      available: false,
      enabled: false,
      mode: "live",
      reason: "商家今天不提供配送",
      points: [] as any[],
      fee_cents: 200,
      min_order_cents: 0,
      starts_at: "00:00",
      ends_at: "23:59",
      eta_min_minutes: 15,
      eta_max_minutes: 30,
    },
  };
  const writes: Record<string, unknown>[] = [];
  await page.addInitScript(
    ({ key, food, seed }) => {
      if (!localStorage.getItem(key))
        localStorage.setItem(
          key,
          JSON.stringify({
            77: seed
              ? [
                  {
                    product: food,
                    quantity: 1,
                    portions: [{ options: { 辣度: "微辣" }, note: "原口味" }],
                    portionKeys: ["original"],
                  },
                ]
              : [],
            88: [
              {
                product: { ...food, id: 880, name: "另一摊草稿" },
                quantity: 1,
                portionKeys: ["other-stall"],
              },
            ],
          }),
        );
    },
    { key: cartKey, food: product, seed: withCart },
  );
  await page.context().route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/events") return route.fulfill({ json: { id: 1 } });
    if (route.request().method() !== "GET") {
      writes.push({ path, body: route.request().postDataJSON() });
      return route.fulfill({
        status: 409,
        json: { detail: "隔离检查：未创建订单，请保留草稿。" },
      });
    }
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
    return route.fulfill({
      status: 404,
      json: { detail: `未定义隔离接口 ${path}` },
    });
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  return { detail, writes, user };
}
async function visibleAction(locator: Locator) {
  await expect(locator).toBeInViewport({ ratio: 1 });
  const box = (await locator.boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(
    await locator.evaluate((el) => {
      const box = el.getBoundingClientRect();
      return el.contains(
        document.elementFromPoint(
          box.x + box.width / 2,
          box.y + box.height / 2,
        ),
      );
    }),
  ).toBe(true);
}
async function readCart(page: Page) {
  return page.evaluate(
    (key) => JSON.parse(localStorage.getItem(key) || "{}"),
    cartKey,
  );
}

test("removing the current dish in another tab clears its completion state while keeping other dishes", async ({
  page,
}) => {
  const { detail, writes } = await fixture(page);
  detail.products.push({ ...product, id: 771, name: "另一份餐点" });
  await page.goto("/stalls/77/products/771");
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  await page.goto("/stalls/77/products/770");
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  await expect(
    page.getByRole("link", { name: "去结算", exact: true }),
  ).toHaveClass(/btn-primary/);
  const other = await page.context().newPage();
  await other.goto("/cart");
  await other
    .getByRole("button", { name: "移除椒盐鸡蛋煎饼", exact: true })
    .click();
  await expect
    .poll(async () =>
      (await readCart(page))[77].map(
        (row: { product: { id: number } }) => row.product.id,
      ),
    )
    .toEqual([771]);
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeVisible();
  await expect(page.locator(".dish-added-feedback")).toHaveCount(0);
  await expect(page.locator(".dish-cart-line")).toContainText("本摊餐袋 1 份");
  expect((await readCart(page))[88][0].portionKeys).toEqual(["other-stall"]);
  // Re-adding from another tab must not revive this tab's obsolete success message.
  await other.goto("/stalls/77/products/770");
  await other.getByRole("button", { name: /加入餐袋/ }).click();
  await expect(page.locator(".dish-cart-line")).toContainText("本摊餐袋 2 份");
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeVisible();
  await expect(page.locator(".dish-added-feedback")).toHaveCount(0);
  await other.close();
  expect(writes).toEqual([]);
});

test("switching accounts does not inherit an added dish completion state", async ({
  page,
}) => {
  const { user, writes } = await fixture(page);
  await page.goto("/stalls/77/products/770");
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  await expect(
    page.getByRole("link", { name: "去结算", exact: true }),
  ).toHaveClass(/btn-primary/);
  await page.evaluate(
    (food) =>
      localStorage.setItem(
        "yanhuo-cart-v2:user:8",
        JSON.stringify({
          77: [
            {
              product: food,
              quantity: 2,
              portionKeys: ["account-b-1", "account-b-2"],
            },
          ],
        }),
      ),
    product,
  );
  user.id = 8;
  user.username = "student_b";
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator(".dish-cart-line")).toContainText("本摊餐袋 2 份");
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeVisible();
  await expect(page.locator(".dish-added-feedback")).toHaveCount(0);
  expect((await readCart(page))[77][0].quantity).toBe(1);
  expect(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("yanhuo-cart-v2:user:8") || "{}")[77][0]
          .portionKeys,
    ),
  ).toEqual(["account-b-1", "account-b-2"]);
  expect(writes).toEqual([]);
});

for (const width of [360, 390]) {
  test(`first dish and the next action fit the mobile flow at ${width}px`, async ({
    page,
  }, info) => {
    const { writes } = await fixture(page);
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/stalls/77");
    await expect(
      page.getByRole("heading", { name: "东门煎饼摊", exact: true }),
    ).toBeVisible();
    await page.screenshot({ path: info.outputPath(`stall-${width}.png`) });
    await visibleAction(
      page.getByRole("button", { name: "添加椒盐鸡蛋煎饼", exact: true }),
    );
    await expect(page.locator(".product-bottom .price").first()).toBeInViewport(
      { ratio: 1 },
    );
    await page
      .getByRole("link", { name: "查看椒盐鸡蛋煎饼详情", exact: true })
      .click();
    await page.getByRole("button", { name: "增加份数", exact: true }).click();
    await page
      .getByRole("button", {
        name: "设置椒盐鸡蛋煎饼每份口味与备注",
        exact: true,
      })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog
      .getByRole("group", { name: "第1份", exact: true })
      .getByRole("combobox")
      .selectOption("微辣");
    await dialog
      .getByRole("group", { name: "第2份", exact: true })
      .getByRole("textbox")
      .fill("第二份切开");
    await dialog
      .getByRole("button", { name: "保存口味与备注", exact: true })
      .click();
    await page.getByRole("button", { name: /加入餐袋/ }).click();
    const checkout = page.getByRole("link", { name: "去结算", exact: true });
    await expect(checkout).toHaveClass(/btn-primary/);
    await visibleAction(checkout);
    await visibleAction(
      page.getByRole("link", { name: "继续选餐", exact: true }),
    );
    await expect(page.getByRole("button", { name: /加入餐袋/ })).toHaveCount(0);
    await page
      .locator(".dish-purchase-panel")
      .evaluate((form) => (form as HTMLFormElement).requestSubmit());
    expect((await readCart(page))[77][0].quantity).toBe(2);
    expect((await readCart(page))[77][0].portions).toEqual([
      { options: { 辣度: "微辣" }, note: "" },
      { options: {}, note: "第二份切开" },
    ]);
    await page.screenshot({ path: info.outputPath(`added-${width}.png`) });
    await page
      .getByRole("button", { name: "再加这道餐点", exact: true })
      .click();
    await page.getByRole("button", { name: "增加份数", exact: true }).click();
    await expect(page.getByRole("button", { name: /加入餐袋/ })).toContainText(
      "¥16",
    );
    expect((await readCart(page))[77][0].quantity).toBe(2);
    await page.getByRole("link", { name: "去结算", exact: true }).click();
    await expect(page).toHaveURL("/checkout/77");
    expect((await readCart(page))[88][0].portionKeys).toEqual(["other-stall"]);
    expect(writes).toEqual([]);
    await assertNoHorizontalOverflow(page);
  });
}

test("pickup checkout keeps optional drafts and explains unavailable services on demand", async ({
  page,
}, info) => {
  const { writes } = await fixture(page, true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/checkout/77");
  await expect(
    page.getByRole("button", { name: "提交自取订单", exact: true }),
  ).toBeEnabled();
  await expect(page.getByRole("button", { name: /商家配送/ })).toHaveCount(0);
  await expect(page.locator(".wechat-method")).toHaveCount(0);
  await expect(
    page.getByRole("textbox", { name: /联系手机号/ }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("textbox", { name: /整单备注/ }),
  ).not.toBeVisible();
  await expect(
    page.getByText("取餐时扫摊主本人的收款码付款，平台不经手款项。"),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("checkout-compact-full.png"),
    fullPage: true,
  });
  await page.getByText("配送暂未开放 · 查看原因", { exact: true }).click();
  await expect(
    page.getByText("商家今天不提供配送", { exact: true }),
  ).toBeVisible();
  await page.getByText("为什么微信支付尚未开通？", { exact: true }).click();
  await expect(
    page.getByText("商家尚未配置微信支付", { exact: true }),
  ).toBeVisible();
  const contact = page.locator(".checkout-contact-card > summary");
  await contact.click();
  await page.getByRole("textbox", { name: /联系手机号/ }).fill("13812345678");
  await page.getByRole("textbox", { name: /整单备注/ }).fill("餐具一套");
  await contact.click();
  await expect(contact).toContainText("13812345678");
  await expect(contact).toContainText("餐具一套");
  await page
    .getByRole("link", { name: "查看椒盐鸡蛋煎饼详情", exact: true })
    .click();
  await page.getByRole("link", { name: "去结算", exact: true }).click();
  await expect(contact).toContainText("餐具一套");
  await contact.click();
  await expect(page.getByRole("textbox", { name: /联系手机号/ })).toHaveValue(
    "13812345678",
  );
  await expect(page.getByRole("textbox", { name: /整单备注/ })).toHaveValue(
    "餐具一套",
  );
  await contact.click();
  await page.getByRole("button", { name: "提交自取订单", exact: true }).click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0].body).toMatchObject({
    contact_phone: "13812345678",
    note: "餐具一套",
    items: [
      {
        product_id: 770,
        quantity: 1,
        portions: [{ options: { 辣度: "微辣" }, note: "原口味" }],
      },
    ],
  });
  expect((await readCart(page))[88][0].portionKeys).toEqual(["other-stall"]);
  await assertNoHorizontalOverflow(page);
});

for (const mode of ["live", "simulation"])
  test(`delivery drafts remain explicit when capability changes: ${mode}`, async ({
    page,
  }) => {
    const { detail, writes } = await fixture(page, true);
    detail.delivery.available = true;
    detail.delivery.enabled = true;
    detail.delivery.mode = mode;
    detail.delivery.points = [
      { id: 9, name: "东门交接点", address: "东门蓝色雨棚旁" },
    ];
    detail.wechat_payment.available = true;
    detail.wechat_payment.mode = mode;
    await page.setViewportSize({ width: 360, height: 844 });
    await page.goto("/checkout/77");
    await page.getByRole("button", { name: /商家配送/ }).click();
    if (mode === "simulation") {
      await expect(
        page.getByRole("heading", { name: "模拟微信支付", exact: false }),
      ).toBeVisible();
      await expect(page.locator(".simulation-notice")).toContainText(
        "不会真实扣款或安排送货",
      );
    } else await expect(page.locator(".simulation-notice")).toHaveCount(0);
    await expect(
      page.getByRole("textbox", { name: "收餐人称呼", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("textbox", { name: "收餐人称呼", exact: true })
      .fill("小陈");
    await page.getByRole("textbox", { name: /联系手机号/ }).fill("13812345678");
    await page.getByRole("textbox", { name: /整单备注/ }).fill("到点电话联系");
    await page
      .getByRole("combobox", { name: "校园交接点", exact: true })
      .selectOption("9");
    await expect(
      page.getByRole("button", { name: "提交配送订单，去付款", exact: true }),
    ).toBeEnabled();
    await expect(page.locator(".checkout-summary")).toContainText("¥10");
    await page
      .getByRole("link", { name: "查看椒盐鸡蛋煎饼详情", exact: true })
      .click();
    detail.delivery.available = false;
    detail.delivery.reason = "当前配送容量已满";
    await page.getByRole("link", { name: "去结算", exact: true }).click();
    await expect(
      page.getByRole("button", { name: /商家配送/ }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(
      page
        .locator(".delivery-unavailable")
        .getByText("当前配送容量已满", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("textbox", { name: "收餐人称呼", exact: true }),
    ).toHaveValue("小陈");
    await expect(page.getByRole("textbox", { name: /联系手机号/ })).toHaveValue(
      "13812345678",
    );
    await expect(page.getByRole("textbox", { name: /整单备注/ })).toHaveValue(
      "到点电话联系",
    );
    await expect(
      page.getByRole("button", { name: "提交配送订单，去付款", exact: true }),
    ).toBeDisabled();
    await page.getByRole("button", { name: /到摊自取/ }).click();
    await expect(
      page.getByRole("button", { name: "提交自取订单", exact: true }),
    ).toBeEnabled();
    if (mode === "live") {
      await expect(page.getByRole("button", { name: /商家配送/ })).toHaveCount(
        0,
      );
      await page
        .getByRole("link", { name: "查看椒盐鸡蛋煎饼详情", exact: true })
        .click();
      detail.delivery.available = true;
      await page.getByRole("link", { name: "去结算", exact: true }).click();
    }
    await page.getByRole("button", { name: /商家配送/ }).click();
    await expect(
      page.getByRole("textbox", { name: "收餐人称呼", exact: true }),
    ).toHaveValue("小陈");
    await expect(
      page.getByRole("combobox", { name: "校园交接点", exact: true }),
    ).toHaveValue("9");
    expect(writes).toEqual([]);
    await assertNoHorizontalOverflow(page);
  });

test("adding the tenth portion advances to checkout without permitting an eleventh", async ({
  page,
}) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/stalls/77/products/770");
  for (let i = 1; i < 10; i++)
    await page.getByRole("button", { name: "增加份数", exact: true }).click();
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  await expect(
    page.getByRole("link", { name: "去结算", exact: true }),
  ).toHaveClass(/btn-primary/);
  await expect(
    page.getByRole("button", { name: "再加这道餐点", exact: true }),
  ).toHaveCount(0);
  await page
    .locator(".dish-purchase-panel")
    .evaluate((form) => (form as HTMLFormElement).requestSubmit());
  expect((await readCart(page))[77][0].quantity).toBe(10);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "每单合计最多 10 份", exact: true }),
  ).toBeDisabled();
  expect((await readCart(page))[77][0].quantity).toBe(10);
});
