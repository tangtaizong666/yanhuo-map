import { expect, test, type Page, type Route } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Workbench integration only. All business APIs are intercepted; no test orders
// are written to the shared demonstration database.
function order(id: string, changes: Record<string, any> = {}): any {
  return {
    id,
    number: `FOLLOW-${id}`,
    stall_id: 921,
    stall_name: "校园好味摊",
    mode: "simulation",
    status: "completed",
    fulfillment_type: "pickup",
    payment_method: "wechat",
    payment_status: "paid",
    payment_review_required: false,
    payment: { id: `p-${id}`, status: "paid", mode: "simulation" },
    refund: null,
    delivery_issue: "",
    cancel_requested: false,
    cancel_reason: "",
    total_cents: 1600,
    items_total_cents: 1600,
    created_at: "2025-09-01T10:00:00+08:00",
    expires_at: new Date(Date.now() + 240000).toISOString(),
    pickup_address: "校园南门",
    current_address: "校园南门",
    note: "",
    review: null,
    items: [
      {
        product_id: 1,
        name: "烤冷面",
        image: "/images/food-cold-noodles.jpg",
        quantity: 1,
        unit_price_cents: 1600,
      },
    ],
    ...changes,
  };
}

async function fixture(page: Page) {
  const user = {
    id: 921,
    username: "followup_fixture",
    display_name: "体验商家",
    is_merchant: true,
    is_staff: false,
  };
  const state = {
    user: user as typeof user | null,
    failOrders: false,
    ordersReads: 0,
    secondOrders: [] as any[],
    holdFirst: false,
    held: undefined as Route | undefined,
    unexpected: [] as string[],
    orders: [
      ...Array.from({ length: 30 }, (_, i) =>
        order(`recent-${i}`, { created_at: new Date().toISOString() }),
      ),
      order("old-refund", {
        status: "cancelled",
        payment_status: "refunding",
        refund: {
          id: "r-old",
          status: "processing",
          reason: "商家同意取消",
          amount_cents: 1600,
        },
      }),
      order("closed-refund", {
        payment_review_required: true,
        refund: {
          id: "r-closed",
          status: "closed",
          reason: "退款异常",
          amount_cents: 1600,
        },
      }),
      order("delivery-issue", {
        fulfillment_type: "delivery",
        status: "delivering",
        delivery_issue: "交接点临时封闭",
      }),
      order("normal-payment", {
        status: "pending_payment",
        fulfillment_type: "delivery",
        payment_status: "unpaid",
        payment: { status: "pending" },
      }),
      order("resolved-delivery", {
        status: "cancelled",
        fulfillment_type: "delivery",
        payment_status: "refunded",
        refund: { id: "r-done", status: "success" },
        delivery_issue: "过去的配送说明",
      }),
      order("pending", {
        status: "pending",
        payment_status: "unpaid",
        payment_method: "offline",
        payment: null,
      }),
    ],
  };
  const stall = (id: number) => ({
    id,
    name: id === 921 ? "校园好味摊" : "第二家小摊",
    image: "/images/food-cold-noodles.jpg",
    area_name: "示例校园",
    status: "open",
    last_confirmed_at: new Date().toISOString(),
    transaction_enabled: true,
    prep_minutes: 10,
    address: "校园南门",
    latitude: 30,
    longitude: 120,
    products: [],
    reviews: [],
    rating: null,
    review_count: 0,
    services: { mode: "simulation" },
  });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname.replace("/api/v1", "");
    const send = (json: any, status = 200) => route.fulfill({ json, status });
    if (path === "/config")
      return send({
        brand: "烟火地图",
        demo_mode: true,
        services_simulation_enabled: true,
        user: state.user,
        areas: [],
        amap_key: "",
      });
    if (path === "/auth/me") return send(state.user);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/merchant/stalls") return send([stall(921), stall(922)]);
    if (path === "/merchant/orders") {
      state.ordersReads++;
      if (state.failOrders) return route.abort("failed");
      if (url.searchParams.get("stall") === "922")
        return send(state.secondOrders);
      if (state.holdFirst) {
        state.held = route;
        return;
      }
      return send(state.orders);
    }
    if (path === "/merchant/metrics")
      return send({
        mode: "simulation",
        today: { revenue_cents: 0, orders_created: 0, orders_completed: 0 },
        series: [],
        recent_payments: [],
        top_products: [],
        followers: 0,
      });
    state.unexpected.push(`${request.method()} ${path}`);
    return send({ detail: "Unexpected fixture request" }, 500);
  });
  return state;
}

