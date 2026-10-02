import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow } from "./helpers";

// Discovery and presentation only. Every API request is intercepted; these
// fixtures must never reserve real inventory or reach a payment provider.
const user = {
  id: 993,
  username: "payment_visibility_fixture",
  display_name: "支付入口测试",
  is_merchant: false,
  is_staff: false,
};
const product = {
  id: 993,
  name: "测试餐点",
  image: "/images/food-cold-noodles.jpg",
  price_cents: 1450,
  stock: 20,
  is_active: true,
};
function readiness(available: boolean) {
  return {
    available,
    reason: available ? "" : "示例环境不发起真实微信扣款，当前支持到摊付款。",
    channels: available ? ["native", "h5"] : [],
  };
}
function order(status: string, available: boolean, id = "visibility-fixture") {
  return {
    id,
    number: `UI-${id}`,
    stall_id: 993,
    stall_name: "支付入口测试摊位",
    status,
    payment_method: "offline",
    payment_status: "unpaid",
    payment_review_required: false,
    wechat_payment: readiness(available),
    payment: null,
    refund: null,
    payment_can_close: false,
    total_cents: 1450,
    created_at: new Date().toISOString(),
    pickup_code: "12345678",
    pickup_address: "测试校园取餐点",
    pickup_latitude: 45.7,
    pickup_longitude: 126.6,
    current_address: "测试校园取餐点",
    location_changed: false,
    note: "",
    contact_phone: "",
    cancel_requested: false,
    cancel_reason: "",
    review: null,
    items: [
      {
        ...product,
        product_id: product.id,
        unit_price_cents: 1450,
        quantity: 1,
      },
    ],
  };
}
async function fixture(
  page: Page,
  orders: ReturnType<typeof order>[],
  available = false,
) {
  const unexpected: string[] = [];
  const writes: string[] = [];
  const stall = {
    id: 993,
    name: "支付入口测试摊位",
    address: "测试校园取餐点",
    area_name: "测试校园",
    prep_minutes: 10,
    contact_phone: "",
    can_order: true,
    transaction_enabled: true,
    wechat_payment: readiness(available),
    products: [product],
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
    if (!["GET", "HEAD"].includes(request.method())) {
      writes.push(`${request.method()} ${path}`);
      return send(
        { detail: "This visibility fixture must not submit requests" },
        500,
      );
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
    if (path === "/orders/active-summary") return send([]);
    if (path === "/stalls/993") return send(stall);
    if (path === "/orders") return send(orders);
    const selected = orders.find((item) => path === `/orders/${item.id}`);
    if (selected) return send(selected);
    unexpected.push(path);
    return send(
      { detail: "Unexpected API request in visibility fixture" },
      500,
    );
  });
  return { unexpected, writes };
}

for (const status of ["pending", "preparing"]) {
  for (const available of [false, true]) {
    test(`payment is discoverable before pickup: ${status}, configured=${available}`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width: 390, height: 844 });
      const state = order(status, available);
      const f = await fixture(page, [state], available);
      await page.goto(`/orders/${state.id}`);
      const payment = page.locator(".student-payment");
      await expect(
        payment.getByRole("heading", { name: "支付方式", exact: true }),
      ).toBeVisible();
      await expect(
        payment.getByRole("button", {
          name: available ? "出餐后可支付" : "微信支付",
          exact: true,
        }),
      ).toBeDisabled();
      await expect(payment).toContainText(
        available ? "出餐后可支付" : "微信支付尚未开通",
      );
      await expect(payment).toContainText("到摊付款");
      await expect(page.locator(".pickup-code")).toHaveCount(0);
      await expect(
        payment.getByText("微信支付已确认", { exact: true }),
      ).toHaveCount(0);

      // A first-time customer can find payment before scrolling through the
      // fulfilment progress, pickup address and receipt.
      const paymentBox = await payment.boundingBox();
      const progressBox = await page.locator(".progress-card").boundingBox();
      expect(paymentBox).toBeTruthy();
      expect(progressBox).toBeTruthy();
      expect(paymentBox!.y).toBeLessThan(844);
      expect(paymentBox!.y).toBeLessThan(progressBox!.y);
      await assertNoHorizontalOverflow(page);
      if (status === "pending" && !available) {
        await page.screenshot({
          path: testInfo.outputPath("payment-visible-before-acceptance.png"),
        });
      }
      expect(f.writes).toEqual([]);
      expect(f.unexpected).toEqual([]);
    });
  }
}

for (const available of [false, true]) {
  test(`checkout displays both payment methods without initiating payment: configured=${available}`, async ({
    page,
  }, testInfo) => {
    const f = await fixture(page, [], available);
    await page.addInitScript((item) => {
      localStorage.setItem(
        "yanhuo-cart-v1",
        JSON.stringify({ 993: [{ product: item, quantity: 1 }] }),
      );
    }, product);
    await page.goto("/checkout/993");
    const methods = page.locator(".checkout-payment-methods");
    await expect(
      methods.getByRole("heading", { name: "支付方式", exact: true }),
    ).toBeVisible();
    await expect(
      methods.getByRole("heading", { name: /^微信支付/ }),
    ).toBeVisible();
    await expect(methods.getByText("到摊付款", { exact: true })).toBeVisible();
    await expect(methods).toContainText(
      available ? "出餐后可支付" : "尚未开通",
    );
    await expect(methods).toContainText("可使用");
    await expect(
      page.getByRole("button", { name: "提交自取订单", exact: true }),
    ).toBeEnabled();
    // Payment isn't a precondition for creating a pickup order. Neither a
    // method information card nor initial rendering may start a transaction.
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await assertNoHorizontalOverflow(page);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await methods.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath(
        `checkout-payment-methods-${available ? "configured" : "unavailable"}.png`,
      ),
    });
    expect(f.writes).toEqual([]);
    expect(f.unexpected).toEqual([]);
  });
}

for (const available of [false, true]) {
  test(`orders list leads customers to payment information: configured=${available}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const state = order("ready", available);
    const f = await fixture(page, [state], available);
    await page.goto("/orders");
    const card = page.locator(".order-card");
    await expect(card.locator(".payment-summary")).toContainText(
      available ? "微信支付 · 待付款" : "微信支付 · 尚未开通",
    );
    await expect(card).toContainText(available ? "去付款" : "查看付款方式");
    await assertNoHorizontalOverflow(page);
    await card.click();
    await expect(page).toHaveURL(new RegExp(`/orders/${state.id}$`));
    const payment = page.locator(".student-payment");
    await expect(
      payment.getByRole("heading", { name: "支付方式", exact: true }),
    ).toBeVisible();
    const button = payment.getByRole("button", {
      name: "微信支付",
      exact: true,
    });
    if (available) await expect(button).toBeEnabled();
    else await expect(button).toBeDisabled();
    expect(f.writes).toEqual([]);
    expect(f.unexpected).toEqual([]);
  });
}
