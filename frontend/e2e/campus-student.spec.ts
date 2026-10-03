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
    user,
    feedbackWrites: [] as any[],
    feedbackBehavior: "success" as "success" | "lose",
    publicReads: 0,
    csrfFail: false,
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
      merchant_contact_phone: "13900139000",
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
            "yanhuo-cart-v2:user:996",
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
    if (path === "/auth/csrf")
      return state.csrfFail
        ? send({ detail: "安全校验暂不可用" }, 503)
        : fulfillCsrf(route);
    if (path === "/events") return send({});
    if (path === "/orders/active-summary")
      return send({ user_id: user.id, counts: { total: 0 }, order: null });
    if (path === "/stalls/996") {
      state.publicReads++;
      return send(state.stall);
    }
    if (path === "/feedback" && req.method() === "POST") {
      state.feedbackWrites.push(req.postDataJSON());
      return state.feedbackBehavior === "lose"
        ? route.abort("failed")
        : send({
            id: 996,
            detail: "已收到反馈",
            replayed: state.feedbackWrites.length > 1,
          });
    }
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
    JSON.parse(localStorage.getItem("yanhuo-cart-v2:user:996") || "{}"),
  );
}
async function edit(page: Page) {
  await page
    .getByRole("button", { name: "设置双份烤冷面每份口味与备注" })
    .click();
  return page.getByRole("dialog", { name: "双份烤冷面每份口味与备注" });
}

// The mock viewport exercises overlay-keyboard layout without claiming to be a
// physical-device keyboard check. All APIs remain isolated fixture responses.
test("checkout puts meals before fulfillment and payment; submit remains above keyboard at four widths", async ({
  page,
}, info) => {
  await page.addInitScript(() => {
    const viewport = new EventTarget();
    Object.assign(viewport, {
      height: window.innerHeight,
      offsetTop: 0,
      offsetLeft: 0,
      width: window.innerWidth,
      scale: 1,
    });
    Object.defineProperty(window, "visualViewport", {
      value: viewport,
      configurable: true,
    });
  });
  const state = await fixture(page, 2, portions);
  await page.goto("/checkout/996");
  const submit = page.getByRole("button", {
    name: "提交自取订单",
    exact: true,
  });
  await expect(submit).toBeEnabled();
  expect(
    await page
      .locator(".checkout-main > section")
      .evaluateAll((nodes) => nodes.map((n) => n.className)),
  ).toEqual([
    "card checkout-card",
    "card fulfillment-card",
    "card checkout-payment-methods",
    "card checkout-card checkout-contact-card",
  ]);
  await expect(
    page.getByText("第 1 份 · 辣度：不辣 · 不要香菜", { exact: true }),
  ).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    await assertNoHorizontalOverflow(page);
    if (width <= 768) {
      await page.evaluate(() => window.scrollTo(0, 0));
      const box = await submit.boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.y + box!.height).toBeLessThanOrEqual(960);
      expect(
        await page
          .locator(".checkout-product")
          .first()
          .evaluate((e) => e.getBoundingClientRect().top),
      ).toBeLessThan(600);
    }
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`checkout-priority-${width}.png`),
        fullPage: true,
      });
  }
  await page.setViewportSize({ width: 390, height: 960 });
  const note = page.getByPlaceholder(
    "例如：餐具按需提供（每份口味请在上方分别填写）",
  );
  await note.fill("不要一次性餐具");
  await page.evaluate(() => {
    Object.assign(window.visualViewport!, { height: 440, offsetTop: 0 });
    window.visualViewport!.dispatchEvent(new Event("resize"));
  });
  await expect
    .poll(async () =>
      Math.round(
        (await submit.boundingBox())!.y + (await submit.boundingBox())!.height,
      ),
    )
    .toBeLessThanOrEqual(440);
  await expect
    .poll(async () =>
      Math.round(
        (await note.boundingBox())!.y + (await note.boundingBox())!.height,
      ),
    )
    .toBeLessThan(360);
  await expect(submit).toBeEnabled();
  await page.screenshot({
    path: info.outputPath("checkout-keyboard-contract.png"),
  });
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("paused product blocks a new checkout without losing its selected portions", async ({
  page,
}) => {
  const state = await fixture(page, 2, portions);
  (state.stall.products[0] as any).sale_paused = true;
  await page.goto("/checkout/996");
  await expect(
    page.getByText("商家暂停了这道餐点的销售，请移除或稍后再来。"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "提交自取订单", exact: true }),
  ).toBeDisabled();
  expect((await cartData(page))[996][0].portions).toEqual(portions);
  expect(state.writes).toEqual([]);
});