test("workbench surfaces old unresolved orders once and keeps fulfilment and follow-up separate", async ({
  page,
}, testInfo) => {
  const state = await fixture(page);
  await page.goto("/merchant");
  const attention = page.getByRole("region", { name: "接单提醒", exact: true });
  const queue = page.getByTestId("merchant-followup-queue");
  await expect(attention).toHaveAttribute("data-followup-count", "3");
  await expect(attention).toHaveAttribute("data-pending-count", "1");
  await expect(queue.getByRole("heading")).toHaveText("售后跟进 3");
  await expect(page.locator(".m-recent-list")).not.toContainText("old-refund");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    const button = queue.getByRole("button", { name: "查看售后", exact: true });
    await expect(button).toBeEnabled();
    expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    if (width === 390 || width === 1440)
      await page
        .locator(".m-workqueue")
        .screenshot({
          path: testInfo.outputPath(`followup-workbench-${width}.png`),
        });
  }
  await queue.getByRole("button", { name: "查看售后", exact: true }).click();
  await expect(page).toHaveURL(/\/merchant\/orders\?filter=followup$/);
  await expect(page.locator("article.merchant-order")).toHaveCount(3);
  await expect(
    page
      .locator("article.merchant-order")
      .filter({ hasText: "FOLLOW-old-refund" }),
  ).toBeVisible();
  expect(state.unexpected).toEqual([]);
});

test("follow-up stays available across pages and disappears only after confirmed resolution", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant/analytics");
  const attention = page.getByRole("region", { name: "接单提醒", exact: true });
  await expect(attention).toHaveAttribute("data-followup-count", "3");
  await attention
    .getByRole("link", { name: "跟进售后 3", exact: true })
    .click();
  await expect(page).toHaveURL(/filter=followup/);
  state.orders = state.orders.map((item) => ({
    ...item,
    payment_review_required: false,
    delivery_issue: "",
    ...(item.refund
      ? {
          refund: { ...item.refund, status: "success" },
          payment_status: "refunded",
        }
      : {}),
  }));
  await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
  await expect(attention).toHaveAttribute("data-followup-count", "0");
  await expect(attention.getByRole("link", { name: /跟进售后/ })).toHaveCount(
    0,
  );
  await expect(page.locator("article.merchant-order")).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("failed refresh retains the old follow-up count with a stale warning, then recovers", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant");
  const attention = page.getByRole("region", { name: "接单提醒", exact: true });
  await expect(attention).toHaveAttribute("data-followup-count", "3");
  state.failOrders = true;
  await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
  await expect(attention).toContainText("订单同步中断");
  await expect(attention).toHaveAttribute("data-followup-count", "3");
  await expect(page.getByTestId("merchant-followup-queue")).toContainText(
    "上次同步结果",
  );
  state.failOrders = false;
  state.orders = [];
  await attention
    .getByRole("button", { name: "重新同步", exact: true })
    .click();
  await expect(attention).not.toContainText("订单同步中断");
  await expect(attention).toHaveAttribute("data-followup-count", "0");
});

test("a late old-stall read cannot repopulate the new stall's after-sales counts", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant");
  const attention = page.getByRole("region", { name: "接单提醒", exact: true });
  await expect(attention).toHaveAttribute("data-followup-count", "3");
  state.holdFirst = true;
  await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
  await expect.poll(() => !!state.held).toBe(true);
  await page.getByLabel("选择管理的摊位").selectOption("922");
  await expect(attention).toHaveAttribute("data-followup-count", "0");
  const delivered = page.waitForResponse(
    (reply) =>
      new URL(reply.url()).pathname.endsWith("/merchant/orders") &&
      new URL(reply.url()).searchParams.get("stall") === "921",
  );
  await state.held!.fulfill({ json: state.orders });
  await delivered;
  await expect(page.getByLabel("选择管理的摊位")).toHaveValue("922");
  await expect(attention).toHaveAttribute("data-followup-count", "0");
  await expect(page.getByTestId("merchant-followup-queue")).toContainText(
    "目前没有待跟进事项",
  );
});

test("an initial failed read does not claim there are no after-sales issues", async ({
  page,
}) => {
  const state = await fixture(page);
  state.failOrders = true;
  await page.goto("/merchant");
  const queue = page.getByTestId("merchant-followup-queue");
  await expect(queue).toContainText("尚未同步订单");
  await expect(
    queue.getByRole("button", { name: "查看售后", exact: true }),
  ).toBeDisabled();
  await expect(queue).not.toContainText("目前没有待跟进事项");
});
