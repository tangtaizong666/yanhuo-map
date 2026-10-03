import { expect, test } from "@playwright/test";
import { baseURL, createStudent, mutate } from "./helpers";

// This suite changes business settings. Only run against an explicitly isolated
// disposable demo database, never the user's working preview.
test.skip(
  process.env.E2E_ISOLATED !== "1",
  "Requires an isolated demo server and E2E_ISOLATED=1",
);

test("real counter API preserves paused stock, rejects stale corrections, and lookup UI completes only after payment", async ({
  browser,
  page,
  context,
}) => {
  const student = await browser.newContext({ baseURL });
  const otherStudent = await browser.newContext({ baseURL });
  const credentials = await createStudent(student);
  expect((await mutate(student, "/auth/login", credentials)).ok()).toBeTruthy();
  const otherCredentials = await createStudent(otherStudent);
  expect((await mutate(otherStudent, "/auth/login", otherCredentials)).ok()).toBeTruthy();
  expect(
    (
      await mutate(context, "/auth/login", {
        username: "vendor",
        password: "demo12345",
      })
    ).ok(),
  ).toBeTruthy();
  const stalls = await (
    await context.request.get(`${baseURL}/api/v1/merchant/stalls`)
  ).json();
  const stall = stalls.find((value: any) => value.transaction_enabled);
  const profilePath = `/merchant/stalls/${stall.id}/profile`;
  const ids: string[] = [];
  let product: any;
  const write = async (path: string, body: any, method = "POST") => {
    const response = await mutate(context, path, body, method);
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  };
  const productSnapshot = async () => {
    const current = await (
      await context.request.get(`${baseURL}/api/v1/merchant/stalls`)
    ).json();
    return current
      .find((value: any) => value.id === stall.id)
      .products.find((value: any) => value.id === product.id);
  };
  const orderSnapshot = async (id: string) =>
    (await student.request.get(`${baseURL}/api/v1/orders/${id}`)).json();
  const create = async (key: string) => {
    const response = await mutate(student, "/orders", {
      stall_id: stall.id,
      idempotency_key: key,
      items: [
        { product_id: product.id, quantity: 1, expected_price_cents: 1200 },
      ],
    });
    expect(response.status(), await response.text()).toBe(201);
    const order = await response.json();
    ids.push(order.id);
    return order;
  };
  try {
    await write(
      profilePath,
      { prep_capacity: 1, accepting_orders: true },
      "PATCH",
    );
    await write(`/merchant/stalls/${stall.id}/status`, {
      status: "open",
      confirm_location: true,
      stop_orders_at: null,
      closes_at: new Date(Date.now() + 3600000).toISOString(),
    });
    product = await write(`/merchant/stalls/${stall.id}/products`, {
      name: `出餐联调-${Date.now()}`,
      price_cents: 1200,
      stock: 3,
      image: "/images/food-cold-noodles.jpg",
    });
    const first = await create(crypto.randomUUID());
    // A distinct student reaches the capacity gate; same-stall duplicates are
    // now rejected by the account reservation budget before capacity is checked.
    const full = await mutate(otherStudent, "/orders", {
      stall_id: stall.id,
      idempotency_key: crypto.randomUUID(),
      items: [
        { product_id: product.id, quantity: 1, expected_price_cents: 1200 },
      ],
    });
    expect(full.status()).toBe(409);
    expect((await full.json()).code).toBe("prep_capacity_reached");
    await write(
      `/merchant/products/${product.id}`,
      { sale_paused: true },
      "PATCH",
    );
    expect(
      (
        await mutate(student, `/orders/${first.id}/cancel`, {
          reason: "隔离测试库存归还",
        })
      ).ok(),
    ).toBeTruthy();
    expect(await productSnapshot()).toMatchObject({
      stock: 3,
      sale_paused: true,
    });
    await write(`/merchant/stalls/${stall.id}/restock`, {
      idempotency_key: crypto.randomUUID(),
      items: [{ product_id: product.id, quantity: 2 }],
    });
    expect(await productSnapshot()).toMatchObject({
      stock: 5,
      sale_paused: true,
    });
    const correctionPath = `/merchant/products/${product.id}/stock-correction`;
    const stale = await mutate(context, correctionPath, {
      stock: 4,
      expected_stock_version: product.stock_version,
      idempotency_key: crypto.randomUUID(),
      reason: "隔离盘点",
    });
    expect(stale.status()).toBe(409);
    expect((await stale.json()).code).toBe("stock_version_conflict");
    const current = await productSnapshot();
    const correction = {
      stock: 4,
      expected_stock_version: current.stock_version,
      idempotency_key: crypto.randomUUID(),
      reason: "隔离盘点",
    };
    expect((await write(correctionPath, correction)).replayed).toBe(false);
    await write(
      `/merchant/products/${product.id}`,
      { sale_paused: false },
      "PATCH",
    );
    const order = await create(crypto.randomUUID());
    const replay = await write(correctionPath, correction);
    expect(replay).toMatchObject({ replayed: true, product: { stock: 3 } });
    await write(`/merchant/orders/${order.id}/action`, {
      action: "accept",
      prep_minutes: 8,
      idempotency_key: crypto.randomUUID(),
    });
    await write(`/merchant/orders/${order.id}/action`, { action: "ready" });
    const ready = await orderSnapshot(order.id);
    expect(ready.payment_status).toBe("unpaid");
    expect(ready.pickup_code).toMatch(/^\d{8}$/);
    await page.goto("/merchant/orders");
    await page.getByLabel("选择管理的摊位").selectOption(String(stall.id));
    await page
      .getByRole("button", { name: "取餐码查单", exact: true })
      .click();
    await page
      .getByLabel("8 位取餐码", { exact: true })
      .fill(ready.pickup_code);
    await page
      .getByRole("button", { name: "查找待取餐订单", exact: true })
      .click();
    const details = page.getByRole("dialog", { name: "订单详情", exact: true });
    await expect(details).toContainText(product.name);
    expect(await orderSnapshot(order.id)).toMatchObject({
      status: "ready",
      payment_status: "unpaid",
    });
    await expect(
      details.getByRole("button", { name: "核销并完成" }),
    ).toHaveCount(0);
    await details.getByRole("button", { name: "收款 ¥12", exact: true }).click();
    await page
      .getByRole("dialog", { name: "确认这笔线下收款" })
      .getByRole("button", { name: "确认收款", exact: true })
      .click();
    await expect(
      details.getByRole("button", { name: "核销并完成" }),
    ).toBeVisible();
    expect(await orderSnapshot(order.id)).toMatchObject({
      status: "ready",
      payment_status: "paid",
    });
    await details.getByRole("button", { name: "核销并完成" }).click();
    await expect
      .poll(async () => (await orderSnapshot(order.id)).status)
      .toBe("completed");
    const duplicate = await mutate(
      context,
      `/merchant/orders/${order.id}/action`,
      { action: "complete", pickup_code: ready.pickup_code },
    );
    expect(duplicate.status()).toBe(409);
    expect((await duplicate.json()).code).toBe("invalid_transition");
    expect((await productSnapshot()).stock).toBe(3);
  } finally {
    for (const id of ids) {
      const order = await orderSnapshot(id);
      if (order.status === "pending")
        await mutate(student, `/orders/${id}/cancel`, {
          reason: "隔离测试结束",
        });
    }
    if (product)
      await write(
        `/merchant/products/${product.id}`,
        { is_active: false },
        "PATCH",
      );
    await write(
      profilePath,
      {
        prep_capacity: stall.prep_capacity ?? null,
        accepting_orders: stall.accepting_orders,
      },
      "PATCH",
    );
    await student.close();
    await otherStudent.close();
  }
});
