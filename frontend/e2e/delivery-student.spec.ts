import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Isolated UI contracts: no live stock, customer data, delivery or payment API.
const user = {
  id: 997,
  username: "delivery_ui_fixture",
  display_name: "配送界面测试",
  is_merchant: false,
  is_staff: false,
};
const product = {
  id: 997,
  name: "招牌烤冷面",
  description: "仅用于隔离测试",
  image: "/images/food-cold-noodles.jpg",
  price_cents: 1800,
  stock: 30,
  is_active: true,
};
const point = {
  id: 71,
  name: "图书馆交接点",
  address: "图书馆东侧指定取餐区",
  latitude: 45.75,
  longitude: 126.67,
  area_id: 1,
};
function makeStall(available = true): any {
  return {
    id: 997,
    name: "校园好味道",
    image: product.image,
    address: "校园南门摊位",
    area_name: "测试校园",
    area_id: 1,
    prep_minutes: 10,
    can_order: true,
    transaction_enabled: true,
    products: [product],
    reviews: [],
    contact_phone: "13800138000",
    wechat_payment: {
      available,
      channels: available ? ["native", "h5"] : [],
      reason: "微信支付尚未配置",
    },
    delivery: {
      enabled: true,
      approved: true,
      available,
      reason: available ? "" : "配送需要先完成微信支付，商家尚未开通线上收款。",
      fee_cents: 300,
      min_order_cents: 1500,
      eta_min_minutes: 25,
      eta_max_minutes: 45,
      starts_at: "10:00",
      ends_at: "22:00",
      capacity: 5,
      points: [point],
      point_ids: [point.id],
    },
  };
}
function makeOrder(status = "pending_payment"): any {
  return {
    id: "delivery-ui-order",
    number: "UI-DELIVERY-997",
    stall_id: 997,
    stall_name: "校园好味道",
    fulfillment_type: "delivery",
    status,
    payment_status: status === "pending_payment" ? "unpaid" : "paid",
    payment_method: "wechat",
    payment_review_required: false,
    wechat_payment: makeStall().wechat_payment,
    payment: null,
    refund: null,
    payment_can_close: false,
    total_cents: 2100,
    items_total_cents: 1800,
    delivery_fee_cents: 300,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 900000).toISOString(),
    pickup_code: "82745103",
    pickup_address: "校园南门摊位",
    pickup_latitude: 45.73,
    pickup_longitude: 126.67,
    current_address: "校园南门摊位",
    location_changed: false,
    delivery_point_id: 71,
    delivery_point_name: point.name,
    delivery_point_address: point.address,
    delivery_point_latitude: point.latitude,
    delivery_point_longitude: point.longitude,
    recipient_name: "小林",
    contact_phone: "13800138000",
    delivery_eta_min_at: new Date(Date.now() + 1500000).toISOString(),
    delivery_eta_max_at: new Date(Date.now() + 2700000).toISOString(),
    dispatched_at: ["delivering", "arrived", "completed"].includes(status)
      ? new Date().toISOString()
      : null,
    arrived_at: ["arrived", "completed"].includes(status)
      ? new Date().toISOString()
      : null,
    delivery_issue: "",
    cancel_requested: false,
    cancel_reason: "",
    note: "",
    review: null,
    items: [
      { ...product, product_id: 997, unit_price_cents: 1800, quantity: 1 },
    ],
  };
}
async function fixture(
  page: Page,
  status = "pending_payment",
  available = true,
) {
  const state = {
    stall: makeStall(available),
    order: makeOrder(status),
    writes: [] as { path: string; body: any }[],
    failCreate: "",
    failFee: false,
    unexpected: [] as string[],
  };
  await page.route("https://**/*", (r) => r.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    const send = (data: any, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (!["GET", "HEAD"].includes(req.method())) {
      const body = req.postDataJSON();
      state.writes.push({ path, body });
      if (path === "/orders") {
        if (state.failCreate) {
          state.failCreate = "";
          return route.abort("failed");
        }
        if (state.failFee) {
          state.failFee = false;
          state.stall.delivery.fee_cents = 400;
          return send(
            { code: "delivery_fee_changed", detail: "配送费已更新" },
            409,
          );
        }
        return send(state.order, 201);
      }
      if (path.endsWith("/confirm-receipt")) {
        state.order.status = "completed";
        state.order.completed_at = new Date().toISOString();
        return send(state.order);
      }
      if (path.endsWith("/cancel")) {
        state.order.status = "cancelled";
        state.order.payment_status = "refunding";
        return send(state.order);
      }
      if (path.endsWith("/payments/wechat")) {
        state.order.payment = {
          id: "attempt-ui",
          channel: "native",
          status: "pending",
          code_url: "weixin://wxpay/bizpayurl?pr=deliveryfixture",
          h5_url: "",
          expires_at: new Date(Date.now() + 600000).toISOString(),
          error_message: "",
        };
        state.order.payment_can_close = true;
        return send(state.order);
      }
      if (path.endsWith("/payments/close")) {
        state.order.payment.status = "closed";
        state.order.payment_can_close = false;
        return send(state.order);
      }
      if (path.endsWith("/payments/sync")) return send(state.order);
      state.unexpected.push(path);
      return send({ detail: "Unexpected mutation" }, 500);
    }
    if (path === "/config")
      return send({
        demo_mode: true,
        brand: "烟火地图",
        amap_key: "",
        amap_proxy: "",
        areas: [],
        user,
      });
    if (path === "/auth/me") return send(user);
    if (path === "/orders/active-summary")
      return send({
        user_id: user.id,
        counts: { total: 1 },
        order: {
          id: state.order.id,
          stall_name: state.order.stall_name,
          status: state.order.status,
          fulfillment_type: "delivery",
          cancel_requested: false,
        },
      });
    if (path === "/stalls/997") return send(state.stall);
    if (path === "/orders") return send([state.order]);
    if (path === `/orders/${state.order.id}`) return send(state.order);
    state.unexpected.push(path);
    return send({ detail: "Unexpected read" }, 500);
  });
  return state;
}
async function checkout(page: Page) {
  await page.addInitScript(
    (item) =>
      localStorage.setItem(
        "yanhuo-cart-v2:user:997",
        JSON.stringify({ 997: [{ product: item, quantity: 1 }] }),
      ),
    product,
  );
  await page.goto("/checkout/997");
  await page.getByRole("button", { name: /商家配送/ }).click();
}
async function fillContact(page: Page) {
  await page.getByLabel("校园交接点").selectOption("71");
  await page.getByLabel("收餐人称呼").fill("小林");
  await page.getByLabel(/联系手机号/).fill("13800138000");
}

