import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow } from "./helpers";

// All reads/writes are intercepted. These transitions describe persisted server
// responses; the student UI must never manufacture payment/refund success.
function order(id: string, overrides: Record<string, any> = {}): any {
  return {
    id,
    number: `FOLLOWUP-${id}`,
    stall_id: 987,
    stall_name: `校园小摊-${id}`,
    mode: "live",
    fulfillment_type: "pickup",
    status: "cancelled",
    payment_status: "paid",
    payment_method: "wechat",
    payment_review_required: false,
    payment: {
      id: `payment-${id}`,
      status: "paid",
      channel: "native",
      code_url: "",
      h5_url: "",
      expires_at: new Date().toISOString(),
    },
    wechat_payment: { available: false, reason: "未开通", channels: [] },
    refund: {
      id: `refund-${id}`,
      status: "closed",
      amount_cents: 1600,
      reason: "商家取消",
      error_message: "",
      created_at: new Date().toISOString(),
      completed_at: null,
    },
    total_cents: 1600,
    created_at: new Date().toISOString(),
    pickup_code: "12345678",
    pickup_address: "南门取餐点",
    contact_phone: "",
    merchant_contact_phone: "13800138000",
    note: "",
    cancel_requested: false,
    cancel_reason: "商家无法提供餐点",
    review: null,
    items: [
      {
        product_id: 987,
        name: "招牌烤冷面",
        image: "/images/food-cold-noodles.jpg",
        unit_price_cents: 1600,
        quantity: 1,
      },
    ],
    ...overrides,
  };
}
async function fixture(page: Page, orders: any[]) {
  const user = {
    id: 987,
    username: "followup_student",
    display_name: "售后验证同学",
    is_merchant: false,
    is_staff: false,
  };
  const state = {
    orders,
    reads: 0,
    failReads: false,
    mutations: [] as string[],
    unexpected: [] as string[],
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
    if (request.method() !== "GET") {
      state.mutations.push(`${request.method()} ${path}`);
      return send(
        { detail: "Student follow-up must only read stored results" },
        500,
      );
    }
    if (path === "/config")
      return send({
        user,
        demo_mode: true,
        services_simulation_enabled: true,
        areas: [],
        amap_key: "",
      });
    if (path === "/auth/me") return send(user);
    if (path === "/orders/active-summary")
      return send({ user_id: user.id, counts: { total: 0 }, order: null });
    if (path === "/stalls/987")
      return send({ id: 987, contact_phone: "13800138000" });
    if (path === "/orders") return send(state.orders);
    const selected = state.orders.find((item) => path === `/orders/${item.id}`);
    if (selected) {
      state.reads++;
      return state.failReads ? route.abort("failed") : send(selected);
    }
    state.unexpected.push(path);
    return send({ detail: "Unexpected fixture request" }, 500);
  });
  return state;
}

