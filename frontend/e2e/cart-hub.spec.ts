import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import {
  assertNoHorizontalOverflow,
  baseURL,
  createStudent,
  correctStock,
  login,
  mutate,
} from "./helpers";

let merchant: BrowserContext;
const fixtures: { stall: any; product: any }[] = [];

async function refresh(page: Page) {
  await page.getByRole("button", { name: "刷新餐袋", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "刷新餐袋", exact: true }),
  ).toBeEnabled();
}

async function fillBags(page: Page, choices = fixtures) {
  await page.goto("/search?type=dishes");
  for (const { stall, product } of choices) {
    await page.goto(`/stalls/${stall.id}/products/${product.id}`);
    await page.getByRole("button", { name: /加入餐袋/ }).click();
  }
  await page.goto("/cart");
  await expect(
    page.getByRole("heading", { name: "我的餐袋。", exact: true }),
  ).toBeVisible();
  for (const { stall } of choices) {
    await expect(
      page.getByRole("link", {
        name: `去结算${stall.name}的餐点`,
        exact: true,
      }),
    ).toBeVisible();
  }
}

test.beforeAll(async ({ browser }) => {
  merchant = await browser.newContext({ baseURL });
  const config = await (
    await merchant.request.get(`${baseURL}/api/v1/config`)
  ).json();
  expect(config.demo_mode, "Cart fixtures require a demo server").toBe(true);
  const authenticated = await mutate(merchant, "/auth/login", {
    username: "vendor",
    password: "demo12345",
  });
  expect(authenticated.ok(), await authenticated.text()).toBeTruthy();
  const stalls = await (
    await merchant.request.get(`${baseURL}/api/v1/merchant/stalls`)
  ).json();
  const eligible = stalls
    .filter((stall: any) => stall.transaction_enabled)
    .slice(0, 2);
  expect(eligible.length).toBe(2);
  for (const [index, stall] of eligible.entries()) {
    const opened = await mutate(
      merchant,
      `/merchant/stalls/${stall.id}/status`,
      {
        status: "open",
        confirm_location: true,
        closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
      },
    );
    expect(opened.ok(), await opened.text()).toBeTruthy();
    const result = await mutate(
      merchant,
      `/merchant/stalls/${stall.id}/products`,
      {
        name: `餐袋验证${index + 1}-${Date.now()}`,
        description: "仅用于餐袋自动化验证的餐点，测试结束后下架。",
        category: "测试小食",
        price_cents: 850 + index * 100,
        image: index
          ? "/images/food-skewers.jpg"
          : "/images/food-cold-noodles.jpg",
        stock: 5,
        is_active: true,
      },
    );
    expect(result.status(), await result.text()).toBe(201);
    fixtures.push({ stall, product: await result.json() });
  }
});

test.afterAll(async () => {
  for (const { product } of fixtures) {
    const result = await mutate(
      merchant,
      `/merchant/products/${product.id}`,
      { is_active: false },
      "PATCH",
    );
    expect(result.ok(), await result.text()).toBeTruthy();
  }
  await merchant?.close();
});

