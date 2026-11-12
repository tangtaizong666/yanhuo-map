import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";
import QRCode from "qrcode";

// Isolated UI contract tests only. All API requests are intercepted, and no
// payment service or real order is used. Financial transitions have backend tests.
const user = {
  id: 992,
  username: "payment_student_fixture",
  display_name: "付款界面测试",
  is_merchant: false,
  is_staff: false,
};
function makeOrder(overrides: Record<string, any> = {}) {
  return {
    id: "student-payment-fixture",
    mode: "live",
    fulfillment_type: "pickup",
    number: "UI-ONLY-0001",
    stall_id: 992,
    stall_name: "界面测试摊位",
    status: "ready",
    payment_method: "offline",
    payment_status: "unpaid",
    payment_review_required: false,
    offline_payment_available: true,
    stall_payment_qr_image: "/media/payment-student/collection-test.svg",
    wechat_payment: {
      mode: "live",
      supported: true,
      available: false,
      reason: "示例环境不发起真实微信扣款，当前支持到摊付款。",
      channels: [],
    },
    payment: null,
    refund: null,
    payment_can_close: false,
    total_cents: 1450,
    created_at: new Date().toISOString(),
    pickup_code: "123456",
    pickup_address: "测试取餐点",
    pickup_latitude: 45.7,
    pickup_longitude: 126.6,
    current_address: "测试取餐点",
    location_changed: false,
    note: "",
    contact_phone: "",
    cancel_requested: false,
    cancel_reason: "",
    review: null,
    items: [
      {
        product_id: 992,
        name: "测试餐点",
        image: "/images/food-cold-noodles.jpg",
        unit_price_cents: 1450,
        quantity: 1,
      },
    ],
    ...overrides,
  };
}
async function fixture(
  page: Page,
  state: any,
  handler?: (kind: string) => Promise<any>,
) {
  const calls: string[] = [],
    unexpected: string[] = [];
  await page.route("https://**/*", (route) => route.abort());
  const code = await QRCode.toString("UI-TEST-ONLY-NO-PAYMENT", {
    type: "svg",
  });
  await page.route("**/media/payment-student/collection-test.svg", (route) =>
    route.fulfill({ contentType: "image/svg+xml", body: code }),
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    const send = (data: any, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
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
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/orders/active-summary") return send([]);
    if (path === "/stalls/992") return send({ id: 992, contact_phone: "" });
    if (path === `/orders/${state.id}`) return send(state);
    const match = path.match(/\/payments\/(wechat|sync|close)$/);
    if (match && handler) {
      calls.push(match[1]);
      return send(await handler(match[1]));
    }
    unexpected.push(path);
    return send({ detail: "Unexpected API request in UI fixture" }, 500);
  });
  await page.goto(`/orders/${state.id}`);
  await expect(
    page.getByRole("heading", { name: "支付方式", exact: true }),
  ).toBeVisible();
  return { calls, unexpected };
}
test("UI contract: unconfigured WeChat is unavailable and offline collection remains clear", async ({
  page,
}, testInfo) => {
  const f = await fixture(page, makeOrder());
  await expect(
    page.getByText("微信支付尚未开通", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "微信支付", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".student-payment")).toContainText(
    "当前支持到摊付款",
  );
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 390, height: 900 });
  await page.locator(".student-payment").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("wechat-unavailable-mobile.png"),
  });
  expect(f.calls).toEqual([]);
  expect(f.unexpected).toEqual([]);
});
test("UI contract: repeated click creates once, QR pending is not paid, and server close restores offline", async ({
  page,
}) => {
  const state = makeOrder({
    wechat_payment: { available: true, reason: "", channels: ["native"] },
  });
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  const f = await fixture(page, state, async (kind) => {
    if (kind === "wechat") {
      await wait;
      Object.assign(state, {
        payment_method: "wechat",
        payment_can_close: true,
        payment: {
          id: "ui-attempt",
          status: "pending",
          channel: "native",
          code_url: "weixin://wxpay/bizpayurl?pr=UI_TEST_ONLY",
          h5_url: "",
          expires_at: new Date(Date.now() + 600000).toISOString(),
          error_message: "",
        },
      });
    } else if (kind === "close") {
      state.payment.status = "closed";
      state.payment_method = "offline";
      state.payment_can_close = false;
    }
    return state;
  });
  await expect(page.locator(".stall-qr img")).toBeVisible();
  await page.getByRole("button", { name: "微信支付", exact: true }).click();
  await expect(page.getByRole("button", { name: "正在创建…" })).toBeDisabled();
  await expect(page.locator(".stall-qr")).toHaveCount(0);
  await expect.poll(() => f.calls).toEqual(["wechat"]);
  release();
  await expect(page.getByAltText("本订单微信支付二维码")).toBeVisible();
  await expect(page.locator(".stall-qr")).toHaveCount(0);
  await expect(page.getByText("微信支付已确认", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "申请取消", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "关闭微信支付，改为到摊付款" })
    .click();
  await expect(
    page.getByRole("button", { name: "微信支付", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".stall-qr img")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "申请取消", exact: true }),
  ).toBeVisible();
  expect(f.calls).toEqual(["wechat", "close"]);
  expect(f.unexpected).toEqual([]);
});
test("UI contract: unknown, refunding, and manual review do not show successful payment or allow cancellation", async ({
  page,
}) => {
  const state = makeOrder({
    payment_method: "wechat",
    payment_can_close: true,
    payment: {
      id: "ui-unknown",
      status: "reconcile",
      channel: "native",
      code_url: "",
      h5_url: "",
      expires_at: new Date().toISOString(),
    },
  });
  const f = await fixture(page, state, async () => state);
  await expect(
    page.getByText("正在确认微信付款状态", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("微信支付已确认", { exact: true })).toHaveCount(
    0,
  );
  Object.assign(state, {
    payment_status: "refunding",
    refund: { status: "processing", amount_cents: 1450 },
  });
  await page.getByRole("button", { name: "刷新订单状态", exact: true }).click();
  await expect(page.getByText("退款正在处理中", { exact: true })).toBeVisible();
  await expect(page.locator(".pickup-code")).toHaveCount(0);
  await expect(page.getByText("微信退款已成功", { exact: true })).toHaveCount(
    0,
  );
  state.payment_review_required = true;
  await page.getByRole("button", { name: "刷新订单状态", exact: true }).click();
  await expect(
    page.getByText("这笔款项需要核对", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "申请取消", exact: true }),
  ).toHaveCount(0);
  expect(f.unexpected).toEqual([]);
});
test("UI contract: H5 entry rejects an untrusted redirect and only verified API results show paid", async ({
  page,
}) => {
  const state = makeOrder({
    payment_method: "wechat",
    payment_can_close: true,
    payment: {
      id: "ui-h5",
      status: "pending",
      channel: "h5",
      h5_url: "https://evil.example/payment",
      expires_at: new Date(Date.now() + 600000).toISOString(),
    },
  });
  const f = await fixture(page, state, async () => {
    state.payment_status = "paid";
    state.payment.status = "paid";
    return state;
  });
  await expect(page.getByRole("link", { name: "前往微信支付" })).toHaveCount(0);
  await page.getByRole("button", { name: "刷新付款状态" }).click();
  await expect(page.getByText("微信支付已确认", { exact: true })).toBeVisible();
  await expect(page.locator(".payment-note")).toContainText("微信支付已核验");
  expect(f.unexpected).toEqual([]);
});

