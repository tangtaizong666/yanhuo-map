import { expect, test, type BrowserContext } from "@playwright/test";
import {
  assertNoHorizontalOverflow,
  baseURL,
  createStudent,
  login,
  mutate,
} from "./helpers";

async function prepare(student: BrowserContext, merchant: BrowserContext) {
  const config = await (
    await student.request.get(`${baseURL}/api/v1/config`)
  ).json();
  expect(
    config.demo_mode,
    "Rehearsal tests must not run against production",
  ).toBe(true);
  expect(
    config.services_simulation_enabled,
    "Start the local server with -Simulation",
  ).toBe(true);
  const credentials = await createStudent(student);
  expect(
    (
      await mutate(merchant, "/auth/login", {
        username: "vendor",
        password: "demo12345",
      })
    ).ok(),
  ).toBe(true);
  const stalls = await (
    await merchant.request.get(`${baseURL}/api/v1/merchant/stalls`)
  ).json();
  const stall = stalls.find(
    (s: any) =>
      s.services?.mode === "simulation" &&
      s.transaction_enabled &&
      s.products.some((p: any) => p.is_active !== false && p.stock > 0),
  );
  expect(stall, "Requires an eligible seeded demo stall").toBeTruthy();
  const opened = await mutate(merchant, `/merchant/stalls/${stall.id}/status`, {
    status: "open",
    confirm_location: true,
    closes_at: new Date(Date.now() + 7200000).toISOString(),
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const services = await mutate(
    merchant,
    `/merchant/stalls/${stall.id}/services`,
    { online_payment_enabled: true, delivery_enabled: true },
    "PATCH",
  );
  expect(services.ok(), await services.text()).toBeTruthy();
  const fresh = await (
    await student.request.get(`${baseURL}/api/v1/stalls/${stall.id}`)
  ).json();
  expect(fresh.delivery.mode).toBe("simulation");
  expect(fresh.delivery.available, fresh.delivery.reason).toBe(true);
  return {
    stall: fresh,
    product: fresh.products.find(
      (p: any) => p.is_active !== false && p.stock > 0,
    ),
    credentials,
  };
}

async function getOrder(context: BrowserContext, id: string) {
  return (await context.request.get(`${baseURL}/api/v1/orders/${id}`)).json();
}

test("live simulation: two devices complete checkout, failed/pending/retried payment and merchant delivery", async ({
  page,
  context,
  browser,
}, info) => {
  test.setTimeout(120000);
  const merchantContext = await browser.newContext({
    baseURL,
    viewport: { width: 390, height: 900 },
  });
  const merchant = await merchantContext.newPage();
  try {
    const { stall, product, credentials } = await prepare(
      context,
      merchantContext,
    );
    await page.goto("/login");
    await login(page, credentials.username, credentials.password);
    await page.goto(`/stalls/${stall.id}`);
    await page
      .getByRole("button", { name: `添加${product.name}`, exact: true })
      .click();
    await page.getByRole("link", { name: "去结算", exact: true }).click();
    await page.getByRole("button", { name: /商家配送/ }).click();
    await page
      .getByLabel("校园交接点")
      .selectOption(String(stall.delivery.points[0].id));
    await page.getByLabel("收餐人称呼").fill("模拟演练同学");
    await page.getByLabel("联系手机号").fill("13800000000");
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await assertNoHorizontalOverflow(page);
    }
    await page.setViewportSize({ width: 390, height: 900 });
    await page.screenshot({
      path: info.outputPath("simulation-checkout-mobile.png"),
      fullPage: true,
    });
    const responsePromise = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" && r.url().endsWith("/api/v1/orders"),
    );
    await page
      .getByRole("button", { name: "提交配送订单，去付款", exact: true })
      .click();
    const response = await responsePromise;
    expect(response.status(), await response.text()).toBe(201);
    const created = await response.json();
    expect(created.mode).toBe("simulation");
    expect(created.status).toBe("pending_payment");
    const originalStock = product.stock;
    await page
      .getByRole("button", { name: "模拟微信付款", exact: true })
      .click();
    await page.getByText("试试其他付款情况", { exact: true }).click();
    await page
      .getByRole("button", { name: "模拟支付失败", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "模拟微信付款", exact: true }),
    ).toBeEnabled();
    const failed = await getOrder(context, created.id);
    expect(failed.payment_status).toBe("unpaid");
    expect(failed.payment.status).toBe("closed");
    await page
      .getByRole("button", { name: "模拟微信付款", exact: true })
      .click();
    await page.getByText("试试其他付款情况", { exact: true }).click();
    await page
      .getByRole("button", { name: "模拟结果待确认", exact: true })
      .click();
    await expect(page.locator(".sim-pending")).toBeVisible();
    const pending = await getOrder(context, created.id);
    expect(pending.payment.id).not.toBe(failed.payment.id);
    expect(pending.payment_status).toBe("unpaid");
    await page.reload();
    await expect(page.locator(".sim-pending")).toBeVisible();
    await page
      .getByRole("button", { name: "模拟支付成功", exact: true })
      .click();
    await expect(
      page.getByText("模拟付款已确认", { exact: true }),
    ).toBeVisible();
    const paid = await getOrder(context, created.id);
    expect(paid.payment.id).toBe(pending.payment.id);
    expect(paid.status).toBe("pending");
    expect(paid.payment_status).toBe("paid");
    expect(paid.total_cents).toBe(
      product.price_cents + stall.delivery.fee_cents,
    );
    const reserved = await (
      await context.request.get(`${baseURL}/api/v1/stalls/${stall.id}`)
    ).json();
    expect(reserved.products.find((p: any) => p.id === product.id).stock).toBe(
      originalStock - 1,
    );

    await merchant.goto("/merchant/orders");
    await merchant.getByLabel("选择管理的摊位").selectOption(String(stall.id));
    const card = merchant
      .locator("article.merchant-order")
      .filter({ hasText: created.number });
    await card.getByRole("button", { name: "确认接单", exact: true }).click();
    await card
      .getByRole("button", { name: "做好了，准备送餐", exact: true })
      .click();
    await card
      .getByRole("button", { name: "模拟出发送餐", exact: true })
      .click();
    await merchant
      .getByRole("dialog", { name: "确认开始配送", exact: true })
      .getByRole("button", { name: "确认已出发", exact: true })
      .click();
    await card
      .getByRole("button", { name: "模拟到达交接点", exact: true })
      .click();
    await merchant
      .getByRole("dialog", { name: "确认已到达交接点", exact: true })
      .getByRole("button", { name: "确认已到达", exact: true })
      .click();
    await page.getByRole("button", { name: "刷新订单状态" }).click();
    await expect(
      page.getByRole("button", { name: "模拟收到餐点", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "前往交接点的路线" }),
    ).toHaveCount(0);
    await page.locator(".delivery-progress").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath("simulation-arrived-mobile.png"),
    });
    await page
      .getByRole("button", { name: "模拟收到餐点", exact: true })
      .click();
    await page.getByRole("button", { name: "确认已收餐", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "已收餐", exact: true }),
    ).toBeVisible();
    const done = await getOrder(context, created.id);
    expect(done.status).toBe("completed");
    expect(done.mode).toBe("simulation");
    const merchantOrders = await (
      await merchantContext.request.get(
        `${baseURL}/api/v1/merchant/orders?stall=${stall.id}`,
      )
    ).json();
    expect(merchantOrders.find((o: any) => o.id === done.id).status).toBe(
      "completed",
    );
    expect(merchantOrders.find((o: any) => o.id === done.id).pickup_code).toBe(
      "",
    );
    for (const width of [360, 390, 768, 1440]) {
      await merchant.setViewportSize({ width, height: 900 });
      await assertNoHorizontalOverflow(merchant);
    }
    await merchant.goto("/merchant/store");
    await merchant.getByLabel("选择管理的摊位").selectOption(String(stall.id));
    await merchant.screenshot({
      path: info.outputPath("simulation-store-desktop.png"),
      fullPage: true,
    });
  } finally {
    await merchantContext.close();
  }
});

