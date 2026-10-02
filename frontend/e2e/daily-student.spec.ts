import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";
const product = {
  id: 996,
  name: "双份烤冷面",
  description: "现做烤冷面",
  image: "/images/food-cold-noodles.jpg",
  price_cents: 1200,
  stock: 30,
  taste_options: [{ name: "辣度", choices: ["不辣", "微辣", "加辣"] }],
};
const portions = [
  { options: { 辣度: "不辣" }, note: "不要香菜" },
  { options: { 辣度: "加辣" }, note: "单独装" },
];
async function fixture(page: Page, quantity = 0, initialPortions?: any[]) {
  const user = {
    id: 996,
    username: "taste_student",
    display_name: "测试同学",
    is_merchant: false,
    is_staff: false,
  };
  const state = {
    stall: {
      id: 996,
      name: "口味小摊",
      image: product.image,
      description: "校园测试",
      area_id: 1,
      area_name: "南门",
      category: "小吃",
      address: "南门外绿色棚顶",
      latitude: 45.7,
      longitude: 126.6,
      status: "open",
      can_order: true,
      transaction_enabled: true,
      prep_minutes: 12,
      last_confirmed_at: new Date().toISOString(),
      closes_at: "",
      rating: 0,
      review_count: 0,
      reviews: [],
      products: [structuredClone(product)],
      contact_phone: "13800138000",
      wechat_payment: { available: false, channels: [], reason: "未开通" },
    },
    order: {
      id: "taste-order",
      number: "TASTE-001",
      stall_id: 996,
      stall_name: "口味小摊",
      status: "preparing",
      fulfillment_type: "pickup",
      payment_status: "unpaid",
      payment_method: "offline",
      total_cents: 2400,
      created_at: new Date().toISOString(),
      accepted_at: new Date().toISOString(),
      estimated_ready_at: new Date(Date.now() + 600000).toISOString(),
      prep_updated_at: new Date().toISOString(),
      prep_delay_reason: "",
      pickup_code: "12345678",
      pickup_address: "南门外绿色棚顶",
      pickup_latitude: 45.7,
      pickup_longitude: 126.6,
      contact_phone: "",
      note: "",
      cancel_requested: false,
      cancel_reason: "",
      review: null,
      items: [
        {
          product_id: 996,
          name: product.name,
          image: product.image,
          unit_price_cents: 1200,
          quantity: 2,
          portions: structuredClone(portions),
        },
      ],
    } as any,
    writes: [] as any[],
    behavior: "success" as "success" | "lose" | "tastes",
    failOrder: false,
    unexpected: [] as string[],
  };
  if (quantity)
    await page.addInitScript(
      ({ product, quantity, initialPortions }) => {
        if (!sessionStorage.getItem("taste-fixture-seeded")) {
          localStorage.setItem(
            "yanhuo-cart-v1",
            JSON.stringify({
              996: [
                {
                  product,
                  quantity,
                  ...(initialPortions ? { portions: initialPortions } : {}),
                },
              ],
            }),
          );
          sessionStorage.setItem("taste-fixture-seeded", "yes");
        }
      },
      { product, quantity, initialPortions },
    );
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    const send = (value: any, status = 200) =>
      route.fulfill({ status, json: value });
    if (path === "/config")
      return send({ user, areas: [], demo_mode: true, amap_key: "" });
    if (path === "/auth/me") return send(user);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/events") return send({});
    if (path === "/orders/active-summary")
      return send({ user_id: user.id, counts: { total: 0 }, order: null });
    if (path === "/stalls/996") return send(state.stall);
    if (path === "/orders/taste-order")
      return state.failOrder ? route.abort("failed") : send(state.order);
    if (path === "/orders" && req.method() === "POST") {
      const body = req.postDataJSON();
      state.writes.push(body);
      if (state.behavior === "tastes") {
        state.stall.products[0].taste_options = [
          { name: "辣度", choices: ["微辣"] },
        ];
        return send(
          {
            code: "tastes_changed",
            detail: "商品口味选项已更新，请重新选择后提交。",
            product_id: 996,
          },
          409,
        );
      }
      state.order = {
        ...state.order,
        status: "pending",
        note: body.note,
        items: body.items.map((item: any) => ({
          ...item,
          name: product.name,
          image: product.image,
          unit_price_cents: item.expected_price_cents,
        })),
      };
      return state.behavior === "lose"
        ? route.abort("failed")
        : send(state.order, 201);
    }
    state.unexpected.push(`${req.method()} ${path}`);
    return send({ detail: "Unexpected test request" }, 500);
  });
  return state;
}
async function cartData(page: Page) {
  return page.evaluate(() =>
    JSON.parse(localStorage.getItem("yanhuo-cart-v1") || "{}"),
  );
}
async function edit(page: Page) {
  await page
    .getByRole("button", { name: "设置双份烤冷面每份口味与备注" })
    .click();
  return page.getByRole("dialog", { name: "双份烤冷面每份口味与备注" });
}

