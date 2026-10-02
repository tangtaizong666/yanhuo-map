import { expect, test } from "@playwright/test";
import {
  assertNoHorizontalOverflow,
  baseURL,
  createStudent,
  mutate,
} from "./helpers";

test("student order reminder follows real merchant progress, recovers offline and clears on account change", async ({
  page,
  context,
  browser,
}, testInfo) => {
  const customer = await createStudent(context);
  const other = await createStudent(context);
  expect((await mutate(context, "/auth/login", customer)).ok()).toBeTruthy();
  const merchant = await browser.newContext({ baseURL });
  await merchant.request.get(`${baseURL}/api/v1/config`);
  expect(
    (
      await mutate(merchant, "/auth/login", {
        username: "vendor",
        password: "demo12345",
      })
    ).ok(),
  ).toBeTruthy();
  const stalls = await (
    await merchant.request.get(`${baseURL}/api/v1/merchant/stalls`)
  ).json();
  const stall = stalls.find(
    (row: any) =>
      row.transaction_enabled &&
      row.products.some(
        (item: any) => item.is_active !== false && item.stock > 0,
      ),
  );
  expect(stall).toBeTruthy();
  const product = stall.products.find(
    (item: any) => item.is_active !== false && item.stock > 0,
  );
  let orderId = "";
  const banner = page.getByRole("complementary", { name: "进行中订单提醒" });
  async function sync() {
    const response = page.waitForResponse((result) =>
      result.url().endsWith("/orders/active-summary"),
    );
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    expect((await response).ok()).toBeTruthy();
  }
  try {
    expect(
      (
        await mutate(merchant, `/merchant/stalls/${stall.id}/status`, {
          status: "open",
          confirm_location: true,
          closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
        })
      ).ok(),
    ).toBeTruthy();
    const created = await mutate(context, "/orders", {
      stall_id: stall.id,
      idempotency_key: `student-reminder-${Date.now()}`,
      note: "进行中订单提醒测试，完成后取消",
      contact_phone: "",
      items: [
        {
          product_id: product.id,
          quantity: 1,
          expected_price_cents: product.price_cents,
        },
      ],
    });
    expect(created.status(), await created.text()).toBe(201);
    orderId = (await created.json()).id;
    await page.goto("/");
    await expect(banner).toContainText("等待商家接单");
    await expect(banner).toContainText(stall.name);
    expect(
      (
        await mutate(merchant, `/merchant/orders/${orderId}/action`, {
          action: "accept",
        })
      ).ok(),
    ).toBeTruthy();
    await sync();
    await expect(banner).toContainText("正在制作");
    expect(
      (
        await mutate(merchant, `/merchant/orders/${orderId}/action`, {
          action: "ready",
        })
      ).ok(),
    ).toBeTruthy();
    await sync();
    await expect(banner).toContainText("餐点做好啦");
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await assertNoHorizontalOverflow(page);
      await expect(
        banner.getByRole("link", { name: "查看取餐码" }),
      ).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`student-ready-${width}.png`),
        fullPage: true,
      });
    }
    await banner.getByRole("link", { name: "查看取餐码" }).click();
    await expect(page).toHaveURL(new RegExp(`/orders/${orderId}$`));
    await expect(banner).toHaveCount(0);
    await page.goto("/cart");
    await expect(banner).toContainText("餐点做好啦");
    await page.route("**/api/v1/orders/active-summary", (route) =>
      route.abort("failed"),
    );
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(banner).toContainText("订单状态暂未同步");
    await expect(banner.getByRole("link", { name: "查看取餐码" })).toHaveCount(
      0,
    );
    await page.unroute("**/api/v1/orders/active-summary");
    await banner.getByRole("button", { name: "重新同步订单状态" }).click();
    await expect(banner).toContainText("餐点做好啦");

    // Delay the previous account's result while another tab changes the shared session cookie.
    let release!: () => void;
    let fetched!: () => void;
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    const captured = new Promise<void>((resolve) => {
      fetched = resolve;
    });
    await page.route(
      "**/api/v1/orders/active-summary",
      async (route) => {
        const response = await route.fetch();
        const body = await response.body();
        fetched();
        await blocked;
        await route.fulfill({ response, body }).catch(() => {});
      },
      { times: 1 },
    );
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await captured;
    expect((await mutate(context, "/auth/login", other)).ok()).toBeTruthy();
    const identity = page.waitForResponse((response) =>
      response.url().endsWith("/auth/me"),
    );
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await identity;
    release();
    await expect(banner).toHaveCount(0);
    await page.unroute("**/api/v1/orders/active-summary");
    await sync();
    await expect(banner).toHaveCount(0);
    expect((await mutate(context, "/auth/logout", {})).ok()).toBeTruthy();
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.locator(".header-user")).toHaveText("我的");
    await expect(banner).toHaveCount(0);
  } finally {
    await page.unroute("**/api/v1/orders/active-summary");
    await mutate(context, "/auth/login", customer);
    if (orderId) {
      const response = await context.request.get(
        `${baseURL}/api/v1/orders/${orderId}`,
      );
      const current = await response.json();
      if (["pending", "preparing", "ready"].includes(current.status)) {
        const cancelled = await mutate(context, `/orders/${orderId}/cancel`, {
          reason: "提醒测试结束，恢复库存",
        });
        expect(cancelled.ok(), await cancelled.text()).toBeTruthy();
        if (current.status !== "pending") {
          const approved = await mutate(
            merchant,
            `/merchant/orders/${orderId}/action`,
            { action: "approve_cancel" },
          );
          expect(approved.ok(), await approved.text()).toBeTruthy();
        }
      }
    }
    const restored = await mutate(
      merchant,
      `/merchant/stalls/${stall.id}/status`,
      {
        status: stall.session_status || "closed",
        closes_at: stall.closes_at,
      },
    );
    expect(restored.ok(), await restored.text()).toBeTruthy();
    await merchant.close();
  }
});
