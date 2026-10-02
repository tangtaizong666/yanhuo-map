import {
  expect,
  test,
  type Browser,
  type BrowserContext,
} from "@playwright/test";
import {
  assertNoHorizontalOverflow,
  baseURL,
  correctStock,
  createStudent,
  mutate,
} from "./helpers";

async function fixture(browser: Browser, studentContext: BrowserContext) {
  const student = await createStudent(studentContext);
  const signedIn = await mutate(studentContext, "/auth/login", student);
  expect(signedIn.ok(), await signedIn.text()).toBeTruthy();
  const merchant = await browser.newContext({ baseURL });
  const merchantLogin = await mutate(merchant, "/auth/login", {
    username: "vendor",
    password: "demo12345",
  });
  expect(merchantLogin.ok(), await merchantLogin.text()).toBeTruthy();
  const stalls = await (
    await merchant.request.get(`${baseURL}/api/v1/merchant/stalls`)
  ).json();
  const stall = stalls.find((item: any) => item.transaction_enabled);
  expect(stall).toBeTruthy();
  const opened = await mutate(merchant, `/merchant/stalls/${stall.id}/status`, {
    status: "open",
    confirm_location: true,
    closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  const products: any[] = [];
  const createdOrderIds: string[] = [];
  const token = Date.now();
  for (const [index, price] of [1000, 800, 600, 500].entries()) {
    const created = await mutate(
      merchant,
      `/merchant/stalls/${stall.id}/products`,
      {
        name: `再来一单验证餐点${index + 1}-${token}`,
        price_cents: price,
        stock: 20,
        image:
          stall.products.find((item: any) => item.image)?.image || stall.image,
        category: "测试餐点",
        description: "自动化验证专用，测试结束后下架。",
      },
    );
    expect(created.status(), await created.text()).toBe(201);
    products.push(await created.json());
  }
  async function update(product: any, values: any) {
    const { stock, ...details } = values;
    if (Object.keys(details).length) {
      const result = await mutate(
        merchant,
        `/merchant/products/${product.id}`,
        details,
        "PATCH",
      );
      expect(result.ok(), await result.text()).toBeTruthy();
    }
    if (stock !== undefined) {
      const result = await correctStock(merchant, product.id, stock);
      expect(result.ok(), await result.text()).toBeTruthy();
    }
  }
  async function create(
    items: { product: any; quantity: number }[],
    suffix: string,
  ) {
    const response = await mutate(studentContext, "/orders", {
      stall_id: stall.id,
      idempotency_key: `reorder-${student.username}-${suffix}`,
      items: items.map(({ product, quantity }) => ({
        product_id: product.id,
        quantity,
        expected_price_cents: product.price_cents,
      })),
    });
    expect(response.status(), await response.text()).toBe(201);
    const order = await response.json();
    createdOrderIds.push(order.id);
    return order;
  }
  async function cleanup() {
    // Only clean up orders created by this fixture and owned by this test student.
    for (const id of createdOrderIds) {
      const response = await studentContext.request.get(
        `${baseURL}/api/v1/orders/${id}`,
      );
      expect(response.ok(), await response.text()).toBeTruthy();
      const order = await response.json();
      if (order.status === "ready" && order.payment_status === "paid") {
        const finished = await mutate(
          merchant,
          `/merchant/orders/${id}/action`,
          {
            action: "complete",
            pickup_code: order.pickup_code,
          },
        );
        expect(finished.ok(), await finished.text()).toBeTruthy();
      } else if (["pending", "preparing", "ready"].includes(order.status)) {
        const cancelled = await mutate(studentContext, `/orders/${id}/cancel`, {
          reason: "再来一单测试结束，清理本测试订单",
        });
        expect(cancelled.ok(), await cancelled.text()).toBeTruthy();
        if (order.status !== "pending") {
          const accepted = await mutate(
            merchant,
            `/merchant/orders/${id}/action`,
            { action: "approve_cancel" },
          );
          expect(accepted.ok(), await accepted.text()).toBeTruthy();
        }
      }
    }
    for (const product of products) await update(product, { is_active: false });
    const restored = await mutate(
      merchant,
      `/merchant/stalls/${stall.id}/status`,
      {
        status: stall.session_status || "open",
        confirm_location: true,
        closes_at:
          stall.closes_at && Date.parse(stall.closes_at) > Date.now()
            ? stall.closes_at
            : null,
      },
    );
    expect(restored.ok(), await restored.text()).toBeTruthy();
    await merchant.close();
  }
  return { stall, stalls, products, merchant, create, update, cleanup };
}

test("reordering reviews current prices and skipped items, then merges only available quantities without creating an order", async ({
  page,
  context,
  browser,
}, testInfo) => {
  const f = await fixture(browser, context);
  try {
    const [changedPrice, soldOut, removed, existingOther] = f.products;
    const order = await f.create(
      [
        { product: changedPrice, quantity: 3 },
        { product: soldOut, quantity: 1 },
        { product: removed, quantity: 1 },
      ],
      "merge",
    );
    const cancelled = await mutate(context, `/orders/${order.id}/cancel`, {
      reason: "验证再来一单",
    });
    expect(cancelled.ok(), await cancelled.text()).toBeTruthy();
    await f.update(changedPrice, { price_cents: 1350, stock: 3 });
    await f.update(soldOut, { stock: 0 });
    await f.update(removed, { is_active: false });
    const otherStall = f.stalls.find(
      (item: any) => item.id !== f.stall.id && item.products.length,
    );
    expect(otherStall).toBeTruthy();
    const otherDraft = [{ product: otherStall.products[0], quantity: 1 }];
    const draft = {
      [f.stall.id]: [
        { product: changedPrice, quantity: 2 },
        { product: existingOther, quantity: 2 },
      ],
      [otherStall.id]: otherDraft,
    };
    await page.goto(`/orders/${order.id}`);
    await page.evaluate(
      (value) => localStorage.setItem("yanhuo-cart-v1", JSON.stringify(value)),
      draft,
    );
    await page.reload();
    const trigger = page.getByRole("button", { name: "再来一单", exact: true });
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "再来一单", exact: true });
    const changedRow = dialog.locator(`[data-product-id="${changedPrice.id}"]`);
    await expect(changedRow).toContainText("现价 ¥13.5");
    await expect(changedRow).toContainText("上次 ¥10");
    await expect(changedRow).toContainText("餐袋已有 2 份");
    await expect(changedRow).toContainText("合并后 3 份");
    await expect(changedRow).toContainText("本次可加 1 份");
    await expect(
      dialog.locator(`[data-product-id="${soldOut.id}"]`),
    ).toContainText("线上份数已售罄，本次跳过");
    await expect(
      dialog.locator(`[data-product-id="${removed.id}"]`),
    ).toContainText("已下架或暂时不可售，本次跳过");
    await expect(dialog).toContainText("餐袋中同款的价格也会同步为上述现价");
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await assertNoHorizontalOverflow(page);
      const button = dialog.getByRole("button", {
        name: "确认加入餐袋",
        exact: true,
      });
      expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await page.screenshot({
        path: testInfo.outputPath(`reorder-${width}.png`),
      });
    }
    // Native Escape dismissal restores the trigger and must not alter any draft.
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(trigger).toBeFocused();
    expect(
      await page.evaluate(() =>
        JSON.parse(localStorage.getItem("yanhuo-cart-v1") || "{}"),
      ),
    ).toEqual(draft);
    await trigger.click();
    await expect(changedRow).toBeVisible();
    const before = await (
      await context.request.get(`${baseURL}/api/v1/orders`)
    ).json();
    expect(before).toHaveLength(1);
    await dialog
      .getByRole("button", { name: "确认加入餐袋", exact: true })
      .click();
    await expect(page).toHaveURL(/\/cart$/);
    const saved = await page.evaluate(() =>
      JSON.parse(localStorage.getItem("yanhuo-cart-v1") || "{}"),
    );
    expect(
      saved[f.stall.id].find((row: any) => row.product.id === changedPrice.id),
    ).toMatchObject({ product: { price_cents: 1350 }, quantity: 3 });
    expect(
      saved[f.stall.id].find((row: any) => row.product.id === existingOther.id),
    ).toEqual({
      product: existingOther,
      quantity: 2,
      portions: [
        { options: {}, note: "" },
        { options: {}, note: "" },
      ],
    });
    expect(saved[f.stall.id]).toHaveLength(2);
    expect(saved[otherStall.id]).toEqual(
      otherDraft.map((row) => ({
        ...row,
        portions: [{ options: {}, note: "" }],
      })),
    );
    const after = await (
      await context.request.get(`${baseURL}/api/v1/orders`)
    ).json();
    expect(after).toHaveLength(1);
    expect(after[0].total_cents).toBe(order.total_cents);
    expect(
      after[0].items.find((item: any) => item.product_id === changedPrice.id)
        .unit_price_cents,
    ).toBe(1000);
    const liveStall = await (
      await context.request.get(`${baseURL}/api/v1/stalls/${f.stall.id}`)
    ).json();
    expect(
      liveStall.products.find((item: any) => item.id === changedPrice.id).stock,
    ).toBe(3);
  } finally {
    await f.cleanup();
  }
});

