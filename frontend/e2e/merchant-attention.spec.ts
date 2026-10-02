import { expect, test, type BrowserContext } from "@playwright/test";
import {
  assertNoHorizontalOverflow,
  baseURL,
  createStudent,
  login,
  mutate,
} from "./helpers";

async function readOrders(context: BrowserContext, stallId: number) {
  const response = await context.request.get(
    `${baseURL}/api/v1/merchant/orders?stall=${stallId}`,
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test("merchant sees new orders across pages once, handles cancellation requests and recovers after offline", async ({
  page,
  context,
  browser,
}, testInfo) => {
  test.setTimeout(120_000);
  const config = await (
    await context.request.get(`${baseURL}/api/v1/config`)
  ).json();
  expect(
    config.demo_mode,
    "Order alert tests only mutate explicitly labelled demo data",
  ).toBe(true);
  await page.goto("/login?returnTo=/merchant/products");
  await login(page, "vendor", "demo12345");
  await expect(page.locator(".m-stall-bar")).toBeVisible();
  const stalls = await (
    await context.request.get(`${baseURL}/api/v1/merchant/stalls`)
  ).json();
  const original = stalls.find(
    (stall: any) =>
      stall.transaction_enabled &&
      stall.products.some(
        (product: any) => product.is_active !== false && product.stock >= 2,
      ),
  );
  expect(
    original,
    "The vendor needs an eligible demo stall with two portions",
  ).toBeTruthy();
  const product = original.products.find(
    (item: any) => item.is_active !== false && item.stock >= 2,
  );
  const customer = await browser.newContext({ baseURL });
  const ownOrders: string[] = [];
  const alert = page.getByRole("region", { name: "接单提醒", exact: true });
  const notice = page.getByTestId("merchant-new-order-notice");
  async function refresh() {
    await expect(
      page.getByRole("button", { name: "刷新工作台", exact: true }),
    ).toBeEnabled();
    const loaded = page.waitForResponse(
      (response) =>
        response.url().includes(`/merchant/orders?stall=${original.id}`) &&
        response.request().method() === "GET",
    );
    await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
    expect((await loaded).ok()).toBeTruthy();
    await expect(
      page.getByRole("button", { name: "刷新工作台", exact: true }),
    ).toBeEnabled();
  }
  async function createOrder(suffix: string) {
    const response = await mutate(customer, "/orders", {
      stall_id: original.id,
      idempotency_key: `attention-${Date.now()}-${suffix}`,
      note: "接单提醒自动化测试订单，稍后取消",
      items: [
        {
          product_id: product.id,
          quantity: 1,
          expected_price_cents: product.price_cents,
        },
      ],
    });
    expect(response.status(), await response.text()).toBe(201);
    const order = await response.json();
    ownOrders.push(order.id);
    return order;
  }
  try {
    const student = await createStudent(customer);
    const signedIn = await mutate(customer, "/auth/login", student);
    expect(signedIn.ok(), await signedIn.text()).toBeTruthy();
    const opened = await mutate(
      context,
      `/merchant/stalls/${original.id}/status`,
      {
        status: "open",
        confirm_location: true,
        closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
      },
    );
    expect(opened.ok(), await opened.text()).toBeTruthy();
    await page.getByLabel("选择管理的摊位").selectOption(String(original.id));
    const first = await createOrder("initial");
    // Existing orders get a persistent action banner, but a fresh page must not announce them as new.
    await page.reload();
    await expect(alert).toContainText("等你接单");
    await expect(notice).toHaveCount(0);
    await expect(
      alert.getByRole("button", { name: "开启声音提醒", exact: true }),
    ).toHaveAttribute("aria-pressed", "false");
    await alert
      .getByRole("button", { name: "开启声音提醒", exact: true })
      .click();
    await expect(
      alert.getByRole("button", { name: "关闭声音提醒", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await alert
      .getByRole("button", { name: "关闭声音提醒", exact: true })
      .click();
    await expect(
      alert.getByRole("button", { name: "开启声音提醒", exact: true }),
    ).toHaveAttribute("aria-pressed", "false");
    const initialOrders = await readOrders(context, original.id);
    const initialPending = initialOrders.filter(
      (order: any) => order.status === "pending",
    );
    await expect(alert).toHaveAttribute(
      "data-pending-count",
      String(initialPending.length),
    );
    await expect(alert.locator(".m-attention-countdown")).toHaveText(
      /最早一单剩余 \d+:\d{2} 接单/,
    );
    const nearest = Math.min(
      ...initialPending.map((order: any) => Date.parse(order.expires_at)),
    );
    const countdown = await alert.locator(".m-attention-countdown").innerText();
    const parsed = countdown.match(/(\d+):(\d{2})/)!;
    expect(
      Math.abs(
        Number(parsed[1]) * 60 +
          Number(parsed[2]) -
          Math.ceil((nearest - Date.now()) / 1000),
      ),
    ).toBeLessThanOrEqual(3);

    const second = await createOrder("arrival");
    await refresh();
    await expect(notice).toContainText("刚收到 1 笔新订单");
    await notice
      .getByRole("button", { name: "知道了，收起新订单提示" })
      .click();
    await expect(notice).toHaveCount(0);
    await refresh();
    await expect(notice).toHaveCount(0);
    await page
      .locator(".m-sidebar")
      .getByRole("link", { name: "经营数据", exact: true })
      .click();
    await expect(page).toHaveURL(/\/merchant\/analytics$/);
    await expect(alert).toContainText("等你接单");
    await expect(notice).toHaveCount(0);
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: width < 768 ? 844 : 1000 });
      await assertNoHorizontalOverflow(page);
      await expect(
        alert.getByRole("link", { name: "去接单", exact: true }),
      ).toBeVisible();
      expect(
        (await alert
          .getByRole("link", { name: "去接单", exact: true })
          .boundingBox())!.height,
      ).toBeGreaterThanOrEqual(44);
      if (width === 390 || width === 1440)
        await page.screenshot({
          path: testInfo.outputPath(`merchant-attention-${width}.png`),
        });
    }

    // Network loss must not present last-known counts as a successfully refreshed workbench.
    await context.setOffline(true);
    await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
    await expect(alert).toContainText("订单同步中断，当前显示上次同步结果");
    await expect(
      alert.getByRole("button", { name: "重新同步", exact: true }),
    ).toBeVisible();
    await context.setOffline(false);
    await alert.getByRole("button", { name: "重新同步", exact: true }).click();
    await expect(alert).not.toContainText("订单同步中断");
    await expect(notice).toHaveCount(0);

    await alert.getByRole("link", { name: "去接单", exact: true }).click();
    await expect(page).toHaveURL(/\/merchant\/orders\?filter=pending$/);
    const firstCard = page
      .locator("article.merchant-order")
      .filter({ hasText: first.number });
    await expect(firstCard).toBeVisible();
    await firstCard
      .getByRole("button", { name: "确认接单", exact: true })
      .click();
    await expect(firstCard).toHaveCount(0);
    const cancellation = await mutate(customer, `/orders/${first.id}/cancel`, {
      reason: "自动化测试取消申请",
    });
    expect(cancellation.ok(), await cancellation.text()).toBeTruthy();
    const secondCancelled = await mutate(
      customer,
      `/orders/${second.id}/cancel`,
      { reason: "自动化测试清理待接单" },
    );
    expect(secondCancelled.ok(), await secondCancelled.text()).toBeTruthy();
    await refresh();
    await expect(
      alert.getByRole("link", { name: /处理取消申请/ }),
    ).toBeVisible();
    await alert.getByRole("link", { name: /处理取消申请/ }).click();
    await expect(page).toHaveURL(/filter=cancellation/);
    const requested = page
      .locator("article.merchant-order")
      .filter({ hasText: first.number });
    await expect(requested).toContainText("顾客申请取消");
    await requested
      .getByRole("button", { name: "同意取消", exact: true })
      .click();
    const confirmation = page.getByRole("dialog", {
      name: "同意顾客取消订单",
      exact: true,
    });
    await confirmation
      .getByRole("button", { name: "确认取消订单", exact: true })
      .click();
    await expect(requested).toHaveCount(0);
    const finalOrders = await readOrders(context, original.id);
    await expect(alert).toHaveAttribute(
      "data-pending-count",
      String(
        finalOrders.filter((order: any) => order.status === "pending").length,
      ),
    );
    await expect(alert).toHaveAttribute(
      "data-cancellation-count",
      String(
        finalOrders.filter(
          (order: any) =>
            order.cancel_requested &&
            ["preparing", "ready"].includes(order.status),
        ).length,
      ),
    );
    for (const id of ownOrders)
      expect(finalOrders.find((order: any) => order.id === id).status).toBe(
        "cancelled",
      );
    await expect(notice).toHaveCount(0);
  } finally {
    await context.setOffline(false);
    for (const id of ownOrders) {
      const response = await customer.request.get(
        `${baseURL}/api/v1/orders/${id}`,
      );
      if (!response.ok()) continue;
      const order = await response.json();
      if (!["pending", "preparing", "ready"].includes(order.status)) continue;
      const cancelled = await mutate(customer, `/orders/${id}/cancel`, {
        reason: "清理接单提醒测试订单",
      });
      expect(cancelled.ok(), await cancelled.text()).toBeTruthy();
      if (order.status !== "pending") {
        const approved = await mutate(
          context,
          `/merchant/orders/${id}/action`,
          { action: "approve_cancel", reason: "清理接单提醒测试订单" },
        );
        expect(approved.ok(), await approved.text()).toBeTruthy();
      }
    }
    const expiredClosing =
      original.closes_at &&
      new Date(original.closes_at).getTime() <= Date.now();
    const sessionStatus =
      original.session_status ||
      (original.status === "stale" ? "paused" : original.status);
    const restored = await mutate(
      context,
      `/merchant/stalls/${original.id}/status`,
      {
        status:
          original.status === "closed" ||
          (sessionStatus === "open" && expiredClosing)
            ? "closed"
            : sessionStatus,
        confirm_location: false,
        closes_at: original.closes_at,
      },
    );
    expect(restored.ok(), await restored.text()).toBeTruthy();
    await customer.close();
  }
});

test("stale refresh responses cannot clear or create errors for a newly selected stall", async ({
  page,
  context,
}) => {
  const config = await (
    await context.request.get(`${baseURL}/api/v1/config`)
  ).json();
  expect(
    config.demo_mode,
    "This test uses the explicitly labelled demo merchant account",
  ).toBe(true);
  await page.goto("/login?returnTo=/merchant/products");
  await login(page, "vendor", "demo12345");
  await expect(page.locator(".m-stall-bar")).toBeVisible();
  const response = await context.request.get(
    `${baseURL}/api/v1/merchant/stalls`,
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const stalls = await response.json();
  expect(
    stalls.length,
    "Two genuine merchant stalls are needed to exercise switching",
  ).toBeGreaterThanOrEqual(2);
  const [first, second] = stalls;
  const selector = page.getByLabel("选择管理的摊位");
  const refreshButton = page.getByRole("button", {
    name: "刷新工作台",
    exact: true,
  });
  const alert = page.getByRole("region", { name: "接单提醒", exact: true });
  const ordersPattern = "**/api/v1/merchant/orders?stall=*";
  const stallsPattern = "**/api/v1/merchant/stalls";
  let releaseOldOrders = () => {};
  let releaseOldCatalogue = () => {};
  try {
    await selector.selectOption(String(first.id));
    await expect(refreshButton).toBeEnabled();
    await expect(alert).toContainText("每 10 秒同步");
    let capturedFirst = false;
    let holdFirst = true;
    let failSecond = true;
    const oldOrdersGate = new Promise<void>((resolve) => {
      releaseOldOrders = resolve;
    });
    await page.route(ordersPattern, async (route) => {
      const stallId = new URL(route.request().url()).searchParams.get("stall");
      if (stallId === String(first.id) && holdFirst) {
        holdFirst = false;
        // Read the real server response, then delay only its delivery to the UI.
        const realResponse = await route.fetch();
        expect(realResponse.ok(), await realResponse.text()).toBeTruthy();
        capturedFirst = true;
        await oldOrdersGate;
        await route.fulfill({ response: realResponse });
      } else if (stallId === String(second.id) && failSecond) {
        await route.abort("failed");
      } else await route.continue();
    });
    await refreshButton.click();
    await expect.poll(() => capturedFirst).toBe(true);
    await selector.selectOption(String(second.id));
    await expect(alert).toContainText("订单同步中断，当前显示上次同步结果");
    await expect(
      alert.getByRole("button", { name: "重新同步", exact: true }),
    ).toBeVisible();
    releaseOldOrders();
    await expect(refreshButton).toBeEnabled();
    // The old successful A request is discarded, including its outer refresh's error reset.
    await expect(selector).toHaveValue(String(second.id));
    await expect(alert).toContainText("订单同步中断，当前显示上次同步结果");
    await expect(
      alert.getByRole("button", { name: "重新同步", exact: true }),
    ).toBeVisible();
    failSecond = false;
    await alert.getByRole("button", { name: "重新同步", exact: true }).click();
    await expect(refreshButton).toBeEnabled();
    await expect(alert).not.toContainText("订单同步中断");
    await page.unroute(ordersPattern);

    // Also exercise the failure path before loadOrders: an old A catalogue
    // request fails after switching to B, whose real orders loaded successfully.
    const firstLoaded = page.waitForResponse((reply) => {
      const url = new URL(reply.url());
      return (
        url.pathname.endsWith("/merchant/orders") &&
        url.searchParams.get("stall") === String(first.id)
      );
    });
    await selector.selectOption(String(first.id));
    expect((await firstLoaded).ok()).toBeTruthy();
    await expect(alert).toContainText("每 10 秒同步");
    let capturedCatalogue = false;
    let holdCatalogue = true;
    const oldCatalogueGate = new Promise<void>((resolve) => {
      releaseOldCatalogue = resolve;
    });
    await page.route(stallsPattern, async (route) => {
      if (!holdCatalogue) return route.continue();
      holdCatalogue = false;
      const actual = await route.fetch();
      expect(actual.ok(), await actual.text()).toBeTruthy();
      capturedCatalogue = true;
      await oldCatalogueGate;
      await route.abort("failed");
    });
    await refreshButton.click();
    await expect.poll(() => capturedCatalogue).toBe(true);
    const secondLoaded = page.waitForResponse((reply) => {
      const url = new URL(reply.url());
      return (
        url.pathname.endsWith("/merchant/orders") &&
        url.searchParams.get("stall") === String(second.id)
      );
    });
    await selector.selectOption(String(second.id));
    expect((await secondLoaded).ok()).toBeTruthy();
    await expect(alert).toContainText("每 10 秒同步");
    releaseOldCatalogue();
    await expect(refreshButton).toBeEnabled();
    await expect(selector).toHaveValue(String(second.id));
    await expect(alert).not.toContainText("订单同步中断");
    await expect(page.locator(".m-content > .m-alert")).toHaveCount(0);
  } finally {
    releaseOldOrders();
    releaseOldCatalogue();
    await page.unroute(ordersPattern);
    await page.unroute(stallsPattern);
  }
});