test("unconfigured delivery is visible with an honest reason and pickup remains usable", async ({
  page,
}, info) => {
  const state = await fixture(page, "pending_payment", false);
  await checkout(page);
  await expect(page.getByText("这家小摊暂时不能配送")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "提交配送订单，去付款", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".checkout-payment-methods")).not.toContainText(
    "到摊付款",
  );
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".fulfillment-card").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("delivery-unavailable-mobile.png"),
  });
  await page.getByRole("button", { name: /到摊自取 免配送费/ }).click();
  await expect(
    page.getByRole("button", { name: "提交自取订单", exact: true }),
  ).toBeEnabled();
  expect(state.writes).toEqual([]);
});
test("delivery validates point/contact/minimum and snapshots fee, then offers prepayment", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await checkout(page);
  const submit = page.getByRole("button", {
    name: "提交配送订单，去付款",
    exact: true,
  });
  await expect(submit).toBeDisabled();
  await fillContact(page);
  await expect(submit).toBeEnabled();
  await expect(page.locator(".summary-total")).toContainText("21");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath("delivery-checkout-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".fulfillment-card").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("delivery-configured-mobile.png"),
  });
  await submit.click();
  await expect(page).toHaveURL(/orders\/delivery-ui-order$/);
  expect(state.writes[0]?.body).toMatchObject({
    fulfillment_type: "delivery",
    delivery_point_id: 71,
    recipient_name: "小林",
    expected_delivery_fee_cents: 300,
    contact_phone: "13800138000",
  });
  await expect(
    page
      .locator(".student-payment")
      .getByRole("button", { name: "微信支付", exact: true }),
  ).toBeEnabled();
  await expect(page.getByText("也可到摊付款")).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});
test("lost checkout response retries with the same delivery idempotency key", async ({
  page,
}) => {
  const state = await fixture(page);
  state.failCreate = "once";
  await checkout(page);
  await fillContact(page);
  const button = page.getByRole("button", {
    name: "提交配送订单，去付款",
    exact: true,
  });
  await button.click();
  await expect(
    page.getByText("暂时连接不上，请检查网络后重试。"),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "确认原订单结果", exact: true })
    .click();
  await expect(page).toHaveURL(/orders\/delivery-ui-order$/);
  const bodies = state.writes
    .filter((x) => x.path === "/orders")
    .map((x) => x.body);
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toEqual(bodies[1]);
});

test("delivery minimum and contact number both gate checkout", async ({
  page,
}) => {
  const state = await fixture(page);
  state.stall.delivery.min_order_cents = 2500;
  await checkout(page);
  await fillContact(page);
  const submit = page.getByRole("button", {
    name: "提交配送订单，去付款",
    exact: true,
  });
  await expect(submit).toBeDisabled();
  await expect(page.getByText("餐费还差 ¥7 达到起送金额。")).toBeVisible();
  await page
    .getByRole("button", { name: "增加招牌烤冷面", exact: true })
    .click();
  await expect(submit).toBeEnabled();
  await page.getByLabel(/联系手机号/).fill("123");
  await expect(submit).toBeDisabled();
  expect(state.writes).toEqual([]);
});