test("reorder appears only after an order ends, requires a second review after menu changes, and handles unavailable or offline stalls", async ({
  page,
  context,
  browser,
}) => {
  const f = await fixture(browser, context);
  try {
    const product = f.products[0];
    const order = await f.create([{ product, quantity: 1 }], "states");
    await page.goto(`/orders/${order.id}`);
    await expect(
      page.getByRole("heading", { name: "订单已提交，等待商家接单" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "再来一单", exact: true }),
    ).toHaveCount(0);
    const rejected = await mutate(
      f.merchant,
      `/merchant/orders/${order.id}/action`,
      { action: "reject" },
    );
    expect(rejected.ok(), await rejected.text()).toBeTruthy();
    await page.getByRole("button", { name: "刷新订单状态" }).click();
    const trigger = page.getByRole("button", { name: "再来一单", exact: true });
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "再来一单", exact: true });
    await expect(dialog).toContainText("现价 ¥10");
    await f.update(product, { price_cents: 1100 });
    await dialog
      .getByRole("button", { name: "确认加入餐袋", exact: true })
      .click();
    await expect(dialog).toContainText("菜单、库存或餐袋发生变化");
    await expect(dialog).toContainText("现价 ¥11");
    expect(
      await page.evaluate(() => localStorage.getItem("yanhuo-cart-v1")),
    ).toBeNull();
    await page.keyboard.press("Escape");
    await context.setOffline(true);
    await trigger.click();
    await expect(
      dialog.getByText("暂时无法核对餐点", { exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "确认加入餐袋", exact: true }),
    ).toHaveCount(0);
    await context.setOffline(false);
    await dialog.getByRole("button", { name: "重新加载", exact: true }).click();
    await expect(dialog).toContainText("现价 ¥11");
    await page.keyboard.press("Escape");
    await f.update(product, { stock: 0 });
    await trigger.click();
    await expect(dialog).toContainText("这次暂时没有可添加的餐点");
    await expect(
      dialog.getByRole("button", { name: "确认加入餐袋", exact: true }),
    ).toHaveCount(0);
    await page.keyboard.press("Escape");
    await f.update(product, { stock: 20, price_cents: 1000 });
    const closed = await mutate(
      f.merchant,
      `/merchant/stalls/${f.stall.id}/status`,
      { status: "closed", confirm_location: false },
    );
    expect(closed.ok(), await closed.text()).toBeTruthy();
    await trigger.click();
    await expect(dialog).toContainText("摊位已收摊");
    await expect(
      dialog.getByRole("button", { name: "确认加入餐袋", exact: true }),
    ).toHaveCount(0);
    await expect(
      dialog.getByRole("link", { name: "去看看当前菜单" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    const opened = await mutate(
      f.merchant,
      `/merchant/stalls/${f.stall.id}/status`,
      { status: "open", confirm_location: true },
    );
    expect(opened.ok(), await opened.text()).toBeTruthy();
    const completed = await f.create([{ product, quantity: 1 }], "completed");
    for (const action of ["accept", "ready", "confirm_payment"]) {
      const result = await mutate(
        f.merchant,
        `/merchant/orders/${completed.id}/action`,
        { action },
      );
      expect(result.ok(), await result.text()).toBeTruthy();
    }
    const readyResponse = await context.request.get(
      `${baseURL}/api/v1/orders/${completed.id}`,
    );
    expect(readyResponse.ok(), await readyResponse.text()).toBeTruthy();
    const readyOrder = await readyResponse.json();
    expect(readyOrder.pickup_code).toMatch(/^\d{8}$/);
    const finished = await mutate(
      f.merchant,
      `/merchant/orders/${completed.id}/action`,
      {
        action: "complete",
        pickup_code: readyOrder.pickup_code,
      },
    );
    expect(finished.ok(), await finished.text()).toBeTruthy();
    await page.goto(`/orders/${completed.id}`);
    await expect(
      page.getByRole("heading", { name: "这一餐，刚刚好" }),
    ).toBeVisible();
    await expect(trigger).toBeVisible();
  } finally {
    await context.setOffline(false);
    await f.cleanup();
  }
});
