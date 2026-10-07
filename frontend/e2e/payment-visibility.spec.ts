import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow } from "./helpers";
import { publicProduct, publicStall } from "./public-contracts";
import QRCode from "qrcode";

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
function readiness(available: boolean, supported = true) {
  return {
    mode: "live",
    supported,
    available,
    reason: available ? "" : "示例环境不发起真实微信扣款，当前支持到摊付款。",
    channels: available ? ["native", "h5"] : [],
  };
}
function order(status: string, available: boolean, id = "visibility-fixture") {
  return {
    id,
    mode: "live",
    fulfillment_type: "pickup",
    number: `UI-${id}`,
    stall_id: 993,
    stall_name: "支付入口测试摊位",
    status,
    payment_method: "offline",
    payment_status: "unpaid",
    payment_review_required: false,
    offline_payment_available: status === "ready",
    // Intentionally configured even for blocked states: the UI must independently
    // reject a stale code, rather than pass because every fixture image is empty.
    stall_payment_qr_image: "/media/payment-visibility/collection-test.svg",
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
  supported = true,
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
    can_order: supported,
    transaction_enabled: supported,
    order_unavailable_reason: supported
      ? ""
      : "该商户仅提供找摊信息，暂不接受线上订单。",
    wechat_payment: readiness(available, supported),
    products: [product],
  };
  await page.route("https://**/*", (route) => route.abort());
  const code = await QRCode.toString("UI-TEST-ONLY-NO-PAYMENT", {
    type: "svg",
  });
  await page.route("**/media/payment-visibility/collection-test.svg", (route) =>
    route.fulfill({ contentType: "image/svg+xml", body: code }),
  );
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
    if (path === "/stalls/993") return send(publicStall(stall));
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
      await expect(payment.locator(".stall-qr")).toHaveCount(0);
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
  test(`checkout displays available payment methods without initiating payment: configured=${available}`, async ({
    page,
  }, testInfo) => {
    const f = await fixture(page, [], available);
    await page.addInitScript((item) => {
      localStorage.setItem(
        "yanhuo-cart-v2:user:993",
        JSON.stringify({ 993: [{ product: item, quantity: 1 }] }),
      );
    }, publicProduct(product));
    await page.goto("/checkout/993");
    const methods = page.locator(".checkout-payment-methods");
    await expect(
      methods.getByRole("heading", { name: "支付方式", exact: true }),
    ).toBeVisible();
    if (available) {
      await expect(methods.getByRole("heading", { name: /^微信支付/ })).toBeVisible();
      await expect(methods).toContainText("出餐后可支付");
      await expect(methods).toContainText("可使用");
    } else {
      await expect(methods.getByRole("heading", { name: /^微信支付/ })).toHaveCount(0);
      await expect(methods).toContainText("先下单，商家出餐后再付款");
      await methods.getByText("为什么微信支付尚未开通？", { exact: true }).click();
      await expect(methods.getByText(readiness(false).reason, { exact: true })).toBeVisible();
      await expect(methods).toContainText("提交订单本身不会扣款");
    }
    await expect(methods.getByText("到摊付款", { exact: true })).toBeVisible();
    await expect(methods).toContainText("取餐时扫摊主本人的收款码付款，平台不经手款项");
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
      available ? "微信支付 · 待付款" : "到摊扫码付给摊主",
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
    await expect(payment.locator(".stall-qr img")).toBeVisible();
    expect(f.writes).toEqual([]);
    expect(f.unexpected).toEqual([]);
  });
}

test("discovery-only merchant offers no checkout or permanently unavailable online-payment card", async ({
  page,
}) => {
  const f = await fixture(page, [], false, false);
  await page.addInitScript((item) => {
    localStorage.setItem(
      "yanhuo-cart-v2:user:993",
      JSON.stringify({ 993: [{ product: item, quantity: 1 }] }),
    );
  }, publicProduct(product));
  await page.goto("/checkout/993");
  const methods = page.locator(".checkout-payment-methods");
  await expect(methods).toContainText("暂不接受线上订单");
  await expect(methods.locator(".wechat-method")).toHaveCount(0);
  await expect(methods.locator(".offline-method")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "提交自取订单", exact: true }),
  ).toBeDisabled();
  await assertNoHorizontalOverflow(page);
  expect(f.writes).toEqual([]);
  expect(f.unexpected).toEqual([]);
});

test("historical pickup keeps onsite settlement when merchant now only provides discovery", async ({
  page,
}) => {
  const state = order("ready", false);
  state.wechat_payment = readiness(false, false);
  const f = await fixture(page, [state], false, false);
  await page.goto(`/orders/${state.id}`);
  const payment = page.locator(".student-payment");
  await expect(
    payment.getByRole("button", { name: "微信支付", exact: true }),
  ).toHaveCount(0);
  await expect(payment).toContainText("到摊付款");
  await expect(payment.locator(".stall-qr img")).toBeVisible();
  await expect(payment.locator(".stall-qr img")).toHaveJSProperty(
    "complete",
    true,
  );
  await expect
    .poll(() =>
      payment
        .locator(".stall-qr img")
        .evaluate((image) => (image as HTMLImageElement).naturalWidth),
    )
    .toBeGreaterThan(0);
  expect(f.writes).toEqual([]);
  expect(f.unexpected).toEqual([]);
});

