import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// All API calls are fixture responses. These fault scenarios never write to the
// running development database or any payment service.
const product = {
  id: 989,
  name: "恢复验证烤冷面",
  description: "隔离测试餐点",
  image: "/images/food-cold-noodles.jpg",
  price_cents: 1600,
  stock: 50,
};
const note = "例如：餐具按需提供（每份口味请在上方分别填写）";
const keyName = "yanhuo-checkout-989-989";
async function fixture(page: Page) {
  const state = {
    user: {
      id: 989,
      username: "recovery_a",
      display_name: "恢复同学",
      is_merchant: false,
      is_staff: false,
    },
    stall: {
      id: 989,
      name: "恢复测试小摊",
      products: [{ ...product }],
      image: product.image,
      description: "隔离测试",
      area_name: "校园",
      category: "小吃",
      address: "南门",
      status: "open",
      can_order: true,
      transaction_enabled: true,
      prep_minutes: 10,
      reviews: [],
      review_count: 0,
      rating: 0,
      contact_phone: "",
      wechat_payment: { available: false, channels: [], reason: "尚未开通" },
    },
    stallUnavailable: false,
    failCsrf: false,
    rejectRetryStatus: 0,
    behavior: "lose" as "lose" | "500" | "400" | "price" | "success" | "hold",
    writes: [] as any[],
    committed: new Map<string, any>(),
    unexpected: [] as string[],
    release: () => {},
  };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (data: any, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    if (path === "/auth/csrf")
      return state.failCsrf
        ? send({ detail: "安全校验暂时无法加载" }, 503)
        : fulfillCsrf(route);
    if (path === "/config")
      return send({
        user: state.user,
        areas: [],
        demo_mode: true,
        amap_key: "",
      });
    if (path === "/auth/me") return send(state.user);
    if (path === "/orders/active-summary")
      return send({
        user_id: state.user.id,
        counts: { total: 0 },
        order: null,
      });
    if (path === "/stalls/989")
      return state.stallUnavailable
        ? send({ code: "not_found", detail: "摊位暂时不可见" }, 404)
        : send(state.stall);
    if (path === "/events") return send({});
    if (path === "/orders" && request.method() === "POST") {
      const body = request.postDataJSON();
      state.writes.push(body);
      if (state.rejectRetryStatus)
        return send(
          { code: "authentication_required", detail: "请重新确认登录状态" },
          state.rejectRetryStatus,
        );
      if (state.behavior === "400")
        return send(
          {
            detail: "请修改联系信息",
            errors: { contact_phone: ["请检查联系信息"] },
          },
          400,
        );
      if (state.behavior === "price") {
        state.stall.products[0]!.price_cents = 1700;
        return send({ code: "price_changed", detail: "餐点价格变化" }, 409);
      }
      const previous = state.committed.get(body.idempotency_key);
      const order = previous || {
        id: `recovered-${state.committed.size + 1}`,
        number: "RECOVERY-001",
        stall_id: 989,
        stall_name: state.stall.name,
        fulfillment_type: body.fulfillment_type || "pickup",
        status: "pending",
        payment_status: "unpaid",
        payment_method: "offline",
        total_cents: body.items.reduce(
          (sum: number, item: any) =>
            sum + item.quantity * item.expected_price_cents,
          0,
        ),
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 300000).toISOString(),
        items: body.items.map((item: any) => ({
          ...product,
          ...item,
          unit_price_cents: item.expected_price_cents,
        })),
        pickup_address: "南门",
        note: body.note,
        wechat_payment: state.stall.wechat_payment,
        review: null,
      };
      state.committed.set(body.idempotency_key, order);
      if (state.behavior === "lose") return route.abort("failed");
      if (state.behavior === "500")
        return send({ detail: "结果暂时无法确认" }, 503);
      if (state.behavior === "hold")
        await new Promise<void>((resolve) => {
          state.release = resolve;
        });
      try {
        return await send(order, previous ? 200 : 201);
      } catch {
        // A navigation/account change can abort the old browser request.
      }
      return;
    }
    if (path === "/orders") return send([...state.committed.values()]);
    if (path.startsWith("/orders/recovered-"))
      return send(
        [...state.committed.values()].find(
          (order) => path === `/orders/${order.id}`,
        ),
      );
    state.unexpected.push(path);
    return send({ detail: "Unexpected fixture request" }, 500);
  });
  await page.goto("/checkout/989");
  await page.evaluate(
    (item) =>
      localStorage.setItem(
        "yanhuo-cart-v1",
        JSON.stringify({ 989: [{ product: item, quantity: 1 }] }),
      ),
    product,
  );
  await page.reload();
  await expect(page.getByPlaceholder(note)).toBeVisible();
  await page.getByPlaceholder(note).fill("少辣，保留原备注");
  return state;
}
async function submit(page: Page) {
  await page.getByRole("button", { name: "提交自取订单", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "上一笔提交结果待确认", exact: true }),
  ).toBeVisible();
}

