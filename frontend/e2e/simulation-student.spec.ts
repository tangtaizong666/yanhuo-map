import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// UI contract fixtures only: financial state transitions are also tested against
// the real simulation provider in simulation-live.spec.ts and Django tests.
function order(overrides: Record<string, any> = {}): any {
  return {
    id: "simulation-ui",
    number: "SIM-UI",
    mode: "simulation",
    stall_id: 991,
    stall_name: "模拟小摊",
    status: "pending_payment",
    fulfillment_type: "delivery",
    payment_method: "wechat",
    payment_status: "unpaid",
    payment_review_required: false,
    payment_can_close: false,
    wechat_payment: {
      mode: "simulation",
      available: true,
      channels: ["simulation"],
    },
    payment: null,
    refund: null,
    total_cents: 1800,
    items_total_cents: 1600,
    delivery_fee_cents: 200,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 600000).toISOString(),
    pickup_code: "123456",
    pickup_address: "示例小摊",
    contact_phone: "13800000000",
    delivery_point_name: "模拟校园交接点",
    delivery_point_address: "演练位置，不会送货",
    delivery_point_latitude: 45.7,
    delivery_point_longitude: 126.6,
    recipient_name: "演练同学",
    cancel_requested: false,
    cancel_reason: "",
    review: null,
    items: [
      {
        product_id: 991,
        name: "模拟烤冷面",
        image: "/images/food-cold-noodles.jpg",
        unit_price_cents: 1600,
        quantity: 1,
      },
    ],
    ...overrides,
  };
}
function attempt(id = "sim-attempt"): any {
  return {
    id,
    mode: "simulation",
    channel: "simulation",
    status: "pending",
    expires_at: new Date(Date.now() + 600000).toISOString(),
    code_url: "",
    h5_url: "",
    error_message: "",
  };
}
async function fixture(
  page: Page,
  state: any,
  mutate?: (kind: string, body: any) => Promise<void>,
) {
  const calls: { kind: string; body: any }[] = [];
  const unexpected: string[] = [];
  const user = {
    id: 991,
    username: "sim_ui",
    display_name: "模拟同学",
    is_merchant: false,
    is_staff: false,
  };
  await page.route("https://**/*", (route) => route.abort());
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
        services_simulation_enabled: true,
        brand: "烟火地图",
        amap_key: "",
        areas: [],
        user,
      });
    if (path === "/auth/me") return send(user);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/orders/active-summary") return send([]);
    if (path === "/stalls/991") return send({ id: 991, contact_phone: "" });
    if (path === "/orders") return send([state]);
    if (path === `/orders/${state.id}`) return send(state);
    const match = path.match(/\/payments\/(wechat|simulate|sync|close)$/);
    if (match) {
      const kind = match[1]!;
      const body = route.request().postDataJSON();
      calls.push({ kind, body });
      if (mutate) await mutate(kind, body);
      return send(state);
    }
    if (path.endsWith("/confirm-receipt")) {
      state.status = "completed";
      return send(state);
    }
    unexpected.push(path);
    return send({ detail: "Unexpected test API request" }, 500);
  });
  await page.goto(`/orders/${state.id}`);
  await expect(
    page.getByRole("heading", { name: "支付方式", exact: true }),
  ).toBeVisible();
  return { calls, unexpected };
}

test("simulation: opens a local cashier, prevents double submit and waits for the server result", async ({
  page,
}, testInfo) => {
  const state = order();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const f = await fixture(page, state, async (kind, body) => {
    if (kind === "wechat") {
      expect(body.channel).toBe("simulation");
      state.payment = attempt();
      state.payment_can_close = true;
    }
    if (kind === "simulate") {
      expect(body).toEqual({ payment_id: "sim-attempt", outcome: "success" });
      await gate;
      state.payment.status = "paid";
      state.payment_status = "paid";
      state.status = "pending";
    }
  });
  await page.getByRole("button", { name: "模拟微信付款", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "微信支付 · 模拟收银台" }),
  ).toBeVisible();
  await expect(page.getByAltText("本订单微信支付二维码")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "前往微信支付" })).toHaveCount(0);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 390, height: 900 });
  await page.locator(".simulation-cashier").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath("simulation-cashier-mobile.png"),
  });
  await page.getByRole("button", { name: "模拟支付成功", exact: true }).click();
  await expect(page.locator(".simulation-pay")).toBeDisabled();
  await expect(page.getByText("模拟付款已确认", { exact: true })).toHaveCount(
    0,
  );
  expect(f.calls.filter((c) => c.kind === "simulate")).toHaveLength(1);
  release();
  await expect(page.getByText("模拟付款已确认", { exact: true })).toBeVisible();
  await expect(page.locator(".receipt-total")).toContainText("模拟 · 已收款");
  expect(f.unexpected).toEqual([]);
});