test("refund follow-up is separate from fulfilment, counts unresolved cases, and fits all four widths", async ({
  page,
}, info) => {
  const orders = [
    order("closed", { mode: "simulation" }),
    order("abnormal", {
      status: "rejected",
      payment_status: "refunding",
      refund: { status: "abnormal", amount_cents: 1600 },
    }),
    order("processing", {
      payment_status: "refunding",
      refund: { status: "processing", amount_cents: 1600 },
    }),
    order("review", {
      status: "completed",
      refund: null,
      payment_review_required: true,
    }),
    order("refunded", {
      payment_status: "refunded",
      refund: { status: "success", amount_cents: 1600 },
    }),
    order("preparing", {
      status: "preparing",
      payment_status: "unpaid",
      payment_method: "offline",
      payment: null,
      refund: null,
    }),
  ];
  const state = await fixture(page, orders);
  await page.goto("/orders");
  await expect(page.locator(".active-notice")).toContainText("1");
  await expect(
    page.getByRole("button", { name: /退款与待处理\s*4/ }),
  ).toBeVisible();
  await expect(page.locator(".financial-notice")).toContainText(
    "4 笔订单的款项仍待处理",
  );
  await page.getByRole("button", { name: /退款与待处理\s*4/ }).click();
  await expect(page.locator(".order-card")).toHaveCount(4);
  const closed = page
    .locator(".order-card")
    .filter({ hasText: "校园小摊-closed" });
  await expect(closed.locator(".payment-summary")).toHaveText(
    "模拟 · 退款已关闭 · 款项尚未退回",
  );
  await expect(closed).toContainText("模拟订单");
  await expect(closed).toContainText("查看退款进度");
  await expect(
    page.locator(".order-card").filter({ hasText: "校园小摊-abnormal" }),
  ).toContainText("退款异常 · 请联系商家处理");
  await expect(
    page.locator(".order-card").filter({ hasText: "校园小摊-refunded" }),
  ).toHaveCount(0);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 950 });
    await assertNoHorizontalOverflow(page);
    await expect(
      page.getByRole("button", { name: /退款与待处理\s*4/ }),
    ).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`student-followup-${width}.png`),
        fullPage: true,
      });
  }
  await page.getByRole("button", { name: /^进行中/ }).click();
  await expect(page.locator(".order-card")).toHaveCount(1);
  await expect(page.locator(".order-card")).toContainText("校园小摊-preparing");
  await page.getByRole("button", { name: /退款与待处理/ }).click();
  await closed.click();
  await expect(page).toHaveURL(/\/orders\/closed$/);
  await expect(
    page.getByText("模拟退款需要商家处理", { exact: true }),
  ).toBeVisible();
  expect(state.mutations).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("a terminal closed refund automatically reads a later successful result without payment requests", async ({
  page,
}) => {
  await page.clock.install();
  const closed = order("closed");
  const state = await fixture(page, [closed]);
  await page.goto("/orders/closed");
  await expect(
    page.getByRole("heading", { name: "退款未完成，请联系商家" }),
  ).toBeVisible();
  await expect(
    page.getByText("退款需要商家处理", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".student-payment")).not.toContainText(
    "微信支付已确认",
  );
  await expect(page.locator(".receipt-total")).toContainText("尚未退回金额");
  await expect(
    page.getByRole("button", { name: "刷新退款状态", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("link", { name: "联系商家", exact: true }),
  ).toHaveAttribute("href", "tel:13800138000");
  closed.refund.status = "success";
  closed.payment_status = "refunded";
  await page.clock.fastForward(10_100);
  await expect(page.getByText("微信退款已成功", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "刷新退款状态", exact: true }),
  ).toHaveCount(0);
  expect(state.reads).toBeGreaterThanOrEqual(2);
  expect(state.mutations).toEqual([]);
});

test("completed orders waiting for manual review read the cleared state when the page regains focus", async ({
  page,
}) => {
  const review = order("review", {
    status: "completed",
    refund: null,
    payment_review_required: true,
  });
  const state = await fixture(page, [review]);
  await page.goto("/orders/review");
  await expect(
    page.getByText("这笔款项需要核对", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "刷新款项处理状态", exact: true }),
  ).toBeEnabled();
  review.payment_review_required = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("微信支付已确认", { exact: true })).toBeVisible();
  await expect(page.getByText("这笔款项需要核对", { exact: true })).toHaveCount(
    0,
  );
  expect(state.mutations).toEqual([]);
});

test("financial refresh failure keeps the previous warning and retry only reads server results", async ({
  page,
}) => {
  const abnormal = order("abnormal", {
    mode: "simulation",
    payment_status: "refunding",
    refund: { status: "abnormal", amount_cents: 1600 },
  });
  const state = await fixture(page, [abnormal]);
  await page.goto("/orders/abnormal");
  await expect(
    page.getByText("模拟退款需要商家处理", { exact: true }),
  ).toBeVisible();
  state.failReads = true;
  await page.getByRole("button", { name: "刷新退款状态", exact: true }).click();
  await expect(
    page.locator(".student-payment").getByRole("alert"),
  ).toContainText("未获取最新处理结果，当前显示上次记录");
  await expect(page.getByText("模拟退款已完成", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "刷新退款状态", exact: true }),
  ).toBeEnabled();
  state.failReads = false;
  abnormal.refund.status = "success";
  abnormal.payment_status = "refunded";
  await page.getByRole("button", { name: "刷新退款状态", exact: true }).click();
  await expect(page.getByText("模拟退款已完成", { exact: true })).toBeVisible();
  await expect(page.getByText("微信退款已成功", { exact: true })).toHaveCount(
    0,
  );
  expect(state.mutations).toEqual([]);
});

