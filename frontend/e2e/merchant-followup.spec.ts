import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// All endpoints are isolated fixtures: no real orders, refunds or deliveries.
const timestamp = "2026-09-29T10:00:00+08:00";
function makeOrder(id: string, changes: Record<string, unknown> = {}) {
  return {
    id,
    number: `FOLLOW-${id}`,
    mode: "live",
    stall_id: 998,
    stall_name: "售后测试摊",
    fulfillment_type: "pickup",
    status: "cancelled",
    payment_method: "wechat",
    payment_status: "refunding",
    payment_review_required: false,
    payment: { id: `payment-${id}`, status: "paid", mode: "live" },
    refund: {
      id: `refund-${id}`,
      status: "processing",
      mode: "live",
      amount_cents: 1600,
      reason: "顾客申请，已协商",
      created_at: timestamp,
      completed_at: null,
      error_message: "",
    },
    total_cents: 1600,
    items_total_cents: 1600,
    delivery_fee_cents: 0,
    created_at: timestamp,
    accepted_at: timestamp,
    ready_at: timestamp,
    paid_at: timestamp,
    completed_at: null,
    expires_at: null,
    delivery_issue: "",
    cancel_requested: false,
    cancel_reason: "已协商取消",
    pickup_address: "示例校门取餐点",
    current_address: "示例校门取餐点",
    contact_phone: "",
    note: "",
    review: null,
    items: [
      {
        product_id: 998,
        name: "示例炒面",
        image: "/images/food-cold-noodles.jpg",
        unit_price_cents: 1600,
        quantity: 1,
      },
    ],
    ...changes,
  };
}
async function setup(
  page: Page,
  orders: any[],
  options: {
    path?: string;
    holdRefund?: boolean;
    refundSuccess?: boolean;
    failOrders?: boolean;
  } = {},
) {
  let user = {
    id: 998,
    username: "followup_fixture",
    display_name: "售后测试商家",
    is_merchant: true,
    is_staff: false,
  };
  const writes: { path: string; body: any }[] = [],
    unexpected: string[] = [];
  let reads = 0,
    release: (() => void) | undefined;
  let failOrders = !!options.failOrders;
  const stall = (id: number) => ({
    id,
    name: `售后测试摊 ${id}`,
    image: "/images/food-cold-noodles.jpg",
    area_name: "测试校门",
    status: "open",
    last_confirmed_at: timestamp,
    transaction_enabled: true,
    rating: null,
    review_count: 0,
    products: [],
    reviews: [],
  });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", "");
    const send = (body: any) => route.fulfill({ json: body });
    if (path === "/config")
      return send({
        brand: "烟火地图",
        demo_mode: true,
        services_simulation_enabled: true,
        user,
        areas: [],
        amap_key: "",
        amap_proxy: "",
      });
    if (path === "/auth/me") return send(user);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/merchant/stalls") return send([stall(998), stall(999)]);
    if (path === "/merchant/orders") {
      reads++;
      if (failOrders)
        return route.fulfill({
          status: 503,
          json: { detail: "隔离测试：订单暂时不可用" },
        });
      return send(
        user.id === 998
          ? orders.filter(
              (o) => String(o.stall_id) === url.searchParams.get("stall"),
            )
          : [],
      );
    }
    if (path === "/merchant/metrics")
      return send({
        mode: "live",
        today: { revenue_cents: 0, orders_created: 0, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    const match = path.match(
      /^\/merchant\/orders\/([^/]+)\/(refund|refunds\/simulate|action)$/,
    );
    if (match && req.method() === "POST") {
      const order = orders.find((o) => o.id === match[1]),
        body = req.postDataJSON();
      writes.push({ path, body });
      if (match[2] === "refund") {
        if (options.holdRefund)
          await new Promise<void>((resolve) => {
            release = resolve;
          });
        if (options.refundSuccess) {
          order.payment_status = "refunded";
          order.refund.status = "success";
          order.refund.completed_at = timestamp;
        }
      } else if (match[2] === "refunds/simulate") {
        if (body.refund_id !== order.refund.id)
          return route.fulfill({
            status: 409,
            json: { detail: "错误的原退款标识" },
          });
        order.payment_status = "refunded";
        order.refund.status = "success";
        order.refund.completed_at = timestamp;
      } else if (body.action === "resolve_delivery_issue")
        order.delivery_issue = "";
      else {
        unexpected.push(`${req.method()} ${path} ${body.action}`);
        return route.fulfill({
          status: 500,
          json: { detail: "Unsupported action" },
        });
      }
      return send(order);
    }
    unexpected.push(`${req.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected fixture request" },
    });
  });
  await page.goto(options.path || "/merchant/orders?filter=followup");
  await expect(
    page.getByRole("heading", { name: "订单处理.", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".m-stall-bar")).toBeVisible();
  return {
    writes,
    unexpected,
    orders,
    readCount: () => reads,
    failOrders: (value: boolean) => {
      failOrders = value;
    },
    release: () => release?.(),
    changeAccount: () => {
      user = { ...user, id: 1998, display_name: "另一商家" };
    },
  };
}
const card = (page: Page, id: string) =>
  page.locator(".merchant-order", {
    has: page.locator(".order-number", { hasText: `FOLLOW-${id}` }),
  });
const followupTab = (page: Page) =>
  page
    .getByRole("group", { name: "订单状态筛选" })
    .getByRole("button", { name: /^售后跟进/ });

test("direct follow-up entry never claims zero before sync and retains known counts after refresh failure", async ({
  page,
}) => {
  const fixture = await setup(page, [makeOrder("recover")], {
    failOrders: true,
  });
  await expect(
    page.getByRole("heading", { name: "订单尚未同步", exact: true }),
  ).toBeVisible();
  await expect(followupTab(page)).toContainText("—");
  await expect(followupTab(page)).not.toContainText("0");
  await expect(
    page.getByRole("heading", { name: "当前筛选下没有待跟进的售后" }),
  ).toHaveCount(0);
  await expect(page.locator(".m-orders-list-caption")).toContainText(
    "— 笔订单",
  );
  fixture.failOrders(false);
  await page.getByRole("button", { name: "重新同步订单", exact: true }).click();
  await expect(card(page, "recover")).toBeVisible();
  await expect(followupTab(page)).toContainText("1");
  fixture.failOrders(true);
  await page.getByRole("button", { name: "刷新订单", exact: true }).click();
  await expect(page.locator(".m-orders-list-caption")).toContainText(
    "当前显示上次同步",
  );
  await expect(card(page, "recover")).toBeVisible();
  await expect(followupTab(page)).toContainText("1");
  expect(fixture.writes).toEqual([]);
  expect(fixture.unexpected).toEqual([]);
});

test("follow-up includes unfinished refunds across terminal states and deduplicates simultaneous issues", async ({
  page,
}, testInfo) => {
  const closed = makeOrder("closed", {
    status: "completed",
    completed_at: timestamp,
    payment_status: "paid",
  });
  closed.refund.status = "closed";
  const review = makeOrder("review", {
    status: "completed",
    payment_status: "paid",
    refund: null,
    payment_review_required: true,
  });
  const multiple = makeOrder("multiple", { payment_review_required: true });
  const waiting = makeOrder("waiting", {
    status: "pending_payment",
    payment_status: "unpaid",
    refund: null,
    payment: { id: "wait", status: "pending" },
  });
  const refunded = makeOrder("refunded", { payment_status: "refunded" });
  refunded.refund.status = "success";
  const historical = makeOrder("history", {
    fulfillment_type: "delivery",
    delivery_issue: "历史问题",
    payment_status: "refunded",
  });
  historical.refund.status = "success";
  const fixture = await setup(page, [
    makeOrder("processing"),
    closed,
    review,
    multiple,
    waiting,
    refunded,
    historical,
  ]);
  await expect(page.locator(".merchant-order")).toHaveCount(4);
  await expect(followupTab(page)).toContainText("4");
  await expect(card(page, "closed")).toContainText("已完成");
  await expect(card(page, "closed")).toContainText("退款未完成");
  await expect(
    card(page, "closed").locator(".merchant-payment-info"),
  ).not.toHaveClass(/(^|\s)paid(\s|$)/);
  await expect(card(page, "closed").locator(".paid-tag")).not.toHaveClass(
    /(^|\s)paid(\s|$)/,
  );
  await expect(
    card(page, "multiple").locator(".m-order-followup > span"),
  ).toHaveCount(2);
  for (const id of ["waiting", "refunded", "history"])
    await expect(card(page, id)).toHaveCount(0);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page
    .locator(".m-orders")
    .screenshot({ path: testInfo.outputPath("merchant-followup-desktop.png") });
  await page.setViewportSize({ width: 390, height: 900 });
  await card(page, "closed").evaluate((el) =>
    window.scrollTo({
      top: el.getBoundingClientRect().top + window.scrollY - 84,
      behavior: "instant",
    }),
  );
  await page.screenshot({
    path: testInfo.outputPath("merchant-followup-mobile.png"),
  });
  expect(fixture.unexpected).toEqual([]);
});

test("query existing refund is prominent only in follow-up and success leaves the queue", async ({
  page,
}) => {
  const order = makeOrder("processing");
  const fixture = await setup(page, [order], {
    path: "/merchant/orders?filter=all",
    refundSuccess: true,
  });
  await expect(
    card(page, "processing").getByRole("button", {
      name: "查询退款进度",
      exact: true,
    }),
  ).toBeHidden();
  await followupTab(page).click();
  await card(page, "processing")
    .getByRole("button", { name: "查询退款进度", exact: true })
    .click();
  await expect(page.locator(".merchant-order")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "当前筛选下没有待跟进的售后" }),
  ).toBeVisible();
  expect(fixture.writes).toEqual([
    {
      path: "/merchant/orders/processing/refund",
      body: { reason: order.refund.reason },
    },
  ]);
  expect(order.refund.id).toBe("refund-processing");
  expect(order.status).toBe("cancelled");
  expect(fixture.unexpected).toEqual([]);
});

test("closed refund query stays discoverable without creating a replacement or claiming success", async ({
  page,
}) => {
  const order = makeOrder("closed", {
    status: "completed",
    payment_status: "paid",
  });
  order.refund.status = "closed";
  const fixture = await setup(page, [order]);
  await card(page, "closed")
    .getByRole("button", { name: "查询退款进度", exact: true })
    .click();
  await expect.poll(() => fixture.writes.length).toBe(1);
  await expect(card(page, "closed")).toContainText("退款未完成");
  await expect(card(page, "closed")).toContainText("款项尚未退还");
  await expect(
    card(page, "closed").getByRole("button", {
      name: "全额原路退款",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(page.locator(".toast.success")).toHaveCount(0);
  expect(order.refund.id).toBe("refund-closed");
  expect(order.status).toBe("completed");
});

test("simulation failure retains original refund and simulation success clears the follow-up", async ({
  page,
}) => {
  const order = makeOrder("simulation", { mode: "simulation" });
  order.refund.mode = "simulation";
  order.refund.status = "abnormal";
  const fixture = await setup(page, [order]);
  await expect(card(page, "simulation")).toContainText("模拟退款出现异常");
  await expect(card(page, "simulation")).toContainText("没有真实资金变动");
  await card(page, "simulation").locator(".m-order-more > summary").click();
  await card(page, "simulation")
    .getByRole("button", { name: "模拟退款成功", exact: true })
    .click();
  await expect(page.locator(".merchant-order")).toHaveCount(0);
  expect(fixture.writes).toEqual([
    {
      path: "/merchant/orders/simulation/refunds/simulate",
      body: { refund_id: "refund-simulation", outcome: "success" },
    },
  ]);
});

test("payment review and reconcile offer refresh without manual receipt or invented resolution", async ({
  page,
}) => {
  const review = makeOrder("review", {
    status: "ready",
    refund: null,
    payment_status: "paid",
    payment: { id: "p-review", status: "review" },
  });
  const pending = makeOrder("reconcile", {
    status: "ready",
    refund: null,
    payment_status: "unpaid",
    payment: { id: "p-reconcile", status: "reconcile" },
  });
  const fixture = await setup(page, [review, pending]);
  await expect(page.locator(".merchant-order")).toHaveCount(2);
  await expect(card(page, "review")).toContainText("联系运营核对");
  await expect(card(page, "reconcile")).toContainText(
    "请顾客在原订单中查询付款进度",
  );
  await expect(
    page.getByRole("button", {
      name: /^确认已收到|核销并完成|标记.*完成|全额原路退款$/,
    }),
  ).toHaveCount(0);
  const before = fixture.readCount();
  await card(page, "review")
    .getByRole("button", { name: "刷新订单状态", exact: true })
    .click();
  await expect.poll(fixture.readCount).toBeGreaterThan(before);
  expect(fixture.writes).toEqual([]);
});

test("delivery issue needs a resolution note and resolving it never completes the order", async ({
  page,
}) => {
  const order = makeOrder("delivery", {
    fulfillment_type: "delivery",
    status: "arrived",
    payment_status: "paid",
    refund: null,
    delivery_issue: "顾客暂未到交接点",
    delivery_point_name: "模拟南门交接点",
    delivery_point_address: "示例校园雨棚",
    recipient_name: "测试同学",
  });
  const fixture = await setup(page, [order]);
  await expect(
    card(page, "delivery").getByRole("button", {
      name: "核销并完成",
      exact: true,
    }),
  ).toHaveCount(0);
  await card(page, "delivery")
    .getByRole("button", { name: "异常已解决，继续处理", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "处理说明", exact: true })
    .fill("已与顾客联系，正在前来取餐");
  await page
    .getByRole("button", { name: "记录解决，继续履约", exact: true })
    .click();
  await expect(page.locator(".merchant-order")).toHaveCount(0);
  expect(fixture.writes).toEqual([
    {
      path: "/merchant/orders/delivery/action",
      body: {
        action: "resolve_delivery_issue",
        reason: "已与顾客联系，正在前来取餐",
      },
    },
  ]);
  expect(order.status).toBe("arrived");
  expect(order.completed_at).toBeNull();
});

test("missing refund details remain visible but only offer safe refresh", async ({
  page,
}) => {
  const fixture = await setup(page, [makeOrder("missing", { refund: null })]);
  await expect(card(page, "missing")).toContainText("退款状态待确认");
  await expect(
    card(page, "missing").getByRole("button", {
      name: "刷新订单状态",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    card(page, "missing").getByRole("button", {
      name: "查询退款进度",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(fixture.writes).toEqual([]);
});

test("late refund response cannot populate another stall and account change clears follow-up data", async ({
  page,
}) => {
  const fixture = await setup(page, [makeOrder("late")], {
    holdRefund: true,
    refundSuccess: true,
  });
  await card(page, "late")
    .getByRole("button", { name: "查询退款进度", exact: true })
    .click();
  await expect.poll(() => fixture.writes.length).toBe(1);
  await page.getByLabel("选择管理的摊位", { exact: true }).selectOption("999");
  await expect(page.locator(".merchant-order")).toHaveCount(0);
  fixture.release();
  await expect(page.locator(".merchant-order")).toHaveCount(0);
  fixture.orders.push(makeOrder("other"));
  await page.getByLabel("选择管理的摊位", { exact: true }).selectOption("998");
  await expect(card(page, "other")).toBeVisible();
  fixture.changeAccount();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.locator(".m-avatar")).toContainText("另");
  await expect(page.locator(".merchant-order")).toHaveCount(0);
  expect(fixture.unexpected).toEqual([]);
});