async function seedManyMeals(
  page: Page,
  state: Awaited<ReturnType<typeof fixture>>,
) {
  state.stall.products = Array.from({ length: 5 }, (_, index) => ({
    ...structuredClone(product),
    id: 996 + index,
    name: `餐点${index + 1}`,
  }));
  await page.addInitScript(
    ({ products, portions }) => {
      if (sessionStorage.getItem("many-meals-seeded")) return;
      localStorage.setItem(
        "yanhuo-cart-v2:user:996",
        JSON.stringify({
          996: products.map((product, index) => ({
            product,
            quantity: index + 1,
            portions: Array.from(
              { length: index + 1 },
              (_, i) => portions[i % portions.length],
            ),
          })),
        }),
      );
      sessionStorage.setItem("many-meals-seeded", "yes");
    },
    { products: state.stall.products, portions },
  );
}

test("many checkout meals collapse into a quantity summary without changing drafts or totals", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await seedManyMeals(page, state);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/checkout/996");
  await expect(page.locator(".checkout-product")).toHaveCount(2);
  await expect(
    page.getByText("另有 3 种餐点，共 12 份；逐份口味与备注均已保留。"),
  ).toBeVisible();
  const before = await cartData(page);
  const total = await page.locator(".mobile-checkout-total").innerText();
  const expand = page.getByRole("button", {
    name: "展开全部 5 种餐点与逐份要求",
  });
  expect((await expand.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await page.screenshot({
    path: info.outputPath("checkout-many-summary-390.png"),
    fullPage: true,
  });
  await expand.click();
  await expect(page.locator(".checkout-product")).toHaveCount(5);
  await expect(page.locator(".checkout-product").last()).toContainText(
    "辣度：加辣 · 单独装",
  );
  await page.getByRole("button", { name: "收起餐点明细" }).click();
  await expect(page.locator(".checkout-product")).toHaveCount(2);
  expect(await cartData(page)).toEqual(before);
  expect(await page.locator(".mobile-checkout-total").innerText()).toBe(total);
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("a hidden checkout meal with stock pause price or taste changes always expands for review", async ({
  page,
}) => {
  const state = await fixture(page);
  await seedManyMeals(page, state);
  const original = structuredClone(state.stall.products[4]);
  for (const issue of ["stock", "paused", "taste", "price"]) {
    state.stall.products[4] = structuredClone(original);
    const changed = state.stall.products[4] as any;
    if (issue === "stock") changed.stock = 1;
    if (issue === "paused") changed.sale_paused = true;
    if (issue === "price") changed.price_cents = 1500;
    if (issue === "taste") changed.taste_options[0].choices = ["微辣"];
    await page.goto("/checkout/996");
    await expect(page.locator(".checkout-product")).toHaveCount(5);
    await expect(
      page.getByText("餐点信息有变化，已展开全部餐点，请核对提示。"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "收起餐点明细" }),
    ).toHaveCount(0);
    const last = page.locator(".checkout-product").last();
    const message = {
      stock: "目前只剩 1 份",
      paused: "商家暂停了",
      price: "当前标价已变为 ¥15",
      taste: "口味选项已变更，请重新选择后再结算。",
    }[issue]!;
    await expect(last).toContainText(message);
    expect((await cartData(page))[996][4].product.price_cents).toBe(1200);
    expect((await cartData(page))[996][4].quantity).toBe(5);
    if (issue === "price") {
      await page.route("**/api/v1/orders", (route) =>
        route.fulfill({
          status: 409,
          json: { code: "price_changed", detail: "价格已更新，请重新确认。" },
        }),
      );
      await page
        .getByRole("button", { name: "提交自取订单", exact: true })
        .click();
      await expect(
        page.getByText(
          "商家刚刚更新了商品价格。已显示最新金额，请核对后再次提交。",
          { exact: true },
        ),
      ).toBeVisible();
      expect((await cartData(page))[996][4].product.price_cents).toBe(1500);
      await expect(page.locator(".checkout-product")).toHaveCount(5);
      await expect(
        page.getByText("餐点信息有变化，已展开全部餐点，请核对提示。"),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "收起餐点明细" }),
      ).toHaveCount(0);
    }
    expect(state.writes).toEqual([]);
    expect(state.unexpected).toEqual([]);
  }
});