test("a lost result locks edits and retries the exact original write only once", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await submit(page);
  await expect(page.getByPlaceholder(note)).toBeDisabled();
  await expect(
    page.getByRole("button", { name: `增加${product.name}`, exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "请先确认原订单结果", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".recovery-note")).toHaveText(
    "原备注：少辣，保留原备注",
  );
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    if ([390, 1440].includes(width))
      await page.screenshot({
        path: info.outputPath(`checkout-recovery-${width}.png`),
        fullPage: true,
      });
  }
  state.behavior = "hold";
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect.poll(() => state.writes.length).toBe(2);
  await expect(
    page.getByRole("button", { name: "正在确认…", exact: true }),
  ).toBeDisabled();
  state.release();
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(state.committed.size).toBe(1);
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), keyName),
  ).toBeNull();
  expect(state.unexpected).toEqual([]);
});

test("reload and a changed cart preserve the original recovery request and newer draft", async ({
  page,
}) => {
  const state = await fixture(page);
  await submit(page);
  const saved = await page.evaluate(
    (key) => sessionStorage.getItem(key),
    keyName,
  );
  await page.evaluate(
    (item) =>
      localStorage.setItem(
        "yanhuo-cart-v1",
        JSON.stringify({ 989: [{ product: item, quantity: 2 }] }),
      ),
    product,
  );
  await page.reload();
  await expect(page.getByPlaceholder(note)).toBeDisabled();
  await expect(page.locator(".recovery-summary")).toContainText("× 1");
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), keyName),
  ).toBe(saved);
  state.behavior = "success";
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("yanhuo-cart-v1")!)[989][0].quantity,
    ),
  ).toBe(2);
});

test("an empty cart and unavailable stall do not hide recovery of an existing order", async ({
  page,
}) => {
  const state = await fixture(page);
  await submit(page);
  await page.evaluate(() => localStorage.setItem("yanhuo-cart-v1", "{}"));
  state.stallUnavailable = true;
  state.behavior = "success";
  await page.reload();
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(state.committed.size).toBe(1);
});

test("5xx preserves uncertainty while an explicit validation rejection unlocks correction", async ({
  page,
}) => {
  const state = await fixture(page);
  state.behavior = "500";
  await submit(page);
  await expect(page.getByPlaceholder(note)).toBeDisabled();
  state.behavior = "success";
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(state.committed.size).toBe(1);
});

for (const failure of ["400", "price"] as const) {
  test(`${failure} rejection permits corrected input and a new confirmed submission`, async ({
    page,
  }) => {
    const state = await fixture(page);
    state.behavior = failure;
    await page
      .getByRole("button", { name: "提交自取订单", exact: true })
      .click();
    await expect(page.getByPlaceholder(note)).toBeEnabled();
    await expect(page.locator(".submission-recovery")).toHaveCount(0);
    await page.getByPlaceholder(note).fill("已修正后的备注");
    expect(
      await page.evaluate((key) => sessionStorage.getItem(key), keyName),
    ).toBeNull();
    state.behavior = "success";
    await page
      .getByRole("button", {
        name: failure === "price" ? "确认新价格并提交" : "提交自取订单",
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(/\/orders\/recovered-1$/);
    expect(state.writes[1].idempotency_key).not.toBe(
      state.writes[0].idempotency_key,
    );
    expect(state.writes[1].note).toBe("已修正后的备注");
    expect(state.committed.size).toBe(1);
  });
}

test("recovery is account scoped and returns when the original account signs back in", async ({
  page,
}) => {
  const state = await fixture(page);
  await submit(page);
  state.user = { ...state.user, id: 990, username: "recovery_b" };
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByPlaceholder(note)).toBeEnabled();
  await expect(page.getByPlaceholder(note)).toHaveValue("");
  await expect(page.locator(".submission-recovery")).toHaveCount(0);
  state.user = { ...state.user, id: 989, username: "recovery_a" };
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByPlaceholder(note)).toBeDisabled();
  await expect(page.getByPlaceholder(note)).toHaveValue("少辣，保留原备注");
  state.behavior = "success";
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(state.writes[1]).toEqual(state.writes[0]);
});

test("leaving while the server response is pending retains the original request for recovery", async ({
  page,
}) => {
  const state = await fixture(page);
  state.behavior = "hold";
  await page.getByRole("button", { name: "提交自取订单", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  const saved = await page.evaluate(
    (key) => sessionStorage.getItem(key),
    keyName,
  );
  await page
    .getByRole("link", { name: `查看${product.name}详情`, exact: true })
    .click();
  await expect(page).toHaveURL(/\/stalls\/989\/products\/989$/);
  state.release();
  await page.goto("/checkout/989");
  await expect(page.getByPlaceholder(note)).toBeDisabled();
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), keyName),
  ).toBe(saved);
  state.behavior = "success";
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(state.writes[1]).toEqual(state.writes[0]);
});