test("two portions can choose distinct tastes and notes on product detail, all four widths", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto("/stalls/996/products/996");
  await page.getByRole("button", { name: "增加份数", exact: true }).click();
  const dialog = await edit(page);
  await dialog
    .getByRole("group", { name: "第1份", exact: true })
    .getByRole("combobox")
    .selectOption("不辣");
  await dialog
    .getByRole("group", { name: "第1份", exact: true })
    .getByRole("textbox")
    .fill("不要香菜");
  await dialog
    .getByRole("group", { name: "第2份", exact: true })
    .getByRole("combobox")
    .selectOption("加辣");
  await dialog
    .getByRole("group", { name: "第2份", exact: true })
    .getByRole("textbox")
    .fill("单独装");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    await assertNoHorizontalOverflow(page);
    expect(
      await dialog.evaluate((e) => e.scrollWidth - e.clientWidth),
    ).toBeLessThanOrEqual(1);
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`taste-editor-${width}.png`),
        fullPage: true,
      });
  }
  await dialog.getByRole("button", { name: "保存口味与备注" }).click();
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  expect((await cartData(page))[996][0]).toMatchObject({
    quantity: 2,
    portions,
  });
  expect(state.unexpected).toEqual([]);
});
test("old quantity-only draft remains usable and quantity edits preserve the first portions", async ({
  page,
}) => {
  await fixture(page, 2);
  await page.goto("/cart");
  const dialog = await edit(page);
  await dialog
    .getByRole("group", { name: "第1份", exact: true })
    .getByRole("combobox")
    .selectOption("不辣");
  await dialog
    .getByRole("group", { name: "第2份", exact: true })
    .getByRole("combobox")
    .selectOption("加辣");
  await dialog.getByRole("button", { name: "保存口味与备注" }).click();
  await page
    .getByRole("button", { name: "增加双份烤冷面", exact: true })
    .click();
  expect((await cartData(page))[996][0].portions).toEqual([
    { options: { 辣度: "不辣" }, note: "" },
    { options: { 辣度: "加辣" }, note: "" },
    { options: {}, note: "" },
  ]);
  await page
    .getByRole("button", { name: "减少双份烤冷面", exact: true })
    .click();
  await page
    .getByRole("button", { name: "减少双份烤冷面", exact: true })
    .click();
  await page.reload();
  expect((await cartData(page))[996][0].portions).toEqual([
    { options: { 辣度: "不辣" }, note: "" },
  ]);
  await expect(
    page.getByRole("link", { name: /去结算口味小摊/ }),
  ).toBeVisible();
});
test("remove and undo preserves per-portion requests", async ({ page }) => {
  await fixture(page, 2, portions);
  await page.goto("/cart");
  await page.getByRole("button", { name: "移除双份烤冷面" }).click();
  await page.getByRole("button", { name: /撤销/ }).click();
  expect((await cartData(page))[996][0].portions).toEqual(portions);
});
test("checkout submits exact portion snapshot and renders it on receipt", async ({
  page,
}, info) => {
  const state = await fixture(page, 2, portions);
  await page.goto("/checkout/996");
  await expect(
    page.getByRole("button", { name: "提交自取订单" }),
  ).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    await assertNoHorizontalOverflow(page);
    if (width === 390)
      await page.screenshot({
        path: info.outputPath("taste-checkout-390.png"),
        fullPage: true,
      });
  }
  await page.getByRole("button", { name: "提交自取订单" }).click();
  await expect(page).toHaveURL(/orders\/taste-order$/);
  expect(state.writes[0].items).toEqual([
    { product_id: 996, quantity: 2, expected_price_cents: 1200, portions },
  ]);
  await expect(
    page.getByText("第 1 份 · 辣度：不辣 · 不要香菜", { exact: true }),
  ).toBeVisible();
  expect(state.unexpected).toEqual([]);
});
test("unknown checkout retry keeps original tastes even after newer local cart changes", async ({
  page,
}) => {
  const state = await fixture(page, 2, portions);
  state.behavior = "lose";
  await page.goto("/checkout/996");
  await page.getByRole("button", { name: "提交自取订单" }).click();
  await expect(
    page.getByRole("button", { name: "设置双份烤冷面每份口味与备注" }),
  ).toBeDisabled();
  await page.evaluate(() => {
    const data = JSON.parse(localStorage.getItem("yanhuo-cart-v1")!);
    data[996][0].portions[0].note = "后来选的，不应清除";
    localStorage.setItem("yanhuo-cart-v1", JSON.stringify(data));
  });
  await page.reload();
  state.behavior = "success";
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/orders\/taste-order$/);
  expect(state.writes).toHaveLength(2);
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect((await cartData(page))[996][0].portions[0].note).toBe(
    "后来选的，不应清除",
  );
});
test("server taste change rejection unlocks editing and requires removing old choice", async ({
  page,
}) => {
  const state = await fixture(page, 2, portions);
  state.behavior = "tastes";
  await page.goto("/checkout/996");
  await page.getByRole("button", { name: "提交自取订单" }).click();
  await expect(
    page.getByText("口味选项已变更，请重新选择后再结算。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "提交自取订单" }),
  ).toBeDisabled();
  const dialog = await edit(page);
  await expect(
    dialog.getByRole("button", { name: "保存口味与备注" }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "移除原选" }).first().click();
  await dialog.getByRole("button", { name: "移除原选" }).first().click();
  await dialog
    .getByRole("group", { name: "第1份", exact: true })
    .getByRole("combobox")
    .selectOption("微辣");
  await dialog.getByRole("button", { name: "保存口味与备注" }).click();
  state.behavior = "success";
  await page.getByRole("button", { name: "提交自取订单" }).click();
  await expect(page).toHaveURL(/orders\/taste-order$/);
  expect(state.writes[1].items[0].portions[0].options).toEqual({
    辣度: "微辣",
  });
  expect(state.writes[1].idempotency_key).not.toBe(
    state.writes[0].idempotency_key,
  );
});
test("reorder preserves historical tastes and flags removed choices before checkout", async ({
  page,
}) => {
  const state = await fixture(page);
  state.order.status = "completed";
  state.stall.products[0].taste_options = [{ name: "辣度", choices: ["微辣"] }];
  await page.goto("/orders/taste-order");
  await page.getByRole("button", { name: /再来一单/ }).click();
  const dialog = page.getByRole("dialog", { name: "再来一单" });
  await expect(dialog.getByText(/原选口味已变更/)).toBeVisible();
  await dialog.getByRole("button", { name: "确认加入餐袋" }).click();
  await expect(page).toHaveURL(/cart$/);
  expect((await cartData(page))[996][0].portions).toEqual(portions);
  await expect(page.getByRole("button", { name: "暂不可结算" })).toBeDisabled();
  await expect(
    page.getByText("口味选项已变更，请重新选择后再结算。", { exact: true }),
  ).toBeVisible();
});
for (const kind of ["estimated", "missing", "overdue", "ready", "delivery"]) {
  test(`preparation ${kind} is truthful and fits four widths`, async ({
    page,
  }, info) => {
    const state = await fixture(page);
    if (kind === "missing") state.order.estimated_ready_at = null;
    if (kind === "overdue") {
      state.order.estimated_ready_at = new Date(
        Date.now() - 600000,
      ).toISOString();
      state.order.prep_delay_reason = "前面两单正在收尾，再等一会儿";
    }
    if (kind === "ready") state.order.status = "ready";
    if (kind === "delivery") state.order.fulfillment_type = "delivery";
    await page.goto("/orders/taste-order");
    const card = page.locator(kind === "ready" ? ".pickup-card" : ".prep-card");
    await expect(card).toBeVisible();
    if (kind === "missing")
      await expect(card).toContainText("商家尚未提供预计出餐时间");
    if (kind === "overdue") {
      await expect(card).toContainText("尚未收到商家出餐确认");
      await expect(card).toContainText(state.order.prep_delay_reason);
      await expect(page.locator(".pickup-code")).toHaveCount(0);
    }
    if (kind === "ready")
      await expect(card).toContainText("餐点已做好");
    if (kind === "delivery")
      await expect(card).toContainText("出餐时间不等于送达时间");
    if (kind === "estimated") await expect(card).toContainText("商家预计");
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 960 });
      await assertNoHorizontalOverflow(page);
      if (kind === "overdue" && (width === 390 || width === 1440))
        await page.screenshot({
          path: info.outputPath(`preparation-${width}.png`),
          fullPage: true,
        });
    }
    expect(state.writes).toEqual([]);
    expect(state.unexpected).toEqual([]);
  });
}
test("stale preparation read shows unsynced warning without advancing order", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/orders/taste-order");
  await expect(page.locator(".prep-card")).toBeVisible();
  state.failOrder = true;
  await page.getByRole("button", { name: "刷新订单状态" }).click();
  await expect(page.locator(".prep-card")).toContainText("订单暂未同步");
  await expect(page.locator(".pickup-code")).toHaveCount(0);
  expect(state.writes).toEqual([]);
});

test("ready food under unresolved refund never invites collection", async ({
  page,
}) => {
  const state = await fixture(page);
  state.order.status = "ready";
  state.order.refund = { status: "processing" };
  state.order.payment_status = "refunding";
  await page.goto("/orders/taste-order");
  await expect(
    page.getByRole("heading", { name: "退款处理中，请留意结果" }),
  ).toBeVisible();
  await expect(page.locator(".prep-card.ready")).toHaveCount(0);
  await expect(page.locator(".pickup-code")).toHaveCount(0);
  expect(state.writes).toEqual([]);
});
