import { expect, test } from "@playwright/test";
import {
  assertNoHorizontalOverflow,
  baseURL,
  createStudent,
  login,
  mutate,
} from "./helpers";

test("student and merchant complete a real pickup order in separate browser sessions", async ({
  page,
  context,
  browser,
}, testInfo) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  const student = await createStudent(context);
  const merchantContext = await browser.newContext({
    baseURL,
    viewport: { width: 390, height: 844 },
  });
  const merchant = await merchantContext.newPage();
  try {
    await merchant.goto("/login?returnTo=/merchant");
    await login(merchant, "vendor", "demo12345");
    await expect(merchant).toHaveURL((url) => url.pathname === "/merchant");
    const availableResponse = await merchantContext.request.get(
      `${baseURL}/api/v1/merchant/stalls`,
    );
    expect(availableResponse.ok(), await availableResponse.text()).toBeTruthy();
    const available = await availableResponse.json();
    const stall = available.find(
      (item: any) =>
        item.transaction_enabled &&
        item.products.some(
          (product: any) => product.is_active !== false && product.stock > 0,
        ),
    );
    expect(
      stall,
      "Seeded vendor must have an eligible stall with inventory",
    ).toBeTruthy();
    const refreshed = await mutate(
      merchantContext,
      `/merchant/stalls/${stall.id}/status`,
      {
        status: "open",
        confirm_location: true,
        closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
      },
    );
    expect(refreshed.ok(), await refreshed.text()).toBeTruthy();
    const product = stall.products.find(
      (item: any) => item.is_active !== false && item.stock > 0,
    );
    await merchant.goto("/merchant/products");
    await merchant.getByLabel("选择管理的摊位").selectOption(String(stall.id));
    const productEditor = merchant.locator("article.merchant-product").filter({
      has: merchant.getByRole("heading", { name: product.name, exact: true }),
    });
    await expect(productEditor.locator('.stock-readout b')).toHaveText(String(product.stock));
    await expect(productEditor.getByRole('spinbutton', { name: `${product.name}库存`, exact: true })).toHaveCount(0);
    await productEditor.getByRole('button', { name: `编辑商品：${product.name}`, exact: true }).click();
    const productDialog = merchant.getByRole('dialog', { name: '编辑商品', exact: true });
    await expect(productDialog.getByRole('spinbutton', { name: /单价（元）/ })).toHaveValue((product.price_cents / 100).toFixed(2));

    // Start as a guest: adding a draft is public; checkout must require login,
    // and the chosen food must survive the authentication round trip.
    await page.goto(`/stalls/${stall.id}`);
    await page
      .getByRole("button", { name: `添加${product.name}`, exact: true })
      .click();
    await page.getByRole("link", { name: "去结算", exact: true }).click();
    await expect(page).toHaveURL(/\/login\?returnTo=/);
    await login(page, student.username, student.password);
    await expect(page).toHaveURL(new RegExp(`/checkout/${stall.id}$`));
    await page.goto('/cart');
    await page.getByRole('button', { name: '加入当前账号餐袋', exact: true }).click();
    await page.goto(`/checkout/${stall.id}`);
    await expect(
      page.locator('.pay-at-stall'),
    ).toBeVisible();
    await page.locator(".checkout-contact-card > summary").click();
    await page
      .getByPlaceholder("例如：餐具按需提供（每份口味请在上方分别填写）")
      .fill(`测试订单 ${student.username}，少辣`);
    for (const width of [360, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await assertNoHorizontalOverflow(page);
      await page.screenshot({
        path: testInfo.outputPath(`checkout-${width}.png`),
        fullPage: true,
      });
    }
    const createdPromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/api\/v1\/orders$/.test(response.url()),
    );
    await page
      .getByRole("button", { name: "提交自取订单", exact: true })
      .click();
    const created = await createdPromise;
    expect(created.status(), await created.text()).toBe(201);
    const order = await created.json();
    await expect(page).toHaveURL(new RegExp(`/orders/${order.id}$`));
    await expect(
      page.getByRole("heading", { name: "订单已提交，等待商家接单" }),
    ).toBeVisible();
    expect(order.total_cents).toBe(product.price_cents);

    // The merchant loaded this editor before the student's stock reservation.
    // A price-only update must never write that old stock count back.
    try {
      await productDialog
        .getByRole("spinbutton", { name: /单价（元）/ })
        .fill(((product.price_cents + 1) / 100).toFixed(2));
      const savedPromise = merchant.waitForResponse(
        (response) =>
          response.request().method() === "PATCH" &&
          response.url().endsWith(`/merchant/products/${product.id}`),
      );
      await productDialog
        .getByRole("button", { name: "保存修改", exact: true })
        .click();
      const saved = await savedPromise;
      expect(saved.status(), await saved.text()).toBe(200);
      expect(saved.request().postDataJSON()).toEqual({
        price_cents: product.price_cents + 1,
      });
      const afterPriceChange = await (
        await merchantContext.request.get(`${baseURL}/api/v1/merchant/stalls`)
      ).json();
      expect(
        afterPriceChange.find((item: any) => item.id === stall.id).products.find((item: any) => item.id === product.id)
          .stock,
      ).toBe(product.stock - 1);
      const unchangedOrder = await (
        await context.request.get(`${baseURL}/api/v1/orders/${order.id}`)
      ).json();
      expect(unchangedOrder.total_cents).toBe(product.price_cents);
    } finally {
      const restored = await mutate(
        merchantContext,
        `/merchant/products/${product.id}`,
        { price_cents: product.price_cents },
        "PATCH",
      );
      expect(restored.ok(), await restored.text()).toBeTruthy();
    }

    await merchant.goto("/merchant/orders");
    await merchant.getByLabel("选择管理的摊位").selectOption(String(stall.id));
    const merchantOrder = merchant
      .locator("article.merchant-order")
      .filter({ hasText: order.number });
    await expect(merchantOrder).toBeVisible();
    await expect(merchantOrder.locator(".m-orders-countdown")).toHaveText(
      /剩余 \d+:\d{2} 接单/,
    );
    const detailTrigger = merchantOrder.getByRole("button", {
      name: `查看订单 ${order.number} 详情`,
      exact: true,
    });
    await detailTrigger.click();
    const details = merchant.getByRole("dialog", {
      name: "订单详情",
      exact: true,
    });
    await expect(details).toBeVisible();
    await details.locator('.m-order-records > summary').click();
    await expect(
      details.getByText(order.pickup_address, { exact: true }),
    ).toBeVisible();
    await expect(
      details.getByText(`测试订单 ${student.username}，少辣`, { exact: false }),
    ).toBeVisible();
    await expect(details.locator(".m-orders-timeline")).toContainText(
      "顾客下单",
    );
    for (const width of [360, 390]) {
      await merchant.setViewportSize({ width, height: 844 });
      await assertNoHorizontalOverflow(merchant);
      await expect(
        details.getByRole("button", { name: "关闭订单详情", exact: true }),
      ).toBeVisible();
      await merchant.screenshot({
        path: testInfo.outputPath(`order-details-${width}.png`),
      });
    }
    await merchant.keyboard.press("Escape");
    await expect(details).not.toBeVisible();
    await expect(detailTrigger).toBeFocused();
    const { results: merchantStored } = await (
      await merchantContext.request.get(
        `${baseURL}/api/v1/merchant/orders?stall=${stall.id}&pagination=cursor`,
      )
    ).json();
    expect(
      merchantStored.find((item: any) => item.id === order.id).pickup_code,
    ).toBe("");
    await merchantOrder
      .getByRole("button", { name: "接单开始做", exact: true })
      .click();
    await merchant.getByRole('group', { name: '订单阶段', exact: true }).getByRole('button', { name: /制作中/ }).click();
    await expect(merchantOrder.locator(".merchant-order-status")).toHaveText(
      "制作中",
    );
    await page.getByRole("button", { name: "刷新订单状态" }).click();
    await expect(
      page.getByRole("heading", { name: "小摊正忙着，为你做一餐" }),
    ).toBeVisible();
    await merchantOrder
      .getByRole("button", { name: "做好了", exact: true })
      .click();
    await merchant.getByRole('group', { name: '订单阶段', exact: true }).getByRole('button', { name: /待取餐/ }).click();
    await expect(merchantOrder.locator(".merchant-order-status")).toHaveText(
      "待取餐",
    );
    await page.getByRole("button", { name: "刷新订单状态" }).click();
    await expect(page.locator(".pickup-code strong")).toHaveText(/^\d{8}$/);
    const pickupCode = await page.locator(".pickup-code strong").innerText();
    for (const width of [360, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await assertNoHorizontalOverflow(page);
      await page.screenshot({
        path: testInfo.outputPath(`pickup-code-${width}.png`),
        fullPage: true,
      });
    }

    // Payment and collection remain distinct, explicit merchant actions.
    await expect(merchantOrder.getByPlaceholder("输入取餐码")).toHaveCount(0);
    await merchantOrder.getByRole("button", { name: /^收款 ¥/ }).click();
    const receipt = merchant.getByRole("dialog", {
      name: "确认这笔线下收款",
      exact: true,
    });
    await expect(receipt).toBeVisible();
    const beforeReceipt = await (
      await context.request.get(`${baseURL}/api/v1/orders/${order.id}`)
    ).json();
    expect(beforeReceipt.payment_status).toBe("unpaid");
    expect(beforeReceipt.status).toBe("ready");
    await receipt
      .getByRole("button", { name: "确认收款", exact: true })
      .click();
    await expect(receipt).not.toBeVisible();
    await expect(merchantOrder.locator(".paid-tag")).toHaveText(/^(模拟 · )?到摊已收款$/);
    await expect(merchantOrder.locator(".merchant-order-status")).toHaveText(
      "待取餐",
    );
    await merchantOrder.getByRole('button', { name: '核对取餐码', exact: true }).click();
    await details.getByPlaceholder("输入取餐码").fill(pickupCode);
    for (const width of [360, 390]) {
      await merchant.setViewportSize({ width, height: 844 });
      await assertNoHorizontalOverflow(merchant);
      await merchant.screenshot({
        path: testInfo.outputPath(`merchant-${width}.png`),
        fullPage: true,
      });
    }
    await details.getByRole("button", { name: "核销并完成" }).click();
    await expect(merchantOrder).toHaveCount(0);
    await page.getByRole("button", { name: "刷新订单状态" }).click();
    await expect(
      page.getByRole("heading", { name: "这一餐，刚刚好" }),
    ).toBeVisible();
    await expect(page.locator('.receipt-total')).toContainText('已收款');
    await page.getByRole("button", { name: "4 星", exact: true }).click();
    const review = `测试评价 ${student.username}：餐点热乎，取餐方便。`;
    await page
      .getByPlaceholder("说说口味、分量，或是让你记住这个小摊的瞬间…")
      .fill(review);
    await page.getByRole("button", { name: "分享这份好味道" }).click();
    await expect(
      page.getByText("已评价，感谢你的分享", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(page.getByText(review, { exact: true })).toBeVisible();
    await assertNoHorizontalOverflow(page);
    const stored = await (
      await context.request.get(`${baseURL}/api/v1/orders/${order.id}`)
    ).json();
    expect(stored.status).toBe("completed");
    expect(stored.payment_status).toBe("paid");
    expect(stored.review.rating).toBe(4);
    expect(stored.items[0].unit_price_cents).toBe(product.price_cents);
    expect(stored.pickup_address).toBe(stall.address);
  } finally {
    await merchantContext.close();
  }
});

test("a pending order can be cancelled from the customer dialog", async ({
  page,
  context,
  browser,
}) => {
  const student = await createStudent(context);
  // This case also runs on its own, after the demo's location confirmation expires.
  const merchantContext = await browser.newContext({ baseURL });
  let fixtureStallId: number | null = null;
  let fixtureProduct: any;
  try {
    const signedIn = await mutate(merchantContext, "/auth/login", {
      username: "vendor",
      password: "demo12345",
    });
    expect(signedIn.ok(), await signedIn.text()).toBeTruthy();
    const available = await (
      await merchantContext.request.get(`${baseURL}/api/v1/merchant/stalls`)
    ).json();
    const eligible = available.find(
      (item: any) =>
        item.transaction_enabled &&
        item.products.some(
          (product: any) => product.is_active !== false && product.stock > 0,
        ),
    );
    expect(
      eligible,
      "The demo vendor needs an eligible stall with stock",
    ).toBeTruthy();
    fixtureStallId = eligible.id;
    fixtureProduct = eligible.products.find((product: any) => product.is_active !== false && product.stock > 0);
    const opened = await mutate(
      merchantContext,
      `/merchant/stalls/${eligible.id}/status`,
      {
        status: "open",
        confirm_location: true,
        closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
      },
    );
    expect(opened.ok(), await opened.text()).toBeTruthy();
  } finally {
    await merchantContext.close();
  }
  await page.goto("/login?returnTo=/orders");
  await login(page, student.username, student.password);
  await expect(page).toHaveURL(/\/orders$/);
  const stall = await (
    await context.request.get(`${baseURL}/api/v1/stalls/${fixtureStallId}`)
  ).json();
  expect(stall.can_order).toBe(true);
  const product = stall.products.find((item: any) => item.id === fixtureProduct.id && item.availability === 'available');
  expect(product).toBeTruthy();
  expect(product).not.toHaveProperty('stock');
  const created = await mutate(context, "/orders", {
    stall_id: stall.id,
    idempotency_key: `cancel-${student.username}`,
    items: [
      {
        product_id: product.id,
        quantity: 1,
        expected_price_cents: product.price_cents,
      },
    ],
  });
  expect(created.status(), await created.text()).toBe(201);
  const order = await created.json();
  await page.goto(`/orders/${order.id}`);
  await page.getByRole("button", { name: "取消订单", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page
    .getByPlaceholder("告诉商家你的原因…")
    .fill("自动化测试取消，尚未接单");
  await page.getByRole("button", { name: "确认取消", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "订单已取消", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "取消订单", exact: true }),
  ).toHaveCount(0);
  const verification = await browser.newContext({ baseURL });
  try {
    const signedIn = await mutate(verification, '/auth/login', { username: 'vendor', password: 'demo12345' });
    expect(signedIn.ok(), await signedIn.text()).toBeTruthy();
    const refreshed = await (await verification.request.get(`${baseURL}/api/v1/merchant/stalls`)).json();
    expect(refreshed.find((item: any) => item.id === stall.id).products.find((item: any) => item.id === product.id).stock).toBe(fixtureProduct.stock);
  } finally {
    await verification.close();
  }
});