for (const code of [
  "product_sale_paused",
  "prep_capacity_reached",
  "ordering_stopped",
]) {
  test(`server ${code} definitively rejects an uncertain new order without discarding its meal draft`, async ({
    page,
  }) => {
    const state = await fixture(page, 2, portions);
    const requests: any[] = [];
    await page.route("**/api/v1/orders", async (route) => {
      requests.push(route.request().postDataJSON());
      return requests.length === 1
        ? route.abort("failed")
        : route.fulfill({
            status: 409,
            json: { code, detail: "商家暂时无法接下这笔新订单，请稍后再试。" },
          });
    });
    await page.goto("/checkout/996");
    await page
      .getByRole("button", { name: "提交自取订单", exact: true })
      .click();
    await expect(page.locator(".submission-recovery")).toBeVisible();
    await page
      .getByRole("button", { name: "确认原订单结果", exact: true })
      .click();
    await expect(page.locator(".submission-recovery")).toHaveCount(0);
    await expect(
      page.getByText("商家暂时无法接下这笔新订单，请稍后再试。", {
        exact: true,
      }),
    ).toBeVisible();
    expect(requests).toHaveLength(2);
    expect(requests[1]).toEqual(requests[0]);
    expect((await cartData(page))[996][0].portions).toEqual(portions);
    expect(state.unexpected).toEqual([]);
  });
}

test("ready pickup code and order contact lead payment details without reading a public stall", async ({
  page,
}, info) => {
  const state = await fixture(page);
  state.order.status = "ready";
  state.order.payment_status = "paid";
  state.order.location_changed = true;
  state.order.current_address = "临时迁到北门";
  await page.goto("/orders/taste-order");
  const card = page.getByRole("region", { name: "到摊取餐凭证" });
  await expect(card).toBeVisible();
  await expect(card.locator(".pickup-code")).toContainText("12345678");
  await expect(card).toContainText("南门外绿色棚顶");
  await expect(card).not.toContainText("临时迁到北门");
  await expect(
    card.getByRole("link", { name: "联系商家", exact: true }),
  ).toHaveAttribute("href", "tel:13900139000");
  expect(state.publicReads).toBe(0);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    await assertNoHorizontalOverflow(page);
    const first = await card.boundingBox(),
      payment = await page.locator(".student-payment").boundingBox();
    if (payment) expect(first!.y).toBeLessThan(payment.y);
    if (width <= 390)
      expect(
        (await card.locator(".pickup-code").boundingBox())!.y,
      ).toBeLessThan(600);
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`pickup-first-${width}.png`),
        fullPage: true,
      });
  }
  expect(state.unexpected).toEqual([]);
});