test("UI contract: uncertain creation offers recovery of the existing payment entry", async ({
  page,
}) => {
  const state = makeOrder({
    wechat_payment: {
      available: true,
      supported: true,
      reason: "",
      channels: ["native"],
    },
    payment_method: "wechat",
    payment_can_close: true,
    payment: {
      id: "ui-recover-existing",
      status: "reconcile",
      channel: "native",
      code_url: "",
      h5_url: "",
      expires_at: new Date(Date.now() + 600000).toISOString(),
    },
  });
  const f = await fixture(page, state, async (kind) => {
    expect(kind).toBe("wechat");
    state.payment.status = "pending";
    state.payment.code_url = "weixin://wxpay/bizpayurl?pr=UI_RECOVERY_ONLY";
    return state;
  });
  await page.getByRole("button", { name: "重试获取付款入口" }).click();
  await expect(page.getByAltText("本订单微信支付二维码")).toBeVisible();
  expect(state.payment.id).toBe("ui-recover-existing");
  expect(f.calls).toEqual(["wechat"]);
  expect(f.unexpected).toEqual([]);
});

test("UI contract: desktop cannot initiate an H5-only merchant channel", async ({
  page,
}) => {
  const f = await fixture(
    page,
    makeOrder({
      wechat_payment: { available: true, reason: "", channels: ["h5"] },
    }),
  );
  await expect(
    page.getByRole("button", { name: "微信支付", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".student-payment")).toContainText(
    "暂未开通电脑扫码支付",
  );
  expect(f.calls).toEqual([]);
});

test("UI contract: delayed payment result pauses parent reads then refreshes the latest fulfilment", async ({
  page,
}) => {
  const state = makeOrder({
    payment_method: "wechat",
    payment_can_close: true,
    payment: {
      id: "ui-delayed-sync",
      status: "pending",
      channel: "native",
      code_url: "weixin://wxpay/bizpayurl?pr=UI_DELAY_ONLY",
      h5_url: "",
      expires_at: new Date(Date.now() + 600000).toISOString(),
    },
  });
  let release!: () => void;
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  const f = await fixture(page, state, async () => {
    const older = structuredClone(state);
    older.payment_status = "paid";
    older.payment.status = "paid";
    await wait;
    return older;
  });
  await page.getByRole("button", { name: "刷新付款状态" }).click();
  await expect.poll(() => f.calls).toEqual(["sync"]);
  await expect(
    page.getByRole("button", { name: "刷新订单状态", exact: true }),
  ).toBeDisabled();
  Object.assign(state, {
    status: "completed",
    payment_status: "paid",
    completed_at: new Date().toISOString(),
  });
  state.payment.status = "paid";
  release();
  await expect(
    page.getByRole("heading", { name: "这一餐，刚刚好" }),
  ).toBeVisible();
  await expect(page.locator(".pickup-code")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "刷新订单状态", exact: true }),
  ).toBeEnabled();
  expect(f.unexpected).toEqual([]);
});