test("live simulation: full refund failure and recovery keep one refund and restore inventory once", async ({
  context,
  browser,
  page,
}) => {
  const merchant = await browser.newContext({ baseURL });
  try {
    const { stall, product, credentials } = await prepare(context, merchant);
    expect((await mutate(context, "/auth/login", credentials)).ok()).toBe(true);
    const liveBefore = await (
      await merchant.request.get(
        `${baseURL}/api/v1/merchant/metrics?stall=${stall.id}&mode=live`,
      )
    ).json();
    const payload = {
      stall_id: stall.id,
      fulfillment_type: "delivery",
      delivery_point_id: stall.delivery.points[0].id,
      recipient_name: "退款演练",
      contact_phone: "13800000000",
      expected_delivery_fee_cents: stall.delivery.fee_cents,
      items: [
        {
          product_id: product.id,
          quantity: 1,
          expected_price_cents: product.price_cents,
        },
      ],
      idempotency_key: crypto.randomUUID(),
    };
    const createdResponse = await mutate(context, "/orders", payload);
    expect(createdResponse.status(), await createdResponse.text()).toBe(201);
    const created = await createdResponse.json();
    const duplicate = await (await mutate(context, "/orders", payload)).json();
    expect(duplicate.id).toBe(created.id);
    const start = await (
      await mutate(context, `/orders/${created.id}/payments/wechat`, {
        channel: "simulation",
      })
    ).json();
    expect(start.payment.mode).toBe("simulation");
    const paidResponse = await mutate(
      context,
      `/orders/${created.id}/payments/simulate`,
      { payment_id: start.payment.id, outcome: "success" },
    );
    expect(paidResponse.ok(), await paidResponse.text()).toBe(true);
    const paid = await paidResponse.json();
    expect(paid.status).toBe("pending");
    const refundResponse = await mutate(
      merchant,
      `/merchant/orders/${created.id}/refund`,
      { reason: "演练退款失败与恢复", simulation_outcome: "failure" },
    );
    expect(refundResponse.ok(), await refundResponse.text()).toBe(true);
    const failed = await refundResponse.json();
    expect(failed.refund.mode).toBe("simulation");
    expect(failed.refund.status).not.toBe("success");
    const recoveredResponse = await mutate(
      merchant,
      `/merchant/orders/${created.id}/refunds/simulate`,
      { refund_id: failed.refund.id, outcome: "success" },
    );
    expect(recoveredResponse.ok(), await recoveredResponse.text()).toBe(true);
    const recovered = await recoveredResponse.json();
    expect(recovered.refund.id).toBe(failed.refund.id);
    expect(recovered.refund.status).toBe("success");
    expect(recovered.refund.amount_cents).toBe(
      product.price_cents + stall.delivery.fee_cents,
    );
    expect(recovered.payment_status).toBe("refunded");
    const replay = await mutate(
      merchant,
      `/merchant/orders/${created.id}/refunds/simulate`,
      { refund_id: failed.refund.id, outcome: "success" },
    );
    expect(replay.ok(), await replay.text()).toBe(true);
    const fresh = await (
      await context.request.get(`${baseURL}/api/v1/stalls/${stall.id}`)
    ).json();
    expect(fresh.products.find((p: any) => p.id === product.id).stock).toBe(
      product.stock,
    );
    const liveAfter = await (
      await merchant.request.get(
        `${baseURL}/api/v1/merchant/metrics?stall=${stall.id}&mode=live`,
      )
    ).json();
    expect(liveAfter.revenue_cents).toBe(liveBefore.revenue_cents);
    await page.goto(`/orders/${created.id}`);
    await expect(
      page.getByText("模拟退款已完成", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".receipt-total")).toContainText("模拟 · 已退款");
  } finally {
    await merchant.close();
  }
});