test("ready orders with an unresolved refund do not display a collection code and remain readable", async ({
  page,
}, info) => {
  const state = await fixture(page, [
    order("ready-refund", { status: "ready", mode: "simulation" }),
  ]);
  await page.goto("/orders/ready-refund");
  await expect(
    page.getByText("模拟退款需要商家处理", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".pickup-code")).toHaveCount(0);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 950 });
    await assertNoHorizontalOverflow(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    if (width === 390)
      await page.screenshot({
        path: info.outputPath("student-refund-detail-390.png"),
        fullPage: true,
      });
  }
  expect(state.mutations).toEqual([]);
});

test("completed refunds leave the follow-up count only after a server refresh", async ({
  page,
}) => {
  const refund = order("closed");
  const state = await fixture(page, [refund]);
  await page.goto("/orders");
  await page.getByRole("button", { name: /退款与待处理\s*1/ }).click();
  refund.refund.status = "success";
  refund.payment_status = "refunded";
  await expect(page.locator(".order-card")).toHaveCount(1);
  await page.getByRole("button", { name: "刷新订单", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "目前没有待处理的款项", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".financial-notice")).toHaveCount(0);
  await page.getByRole("button", { name: "全部订单", exact: true }).click();
  await expect(page.locator(".order-card")).toContainText("已退款");
  expect(state.mutations).toEqual([]);
});

test("arrived delivery with a closed refund cannot expose a code or confirm receipt", async ({
  page,
}) => {
  const state = await fixture(page, [
    order("arrived-refund", {
      status: "arrived",
      mode: "simulation",
      fulfillment_type: "delivery",
      delivery_point_name: "南门交接点",
      delivery_point_address: "南门测试位置",
      items_total_cents: 1400,
      delivery_fee_cents: 200,
    }),
  ]);
  await page.goto("/orders/arrived-refund");
  await expect(
    page.getByText("模拟退款需要商家处理", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "本单款项仍待处理，请先联系商家核对，暂时不要重复付款或分享收餐码。",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByLabel("收餐码", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "模拟收到餐点", exact: true }),
  ).toHaveCount(0);
  expect(state.mutations).toEqual([]);
});

test("terminal reconcile records keep an explicit pending-payment-result label regardless of paid summary", async ({
  page,
}) => {
  const unpaid = order("reconcile-unpaid", {
    mode: "simulation",
    payment_status: "unpaid",
    refund: null,
  });
  const paid = order("reconcile-paid", {
    status: "completed",
    payment_status: "paid",
    refund: null,
  });
  unpaid.payment.status = "reconcile";
  paid.payment.status = "reconcile";
  const state = await fixture(page, [unpaid, paid]);
  await page.goto("/orders");
  await page.getByRole("button", { name: /退款与待处理\s*2/ }).click();
  await expect(page.locator(".order-card")).toHaveCount(2);
  for (const item of [unpaid, paid]) {
    const card = page
      .locator(".order-card")
      .filter({ hasText: item.stall_name });
    await expect(card.locator(".payment-summary")).toHaveText(
      `${item.mode === "simulation" ? "模拟 · " : ""}微信付款结果待确认，请勿重复付款`,
    );
    await expect(card).toContainText("查看付款进度");
    await expect(card.locator(".payment-summary")).not.toContainText("已付款");
  }
  expect(state.mutations).toEqual([]);
});