test("UI contract: mobile external browser offers H5 while WeChat webview explains the unavailable channel", async ({
  browser,
}) => {
  for (const embedded of [false, true]) {
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      userAgent: `Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Mobile Safari/537.36${embedded ? " MicroMessenger/8.0" : ""}`,
    });
    try {
      const page = await context.newPage();
      const state = makeOrder({
        wechat_payment: { available: true, reason: "", channels: ["h5"] },
      });
      const f = await fixture(page, state, async () => {
        Object.assign(state, {
          payment_method: "wechat",
          payment_can_close: true,
          payment: {
            id: "ui-mobile-h5",
            status: "pending",
            channel: "h5",
            code_url: "",
            h5_url:
              "https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb?prepay_id=UI_TEST_ONLY",
            expires_at: new Date(Date.now() + 600000).toISOString(),
          },
        });
        return state;
      });
      if (embedded) {
        await expect(
          page.getByRole("button", { name: "微信支付", exact: true }),
        ).toBeDisabled();
        await expect(page.locator(".student-payment")).toContainText(
          "尚未接入微信内网页支付",
        );
        expect(f.calls).toEqual([]);
      } else {
        await page
          .getByRole("button", { name: "微信支付", exact: true })
          .click();
        const entry = page.getByRole("link", { name: "前往微信支付" });
        await expect(entry).toBeVisible();
        const url = new URL((await entry.getAttribute("href"))!);
        expect(url.hostname).toBe("wx.tenpay.com");
        expect(url.searchParams.get("redirect_url")).toBe(
          `${new URL(page.url()).origin}/orders/${state.id}`,
        );
        await expect(
          page.getByText("微信支付已确认", { exact: true }),
        ).toHaveCount(0);
      }
      await assertNoHorizontalOverflow(page);
      expect(f.unexpected).toEqual([]);
    } finally {
      await context.close();
    }
  }
});