test("an unpaid ready pickup still offers its existing cancellation request", async ({
  page,
}, info) => {
  const state = await fixture(page);
  state.order.status = "ready";
  state.order.payment_status = "unpaid";
  await page.goto("/orders/taste-order");
  await expect(
    page.getByRole("region", { name: "到摊取餐凭证" }),
  ).toBeVisible();
  const card = page.getByRole("region", { name: "到摊取餐凭证" });
  await expect(card).toContainText("应付 ¥24 · 请在下方支付或到摊付款");
  await page.setViewportSize({ width: 390, height: 844 });
  const amount = await card.locator(".pickup-payment").boundingBox();
  expect(amount!.y + amount!.height).toBeLessThan(844);
  await page.screenshot({
    path: info.outputPath("pickup-unpaid-390.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "申请取消", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

for (const blocked of [
  "cancel",
  "pending-payment",
  "reconcile",
  "refunding",
  "refunded",
  "manual-review",
  "delivery",
])
  test(`pickup receipt is suppressed for ${blocked}`, async ({ page }) => {
    const state = await fixture(page);
    state.order.status = "ready";
    if (blocked === "cancel") state.order.cancel_requested = true;
    if (blocked === "pending-payment")
      state.order.payment = {
        status: "pending",
        id: "pay",
        expires_at: new Date(Date.now() + 600000).toISOString(),
      };
    if (blocked === "reconcile")
      state.order.payment = { status: "reconcile", id: "pay" };
    if (blocked === "refunding") {
      state.order.payment_status = "refunding";
      state.order.refund = { status: "processing" };
    }
    if (blocked === "refunded") state.order.payment_status = "refunded";
    if (blocked === "manual-review") state.order.payment_review_required = true;
    if (blocked === "delivery") state.order.fulfillment_type = "delivery";
    await page.goto("/orders/taste-order");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator(".pickup-card")).toHaveCount(0);
    await expect(page.locator(".pickup-code")).toHaveCount(0);
    expect(state.writes).toEqual([]);
  });

test("missing contact sends owner-order feedback to manual support and retries the same uncertain payload", async ({
  page,
}) => {
  const state = await fixture(page);
  state.order.status = "ready";
  state.order.merchant_contact_phone = "";
  state.feedbackBehavior = "lose";
  await page.goto("/orders/taste-order");
  await expect(
    page.getByText("商家暂未提供联系电话。", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "联系不上商家？反馈订单问题", exact: true })
    .click();
  let dialog = page.getByRole("dialog", { name: "把这笔订单的问题告诉我们" });
  await dialog.getByLabel("遇到了什么问题").fill("到下单地址没有找到商家");
  await dialog
    .getByPlaceholder("可填写电话号码或其他联系方式")
    .fill("13800138000");
  await dialog
    .getByRole("button", { name: "提交给运营人工处理", exact: true })
    .click();
  await expect(
    dialog.getByRole("button", { name: "确认原反馈结果" }),
  ).toBeVisible();
  await expect(dialog.getByLabel("遇到了什么问题")).toBeDisabled();
  await page.reload();
  await page
    .getByRole("button", { name: "联系不上商家？反馈订单问题", exact: true })
    .click();
  dialog = page.getByRole("dialog", { name: "把这笔订单的问题告诉我们" });
  state.feedbackBehavior = "success";
  await dialog.getByRole("button", { name: "确认原反馈结果" }).click();
  await expect(page.getByRole("dialog", { name: "反馈已收到" })).toContainText(
    "运营会人工查看",
  );
  expect(state.feedbackWrites).toHaveLength(2);
  expect(state.feedbackWrites[1]).toEqual(state.feedbackWrites[0]);
  expect(state.feedbackWrites[0]).toMatchObject({
    order_id: "taste-order",
    content: "到下单地址没有找到商家",
    contact: "13800138000",
  });
  expect(state.feedbackWrites[0]).not.toHaveProperty("stall_id");
  expect(state.writes).toEqual([]);
  expect(state.publicReads).toBe(0);
});

test("weak order refresh retains the last read pickup address and contact", async ({
  page,
}) => {
  const state = await fixture(page);
  state.order.status = "ready";
  await page.goto("/orders/taste-order");
  const card = page.locator(".pickup-card");
  await expect(card).toBeVisible();
  state.failOrder = true;
  await page.getByRole("button", { name: "刷新订单状态" }).click();
  await expect(card).toContainText("以下为上次读取的信息");
  await expect(card).toContainText("南门外绿色棚顶");
  await expect(
    card.getByRole("link", { name: "联系商家", exact: true }),
  ).toHaveAttribute("href", "tel:13900139000");
  expect(state.publicReads).toBe(0);
  expect(state.writes).toEqual([]);
});

test("copy failure exposes the original pickup address for manual copying", async ({
  page,
}) => {
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    }),
  );
  const state = await fixture(page);
  state.order.status = "ready";
  await page.goto("/orders/taste-order");
  await page.getByRole("button", { name: "复制地址", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "可复制的订单取餐地址" }),
  ).toHaveValue("南门外绿色棚顶");
  expect(state.writes).toEqual([]);
});