test("legacy delivery retry snapshots retain the original fee despite a changed cart", async ({
  page,
}) => {
  const state = await fixture(page);
  const oldCart = JSON.stringify([[989, 1600, 1]]);
  const oldKey = "legacy-delivery-recovery-989";
  const legacy = {
    key: oldKey,
    cart: oldCart,
    fingerprint: JSON.stringify([
      oldCart,
      "原来的配送备注",
      "13800138000",
      "delivery",
      [77, "同学", 250],
    ]),
    note: "原来的配送备注",
    phone: "13800138000",
    fulfillment: "delivery",
    pointId: 77,
    recipient: "同学",
  };
  await page.evaluate(
    ({ item, key, record }) => {
      sessionStorage.setItem(key, JSON.stringify(record));
      localStorage.setItem(
        "yanhuo-cart-v1",
        JSON.stringify({ 989: [{ product: item, quantity: 2 }] }),
      );
    },
    { item: product, key: keyName, record: legacy },
  );
  await page.reload();
  await expect(page.locator(".recovery-summary")).toContainText("18.5");
  state.behavior = "success";
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]).toEqual({
    stall_id: 989,
    items: [{ product_id: 989, quantity: 1, expected_price_cents: 1600 }],
    note: "原来的配送备注",
    contact_phone: "13800138000",
    fulfillment_type: "delivery",
    delivery_point_id: 77,
    recipient_name: "同学",
    expected_delivery_fee_cents: 250,
    idempotency_key: oldKey,
  });
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("yanhuo-cart-v1")!)[989][0].quantity,
    ),
  ).toBe(2);
});

test("an old account response cannot clear the new account's pending checkout", async ({
  page,
}) => {
  const state = await fixture(page);
  state.behavior = "hold";
  await page.getByRole("button", { name: "提交自取订单", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  const releaseFirst = state.release;
  const firstRecord = await page.evaluate(
    (key) => sessionStorage.getItem(key),
    keyName,
  );
  state.user = { ...state.user, id: 990, username: "recovery_b" };
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByPlaceholder(note)).toBeEnabled();
  await page.getByPlaceholder(note).fill("新账号的备注");
  state.behavior = "lose";
  await submit(page);
  const secondKey = "yanhuo-checkout-990-989";
  const secondRecord = await page.evaluate(
    (key) => sessionStorage.getItem(key),
    secondKey,
  );
  releaseFirst();
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  await expect(page).toHaveURL(/\/checkout\/989$/);
  await expect(page.getByPlaceholder(note)).toHaveValue("新账号的备注");
  await expect(page.getByPlaceholder(note)).toBeDisabled();
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), keyName),
  ).toBe(firstRecord);
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), secondKey),
  ).toBe(secondRecord);
});

test("a failed CSRF preflight during recovery cannot discard an already committed original order", async ({
  page,
  context,
}) => {
  const state = await fixture(page);
  await submit(page);
  const originalRecord = await page.evaluate(
    (key) => sessionStorage.getItem(key),
    keyName,
  );
  await context.clearCookies();
  state.failCsrf = true;
  state.behavior = "success";
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "确认原订单结果", exact: true }),
  ).toBeEnabled();
  await expect(page.getByPlaceholder(note)).toBeDisabled();
  expect(state.writes).toHaveLength(1);
  expect(
    await page.evaluate((key) => sessionStorage.getItem(key), keyName),
  ).toBe(originalRecord);
  state.failCsrf = false;
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(state.committed.size).toBe(1);
});

test("authentication and proxy rejections during recovery preserve the original submission", async ({
  page,
}) => {
  const state = await fixture(page);
  await submit(page);
  const originalRecord = await page.evaluate(
    (key) => sessionStorage.getItem(key),
    keyName,
  );
  state.behavior = "success";
  for (const status of [403, 408, 429]) {
    state.rejectRetryStatus = status;
    await page
      .getByRole("button", { name: "确认原订单结果", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "确认原订单结果", exact: true }),
    ).toBeEnabled();
    await expect(page.getByPlaceholder(note)).toBeDisabled();
    expect(
      await page.evaluate((key) => sessionStorage.getItem(key), keyName),
    ).toBe(originalRecord);
  }
  state.rejectRetryStatus = 0;
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/\/orders\/recovered-1$/);
  expect(
    state.writes.every(
      (body) => JSON.stringify(body) === JSON.stringify(state.writes[0]),
    ),
  ).toBeTruthy();
  expect(state.committed.size).toBe(1);
});