test("a delivery payment nearing expiry cannot start a new payment intent", async ({
  page,
}) => {
  const state = await fixture(page);
  state.order.expires_at = new Date(Date.now() + 50000).toISOString();
  await page.goto("/orders/delivery-ui-order");
  await expect(
    page.getByRole("button", { name: "付款时限将至", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(
      "剩余时间不足以发起新支付，请取消本单后重新下单。已发起的支付请先核对结果。",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "取消订单", exact: true }),
  ).toBeEnabled();
  expect(state.writes).toEqual([]);
});
test("fee changes require another explicit confirmation", async ({ page }) => {
  const state = await fixture(page);
  state.failFee = true;
  await checkout(page);
  await fillContact(page);
  await page
    .getByRole("button", { name: "提交配送订单，去付款", exact: true })
    .click();
  await expect(
    page.getByText("配送费刚刚更新，已显示最新应付金额。请核对后再次提交。"),
  ).toBeVisible();
  expect(state.writes).toHaveLength(1);
  await expect(page.locator(".summary-total")).toContainText("22");
  await page
    .getByRole("button", { name: "确认新价格并提交", exact: true })
    .click();
  await expect(page).toHaveURL(/orders\/delivery-ui-order$/);
  expect(state.writes[1]?.body.expected_delivery_fee_cents).toBe(400);
  expect(state.writes[1]?.body.idempotency_key).not.toBe(
    state.writes[0]?.body.idempotency_key,
  );
});
test("closing delivery payment never switches to cash and receipt cannot be confirmed early", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/orders/delivery-ui-order");
  await page
    .locator(".student-payment")
    .getByRole("button", { name: "微信支付", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "关闭本次微信支付", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("也可到摊付款")).toHaveCount(0);
  await page
    .getByRole("button", { name: "关闭本次微信支付", exact: true })
    .click();
  expect(state.order.payment_method).toBe("wechat");
  await expect(
    page.getByRole("button", { name: "我已收到餐点", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".receipt-code")).toHaveCount(0);
});
test("arrived means waiting for handoff; customer explicitly confirms actual receipt", async ({
  page,
}, info) => {
  const state = await fixture(page, "arrived");
  await page.goto("/orders/delivery-ui-order");
  await expect(
    page.getByRole("heading", { name: "餐点到了，去交接点收餐吧" }),
  ).toBeVisible();
  await expect(page.locator(".delivery-destination")).toContainText(
    point.address,
  );
  await expect(page.locator(".receipt-code")).toContainText("82745103");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page.screenshot({
    path: info.outputPath("delivery-arrived-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".delivery-progress").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("delivery-arrived-mobile.png"),
  });
  await page.getByRole("button", { name: "我已收到餐点", exact: true }).click();
  expect(state.writes).toHaveLength(0);
  await page.getByRole("button", { name: "还没收到", exact: true }).click();
  expect(state.writes).toHaveLength(0);
  await page.getByRole("button", { name: "我已收到餐点", exact: true }).click();
  await page.getByRole("button", { name: "确认已收餐", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "这一餐，送到你身边" }),
  ).toBeVisible();
  expect(
    state.writes.filter((x) => x.path.endsWith("/confirm-receipt")),
  ).toHaveLength(1);
});
test("delivery issue suppresses receipt confirmation and exposes the real issue", async ({
  page,
}) => {
  const state = await fixture(page, "arrived");
  state.order.delivery_issue = "交接点临时封闭，请联系商家";
  await page.goto("/orders/delivery-ui-order");
  await expect(page.locator(".delivery-alert")).toContainText(
    state.order.delivery_issue,
  );
  await expect(
    page.getByRole("button", { name: "我已收到餐点", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".receipt-code")).toHaveCount(0);
});
test("paid pending delivery can cancel with a full refund explanation", async ({
  page,
}) => {
  const state = await fixture(page, "pending");
  await page.goto("/orders/delivery-ui-order");
  await page.getByRole("button", { name: "取消订单", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "取消后将申请退回餐费和配送费",
  );
  await page.getByRole("button", { name: "确认取消", exact: true }).click();
  await expect(page.locator(".student-payment")).toContainText(
    "退款正在处理中",
  );
  expect(state.writes).toHaveLength(1);
});
for (const status of ["pending_payment", "ready", "delivering", "arrived"])
  test(`delivery ${status} remains in active orders with appropriate CTA`, async ({
    page,
  }) => {
    const state = await fixture(page, status);
    await page.goto("/orders");
    await page.getByRole("button", { name: /进行中/ }).click();
    const card = page.locator(".order-card");
    await expect(card).toContainText("商家配送");
    await expect(card).toContainText(
      status === "pending_payment"
        ? "去付款"
        : status === "arrived"
          ? "去收餐"
          : "查看配送进度",
    );
    await expect(card).not.toContainText("查看取餐码");
    expect(state.unexpected).toEqual([]);
  });