test("multiple stall bags survive reload and one real checkout leaves the other bag intact", async ({
  page,
  context,
}, testInfo) => {
  const student = await createStudent(context);
  await fillBags(page);
  await expect(page.locator(".cart-stall")).toHaveCount(2);
  for (const { stall } of fixtures) {
    await expect(
      page.getByRole("link", {
        name: `去结算${stall.name}的餐点`,
        exact: true,
      }),
    ).toBeVisible();
  }
  await page.reload();
  await expect(page.locator(".cart-stall")).toHaveCount(2);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`cart-${width}.png`),
      fullPage: true,
    });
  }
  const first = fixtures[0]!;
  const second = fixtures[1]!;
  await page
    .getByRole("link", {
      name: `查看${first.product.name}图片与详情`,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/stalls/${first.stall.id}/products/${first.product.id}$`),
  );
  await page.goto("/cart");
  await page
    .getByRole("link", { name: `去结算${first.stall.name}的餐点`, exact: true })
    .click();
  await expect(page).toHaveURL(/\/login\?returnTo=/);
  await login(page, student.username, student.password);
  await expect(page).toHaveURL(new RegExp(`/checkout/${first.stall.id}$`));
  await expect(page.locator(".checkout-product")).toHaveCount(1);
  await expect(page.locator(".checkout-product")).toContainText(
    first.product.name,
  );
  const createdPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      /\/api\/v1\/orders$/.test(response.url()),
  );
  await page.getByRole("button", { name: "提交自取订单", exact: true }).click();
  const result = await createdPromise;
  expect(result.status(), await result.text()).toBe(201);
  const order = await result.json();
  try {
    await expect(page).toHaveURL(new RegExp(`/orders/${order.id}$`));
    expect(order.stall_id).toBe(first.stall.id);
    expect(order.items).toHaveLength(1);
    expect(order.items[0].product_id).toBe(first.product.id);
    await page.goto("/cart");
    await expect(page.locator(".cart-stall")).toHaveCount(1);
    await expect(page.locator(".cart-stall")).toContainText(
      second.product.name,
    );
    await page
      .getByRole("link", {
        name: `去结算${second.stall.name}的餐点`,
        exact: true,
      })
      .click();
    await expect(page).toHaveURL(new RegExp(`/checkout/${second.stall.id}$`));
    await expect(page.locator(".checkout-product")).toContainText(
      second.product.name,
    );
  } finally {
    const cancelled = await mutate(context, `/orders/${order.id}/cancel`, {
      reason: "餐袋测试结束，取消待接单订单",
    });
    expect(cancelled.ok(), await cancelled.text()).toBeTruthy();
  }
});

test("quantity controls retain selected items and removal and clearing can be undone", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fillBags(page);
  const first = fixtures[0]!;
  const row = page.locator(`.cart-item[data-product-id="${first.product.id}"]`);
  await row
    .getByRole("button", { name: `增加${first.product.name}`, exact: true })
    .click();
  await expect(row.locator("output")).toHaveText("2");
  await page.reload();
  await expect(row.locator("output")).toHaveText("2");
  await row
    .getByRole("button", { name: `减少${first.product.name}`, exact: true })
    .click();
  await expect(row.locator("output")).toHaveText("1");
  await row
    .getByRole("button", { name: `移除${first.product.name}`, exact: true })
    .click();
  await expect(row).toHaveCount(0);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(row.locator("output")).toHaveText("1");
  await page
    .getByRole("button", { name: `清空${first.stall.name}的餐袋`, exact: true })
    .click();
  await expect(page.locator(".cart-stall")).toHaveCount(1);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".cart-stall")).toHaveCount(2);
  for (const { stall } of fixtures)
    await page
      .getByRole("button", { name: `清空${stall.name}的餐袋`, exact: true })
      .click();
  await expect(
    page.getByRole("heading", { name: "还没决定吃什么？" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".cart-stall")).toHaveCount(1);
});

test("changed price requires acceptance and actual sold out or inactive dishes block checkout", async ({
  page,
}) => {
  const first = fixtures[0]!;
  await fillBags(page, [first]);
  const group = page.locator(`.cart-stall[data-stall-id="${first.stall.id}"]`);
  try {
    const changed = await mutate(
      merchant,
      `/merchant/products/${first.product.id}`,
      { price_cents: 950 },
      "PATCH",
    );
    expect(changed.ok(), await changed.text()).toBeTruthy();
    await refresh(page);
    await expect(group.locator(".cart-price-change")).toContainText(
      "¥8.5 → ¥9.5",
    );
    await expect(
      group.getByRole("button", { name: "请先确认价格", exact: true }),
    ).toBeDisabled();
    await expect(group.locator(".cart-food-price strong")).toHaveText("¥8.5");
    await page.reload();
    await expect(group.locator(".cart-food-price strong")).toHaveText("¥8.5");
    await group
      .getByRole("button", {
        name: `确认${first.product.name}新价格`,
        exact: true,
      })
      .click();
    await expect(group.locator(".cart-food-price strong")).toHaveText("¥9.5");
    await expect(
      group.getByRole("link", {
        name: `去结算${first.stall.name}的餐点`,
        exact: true,
      }),
    ).toBeVisible();
    await group
      .getByRole("button", { name: `增加${first.product.name}`, exact: true })
      .click();
    const limited = await correctStock(merchant, first.product.id, 1);
    expect(limited.ok(), await limited.text()).toBeTruthy();
    await refresh(page);
    await expect(
      group.getByText("仅剩 1 份，请调整数量", { exact: true }),
    ).toBeVisible();
    await group
      .getByRole("button", { name: `减少${first.product.name}`, exact: true })
      .click();
    await expect(
      group.getByRole("link", {
        name: `去结算${first.stall.name}的餐点`,
        exact: true,
      }),
    ).toBeVisible();
    const sold = await correctStock(merchant, first.product.id, 0);
    expect(sold.ok(), await sold.text()).toBeTruthy();
    await refresh(page);
    await expect(
      group.getByText("线上份数已售罄", { exact: true }),
    ).toBeVisible();
    await expect(
      group.getByRole("button", { name: "暂不可结算", exact: true }),
    ).toBeDisabled();
    const inactive = await mutate(
      merchant,
      `/merchant/products/${first.product.id}`,
      { is_active: false },
      "PATCH",
    );
    expect(inactive.ok(), await inactive.text()).toBeTruthy();
    await refresh(page);
    await expect(
      group.getByText("已下架或不再供应", { exact: true }),
    ).toBeVisible();
  } finally {
    const restored = await mutate(
      merchant,
      `/merchant/products/${first.product.id}`,
      { price_cents: first.product.price_cents, is_active: true },
      "PATCH",
    );
    expect(restored.ok(), await restored.text()).toBeTruthy();
    const stockRestored = await correctStock(merchant, first.product.id, 5);
    expect(stockRestored.ok(), await stockRestored.text()).toBeTruthy();
  }
});

test("a real paused stall and connection failure preserve drafts while disabling checkout", async ({
  page,
}) => {
  const first = fixtures[0]!;
  await fillBags(page, [first]);
  const group = page.locator(`.cart-stall[data-stall-id="${first.stall.id}"]`);
  try {
    const paused = await mutate(
      merchant,
      `/merchant/stalls/${first.stall.id}/status`,
      { status: "paused" },
    );
    expect(paused.ok(), await paused.text()).toBeTruthy();
    await refresh(page);
    await expect(group.locator(".cart-stall-alert")).toContainText(
      "商家暂时休息",
    );
    await expect(
      group.getByRole("button", { name: "暂不可结算", exact: true }),
    ).toBeDisabled();
  } finally {
    const opened = await mutate(
      merchant,
      `/merchant/stalls/${first.stall.id}/status`,
      { status: "open", confirm_location: true },
    );
    expect(opened.ok(), await opened.text()).toBeTruthy();
  }
  await page.route(`**/api/v1/stalls/${first.stall.id}`, (route) =>
    route.abort("connectionfailed"),
  );
  await refresh(page);
  await expect(group.locator(".cart-stall-alert")).toContainText(
    "暂时连接不上",
  );
  await expect(
    group.getByRole("button", { name: "暂不可结算", exact: true }),
  ).toBeDisabled();
  await expect(group.locator("output")).toHaveText("1");
  await page.unroute(`**/api/v1/stalls/${first.stall.id}`);
  await refresh(page);
  await expect(
    group.getByRole("link", {
      name: `去结算${first.stall.name}的餐点`,
      exact: true,
    }),
  ).toBeVisible();
});

test("a removed stall keeps its saved dish visible with a usable removal and undo path", async ({
  page,
}) => {
  await page.goto("/cart");
  await page.evaluate((product) => {
    localStorage.setItem(
      "yanhuo-cart-v1",
      JSON.stringify({ "2147483647": [{ product, quantity: 1 }] }),
    );
  }, fixtures[0]!.product);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "摊位已不可用", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".cart-stall-alert")).toContainText(
    "这个摊位已不可用",
  );
  await expect(
    page.getByRole("button", { name: "暂不可结算", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "清空这个摊位的餐袋", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "还没决定吃什么？" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "摊位已不可用", exact: true }),
  ).toBeVisible();
});

test("a stalled real response times out without losing the bag, and cannot unlock a newer retry", async ({
  page,
}) => {
  const first = fixtures[0]!;
  await fillBags(page, [first]);
  const before = await page.evaluate(() =>
    localStorage.getItem("yanhuo-cart-v1"),
  );
  const group = page.locator(`.cart-stall[data-stall-id="${first.stall.id}"]`);
  const pattern = `**/api/v1/stalls/${first.stall.id}`;
  function latch() {
    let resolve!: () => void;
    const promise = new Promise<void>((done) => {
      resolve = done;
    });
    return { promise, resolve };
  }
  const requests = [
    { captured: latch(), release: latch(), finished: latch() },
    { captured: latch(), release: latch(), finished: latch() },
  ];
  let requestIndex = 0;
  await page.route(pattern, async (route) => {
    const held = requests[requestIndex++];
    if (!held) {
      await route.continue();
      return;
    }
    try {
      // Read the live backend response, then delay delivery to model a hanging connection.
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBeTruthy();
      held.captured.resolve();
      await held.release.promise;
      await route.fulfill({ response }).catch(() => {});
    } finally {
      held.finished.resolve();
    }
  });
  try {
    await page.getByRole("button", { name: "刷新餐袋", exact: true }).click();
    await requests[0]!.captured.promise;
    await expect(group.locator(".cart-stall-alert")).toContainText(
      "请求超时，餐袋已保留",
      { timeout: 15_000 },
    );
    await expect(
      page.getByRole("button", { name: "刷新餐袋", exact: true }),
    ).toBeEnabled();
    await expect(
      group.getByRole("button", { name: "暂不可结算", exact: true }),
    ).toBeDisabled();
    expect(
      await page.evaluate(() => localStorage.getItem("yanhuo-cart-v1")),
    ).toBe(before);

    await page.getByRole("button", { name: "刷新餐袋", exact: true }).click();
    await requests[1]!.captured.promise;
    requests[0]!.release.resolve();
    await requests[0]!.finished.promise;
    // Finishing the cancelled request cannot finish or overwrite the current one.
    await expect(
      page.getByRole("button", { name: "正在核对", exact: true }),
    ).toBeDisabled();
    await expect(group.locator(".cart-stall-alert")).toContainText(
      "正在核对营业状态与库存",
    );
    requests[1]!.release.resolve();
    await requests[1]!.finished.promise;
    await expect(
      page.getByRole("button", { name: "刷新餐袋", exact: true }),
    ).toBeEnabled();
    await expect(
      group.getByRole("link", {
        name: `去结算${first.stall.name}的餐点`,
        exact: true,
      }),
    ).toBeVisible();
    expect(
      await page.evaluate(() => localStorage.getItem("yanhuo-cart-v1")),
    ).toBe(before);
  } finally {
    for (const request of requests) request.release.resolve();
    await page.unroute(pattern);
  }
});

test("leaving the cart aborts its pending request and returning starts a fresh check", async ({
  page,
}) => {
  const first = fixtures[0]!;
  await fillBags(page, [first]);
  const pattern = `**/api/v1/stalls/${first.stall.id}`;
  let release!: () => void;
  let captured!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const gotResponse = new Promise<void>((resolve) => {
    captured = resolve;
  });
  await page.route(pattern, async (route) => {
    const response = await route.fetch();
    expect(response.ok(), await response.text()).toBeTruthy();
    captured();
    await held;
    await route.fulfill({ response }).catch(() => {});
  });
  try {
    await page.getByRole("button", { name: "刷新餐袋", exact: true }).click();
    await gotResponse;
    const cancelled = page.waitForEvent("requestfailed", {
      predicate: (request) =>
        request.url().endsWith(`/api/v1/stalls/${first.stall.id}`),
    });
    await page
      .getByRole("link", { name: "继续发现好味道", exact: true })
      .click();
    await expect(page).toHaveURL(/\/search\?type=dishes$/);
    expect((await cancelled).failure()?.errorText).toContain("ERR_ABORTED");
  } finally {
    release();
    await page.unroute(pattern);
  }
  await page.goto("/cart");
  await expect(
    page.getByRole("link", {
      name: `去结算${first.stall.name}的餐点`,
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.locator(`.cart-item[data-product-id="${first.product.id}"] output`),
  ).toHaveText("1");
});