test("simulation: unresolved payment persists through refresh and resolves with the same payment id", async ({
  page,
}) => {
  const state = order({ payment: attempt(), payment_can_close: true });
  const f = await fixture(page, state, async (kind, body) => {
    if (kind === "simulate") {
      expect(body.payment_id).toBe("sim-attempt");
      if (body.outcome === "pending") state.payment.status = "reconcile";
      else {
        state.payment.status = "paid";
        state.payment_status = "paid";
        state.status = "pending";
      }
    }
  });
  await page.getByText("试试其他付款情况", { exact: true }).click();
  await page.getByRole("button", { name: "模拟结果待确认" }).click();
  await expect(page.locator(".simulation-cashier")).toContainText(
    "订单会保留待付款状态",
  );
  await page.reload();
  await expect(page.locator(".simulation-cashier")).toContainText(
    "订单会保留待付款状态",
  );
  await page.getByRole("button", { name: "模拟支付成功", exact: true }).click();
  await expect(page.getByText("模拟付款已确认", { exact: true })).toBeVisible();
  expect(f.calls.filter((c) => c.kind === "wechat")).toHaveLength(0);
});

test("simulation: failed attempt can start a new attempt without recreating an order", async ({
  page,
}) => {
  const state = order({ payment: attempt(), payment_can_close: true });
  const f = await fixture(page, state, async (kind, body) => {
    if (kind === "simulate") {
      expect(body.outcome).toBe("failure");
      state.payment.status = "closed";
      state.payment_can_close = false;
    }
    if (kind === "wechat") {
      state.payment = attempt("sim-retry");
      state.payment_can_close = true;
    }
  });
  await page.getByText("试试其他付款情况", { exact: true }).click();
  await page.getByRole("button", { name: "模拟支付失败", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "模拟微信付款", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "模拟微信付款", exact: true }).click();
  await expect(page.locator(".simulation-cashier")).toBeVisible();
  expect(state.payment.id).toBe("sim-retry");
  expect(f.calls.map((c) => c.kind)).toEqual(["simulate", "wechat"]);
  expect(f.unexpected).toEqual([]);
});

test("simulation: refund and order list are marked simulated, never claim a real WeChat refund", async ({
  page,
}) => {
  const state = order({
    status: "cancelled",
    payment_status: "refunded",
    payment: { ...attempt(), status: "paid" },
    refund: {
      id: "r",
      mode: "simulation",
      status: "success",
      amount_cents: 1800,
    },
  });
  await fixture(page, state);
  await expect(page.getByText("模拟退款已完成", { exact: true })).toBeVisible();
  await expect(page.getByText("微信退款已成功", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.locator(".payment-note")).toContainText("没有实际资金变动");
  await page.goto("/orders");
  await expect(page.getByText("模拟订单", { exact: true })).toBeVisible();
});

test("simulation: delivery progress has no route to an example point and confirms only after simulated arrival", async ({
  page,
}) => {
  const state = order({
    status: "delivering",
    payment_status: "paid",
    payment: { ...attempt(), status: "paid" },
  });
  await fixture(page, state);
  await expect(
    page.getByRole("link", { name: "前往交接点的路线" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "模拟收到餐点" })).toHaveCount(
    0,
  );
  state.status = "arrived";
  await page.reload();
  await page.getByRole("button", { name: "模拟收到餐点" }).click();
  await page.getByRole("button", { name: "确认已收餐", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已收餐", exact: true }),
  ).toBeVisible();
});

test("simulation: historical live orders keep real payment UI even while the environment offers simulation", async ({
  page,
}) => {
  const state = order({
    mode: "live",
    status: "ready",
    fulfillment_type: "pickup",
    wechat_payment: { mode: "live", available: false, channels: [] },
  });
  await fixture(page, state);
  await expect(
    page.getByText("微信支付尚未开通", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".simulation-cashier")).toHaveCount(0);
  await expect(page.locator(".simulation-notice")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "模拟微信付款", exact: true }),
  ).toHaveCount(0);
});

test("simulation: lost success response reads the persisted result without submitting payment twice", async ({
  page,
}) => {
  const state = order({ payment: attempt(), payment_can_close: true });
  await fixture(page, state);
  let writes = 0;
  await page.route("**/payments/simulate", async (route) => {
    writes++;
    state.payment_status = "paid";
    state.payment.status = "paid";
    state.status = "pending";
    await route.abort("connectionreset");
  });
  await page.getByRole("button", { name: "模拟支付成功", exact: true }).click();
  await expect(page.getByText("模拟付款已确认", { exact: true })).toBeVisible();
  await expect(page.locator(".simulation-pay")).toHaveCount(0);
  expect(writes).toBe(1);
  await page.reload();
  await expect(page.getByText("模拟付款已确认", { exact: true })).toBeVisible();
  expect(writes).toBe(1);
});