test("revoked admission hides an already-created WeChat code but keeps resolution actions", async ({
  page,
}) => {
  const state = {
    ...order("ready", true),
    payment_method: "wechat",
    allowed_actions: ["pay", "sync_payment", "close_payment"],
    payment: {
      id: "previously-allowed",
      mode: "live",
      status: "pending",
      channel: "native",
      code_url: "weixin://wxpay/UI-ONLY-BEFORE-REVOCATION",
      h5_url: "",
      expires_at: new Date(Date.now() + 600000).toISOString(),
    },
  };
  const f = await fixture(page, [state], true);
  await page.goto(`/orders/${state.id}`);
  await expect(page.getByAltText("本订单微信支付二维码")).toBeVisible();
  state.wechat_payment = {
    ...readiness(false),
    reason: "经营许可证已过有效期，暂停新的线上交易。",
  };
  // Retain the stale code and pay action intentionally: the client still hides it.
  await page.getByRole("button", { name: "刷新订单状态", exact: true }).click();
  await expect(page.getByAltText("本订单微信支付二维码")).toHaveCount(0);
  await expect(page.locator(".stall-qr")).toHaveCount(0);
  await expect(page.locator(".student-payment")).toContainText(
    "当前已暂停新的微信付款",
  );
  await expect(
    page.getByRole("button", { name: "刷新付款状态", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", {
      name: "关闭微信支付，改为到摊付款",
      exact: true,
    }),
  ).toBeEnabled();
  expect(f.writes).toEqual([]);
  expect(f.unexpected).toEqual([]);
});

test("historical delivery never offers onsite payment after admission closes", async ({
  page,
}) => {
  const state = {
    ...order("pending_payment", false),
    fulfillment_type: "delivery",
    wechat_payment: readiness(false, false),
    expires_at: new Date(Date.now() + 600000).toISOString(),
  };
  const f = await fixture(page, [state], false, false);
  await page.goto(`/orders/${state.id}`);
  const payment = page.locator(".student-payment");
  await expect(payment).toContainText("配送不能改为线下付款");
  await expect(payment).not.toContainText("到摊付款");
  await expect(payment.locator(".stall-qr")).toHaveCount(0);
  await expect(
    payment.getByRole("button", { name: "微信支付", exact: true }),
  ).toHaveCount(0);
  expect(f.writes).toEqual([]);
  expect(f.unexpected).toEqual([]);
});

const blockedEntries: [string, Record<string, unknown>][] = [
  ["pending", { status: "pending" }],
  ["preparing", { status: "preparing" }],
  ["cancellation in progress", { cancel_requested: true }],
  ["cancelled", { status: "cancelled" }],
  ["completed", { status: "completed" }],
  ["paid", { payment_status: "paid" }],
  [
    "refund pending",
    {
      payment_status: "refunding",
      refund: { id: "refund", status: "processing", amount_cents: 1450 },
    },
  ],
  ["refunded", { payment_status: "refunded" }],
  ["review required", { payment_review_required: true }],
  ["financial hold", { financial_hold_reason: "款项尚待核实，请勿重复付款。" }],
  [
    "unknown payment",
    {
      payment: {
        id: "unknown",
        status: "reconcile",
        channel: "native",
        expires_at: new Date(Date.now() + 600000).toISOString(),
      },
    },
  ],
  [
    "active payment",
    {
      payment: {
        id: "active",
        status: "pending",
        channel: "native",
        expires_at: new Date(Date.now() + 600000).toISOString(),
      },
    },
  ],
  [
    "captured payment in inconsistent snapshot",
    {
      payment: {
        id: "captured",
        status: "paid",
        channel: "native",
        expires_at: new Date().toISOString(),
      },
    },
  ],
  [
    "historical resolved refund",
    {
      refunds: [
        { id: "old", status: "closed", resolved_at: new Date().toISOString() },
      ],
    },
  ],
  ["delivery", { fulfillment_type: "delivery" }],
  ["simulation", { mode: "simulation" }],
  ["server disabled entry", { offline_payment_available: false }],
  ["server missing permission", { offline_payment_available: undefined }],
];
for (const [label, changes] of blockedEntries) {
  test(`configured collection code stays hidden: ${label}`, async ({
    page,
  }) => {
    // Start with a positive ready-order permission then introduce contradictory
    // state to prove the client does not blindly trust an image/permission alone.
    const state = { ...order("ready", true), ...changes };
    const f = await fixture(page, [state], true);
    await page.goto(`/orders/${state.id}`);
    await expect(
      page.getByText(state.stall_name, { exact: true }).first(),
    ).toBeVisible();
    await expect(page.locator(".stall-qr")).toHaveCount(0);
    if (changes.cancel_requested) {
      await expect(page.locator(".student-payment")).toContainText(
        "取消申请正在处理中",
      );
      await expect(
        page.getByRole("button", { name: "微信支付", exact: true }),
      ).toHaveCount(0);
    }
    if (changes.mode === "simulation") {
      await expect(page.locator(".student-payment")).toContainText(
        "模拟 · 不会扣款",
      );
      await expect(page.locator(".student-payment")).toContainText(
        "不要支付真实款项",
      );
    }
    expect(f.writes).toEqual([]);
    expect(f.unexpected).toEqual([]);
  });
}
