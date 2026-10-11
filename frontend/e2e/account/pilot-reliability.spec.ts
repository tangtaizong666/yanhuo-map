import { expect, test, type Page, type Route } from "@playwright/test";
import { assertNoHorizontalOverflow } from "../helpers";

// Synthetic API responses only: these tests never read or mutate real orders.
function order(id: string, status = "ready", followup = false): any {
  return {
    id,
    number: id,
    stall_id: 801,
    stall_name: `同步测试 ${id}`,
    stall_image: "/images/food-jianbing.jpg",
    status,
    mode: "live",
    fulfillment_type: "pickup",
    payment_method: "offline",
    payment_status: status === "completed" ? "paid" : "unpaid",
    payment: null,
    refund: null,
    review: null,
    payment_review_required: false,
    financial_hold_reason: followup ? "测试款项待核对" : "",
    cancel_requested: false,
    created_at: "2026-10-11T02:00:00Z",
    pickup_address: "测试校园南门",
    total_cents: 1200,
    items: [{ name: "煎饼", quantity: 1, image: "/images/food-jianbing.jpg" }],
    wechat_payment: { available: false },
  };
}

async function fixture(page: Page) {
  const user = {
    id: 801,
    username: "synthetic-order-sync",
    display_name: "同步测试",
    is_merchant: false,
    is_staff: false,
  };
  const state = {
    orders: [] as any[],
    detailFailures: new Map<string, number | "offline">(),
    listFailures: new Set<string>(),
    detailReads: [] as string[],
    listReads: [] as string[],
    unexpected: [] as string[],
  };
  const matches = (row: any, filter: string) => {
    const active = ["pending", "preparing", "ready"].includes(row.status);
    if (filter === "all") return true;
    if (filter === "active") return active;
    if (filter === "followup") return !!row.financial_hold_reason;
    if (filter === "attention") return active || !!row.financial_hold_reason;
    if (filter === "cancelled")
      return ["cancelled", "rejected"].includes(row.status);
    return row.status === filter;
  };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/config")
      return route.fulfill({
        json: {
          user,
          areas: [],
          demo_mode: false,
          amap_key: "",
          stale_minutes: 60,
        },
      });
    if (path === "/auth/me") return route.fulfill({ json: user });
    if (path === "/orders/active-summary")
      return route.fulfill({
        json: { user_id: user.id, counts: { total: 0 }, order: null },
      });
    if (path === "/events") return route.fulfill({ json: {} });
    if (path === "/orders") {
      const filter = url.searchParams.get("filter") || "all";
      state.listReads.push(filter);
      if (state.listFailures.has(filter))
        return route.fulfill({
          status: 500,
          json: { detail: "测试列表读取失败" },
        });
      const counts = Object.fromEntries(
        [
          "all",
          "active",
          "followup",
          "attention",
          "completed",
          "cancelled",
        ].map((key) => [
          key,
          state.orders.filter((row) => matches(row, key)).length,
        ]),
      );
      return route.fulfill({
        json: {
          results: state.orders.filter((row) => matches(row, filter)),
          next: null,
          counts,
        },
      });
    }
    if (path.startsWith("/orders/")) {
      const id = path.slice("/orders/".length);
      state.detailReads.push(id);
      const failure = state.detailFailures.get(id);
      if (failure === "offline") return route.abort("failed");
      if (failure)
        return route.fulfill({
          status: failure,
          json: { detail: "测试订单暂时无法读取" },
        });
      const found = state.orders.find((row) => row.id === id);
      return route.fulfill({
        status: found ? 200 : 404,
        json: found || { detail: "订单不存在" },
      });
    }
    state.unexpected.push(path);
    return route.fulfill({
      status: 500,
      json: { detail: `Unexpected fixture request: ${path}` },
    });
  });
  await page.clock.install();
  return state;
}

const card = (page: Page, id: string) =>
  page.locator(`.order-card[href="/orders/${id}"]`);
const warning = (page: Page) => page.locator(".orders-page [role='alert']");

for (const failure of [500, "offline"] as const) {
  test(`order sync retains uncertainty after ${failure}, updates other orders and retries`, async ({
    page,
  }, testInfo) => {
    const state = await fixture(page);
    state.orders = [order("retry"), order("success", "preparing")];
    await page.goto("/orders");
    await expect(card(page, "retry")).toBeVisible();
    await page.getByRole("button", { name: "进行中", exact: false }).click();
    await expect(card(page, "success")).toBeVisible();
    await page.getByRole("button", { name: "全部订单", exact: true }).click();

    state.orders = [order("retry", "cancelled"), order("success", "completed")];
    state.detailFailures.set("retry", failure);
    await page.clock.fastForward(10_001);
    await expect(card(page, "success")).toContainText("已完成");
    await expect(warning(page)).toBeVisible();
    await expect(card(page, "retry")).toContainText("状态尚未同步");
    await expect(page.locator(".sync-status").first()).toContainText(
      "尚未获取最新订单状态",
    );

    // Loading or refreshing another filter cannot certify the unresolved order.
    await page.getByRole("button", { name: "已完成", exact: true }).click();
    await expect(card(page, "success")).toBeVisible();
    await expect(warning(page)).toBeVisible();
    await page.getByRole("button", { name: "刷新订单", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "刷新订单", exact: true }),
    ).toBeEnabled();
    await expect(warning(page)).toBeVisible();
    await page.getByRole("button", { name: "进行中", exact: false }).click();
    await expect(card(page, "retry")).toBeVisible();
    await expect(card(page, "success")).toHaveCount(0);
    await expect(warning(page)).toBeVisible();

    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await assertNoHorizontalOverflow(page);
      await expect(warning(page)).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`order-sync-${failure}-${width}.png`),
        fullPage: true,
      });
    }

    state.detailFailures.delete("retry");
    await page.clock.fastForward(10_001);
    await expect
      .poll(() => state.detailReads.filter((id) => id === "retry").length)
      .toBeGreaterThanOrEqual(2);
    await expect(card(page, "retry")).toHaveCount(0);
    await expect(warning(page)).toHaveCount(0);
    await page.getByRole("button", { name: "全部订单", exact: true }).click();
    await expect(card(page, "retry")).toContainText("已取消");
    await expect(card(page, "success")).toContainText("已完成");
    expect(state.unexpected).toEqual([]);
  });
}

test("refreshing a previously unreadable order updates every cached filter before clearing uncertainty", async ({
  page,
}) => {
  const state = await fixture(page);
  state.orders = [order("refreshed")];
  await page.goto("/orders");
  await expect(card(page, "refreshed")).toBeVisible();
  await page.getByRole("button", { name: "进行中", exact: false }).click();
  await expect(card(page, "refreshed")).toBeVisible();
  await page.getByRole("button", { name: "全部订单", exact: true }).click();

  state.orders = [order("refreshed", "completed")];
  state.detailFailures.set("refreshed", 500);
  await page.clock.fastForward(10_001);
  await expect(warning(page)).toBeVisible();
  await page.getByRole("button", { name: "刷新订单", exact: true }).click();
  await expect(card(page, "refreshed")).toContainText("已完成");
  await expect(warning(page)).toHaveCount(0);
  await page.getByRole("button", { name: "进行中", exact: false }).click();
  await expect(card(page, "refreshed")).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("successful attention polling cannot hide a failed history refresh", async ({
  page,
}) => {
  const state = await fixture(page);
  state.orders = [order("history", "completed")];
  await page.goto("/orders");
  await expect(card(page, "history")).toBeVisible();
  state.listFailures.add("all");
  await page.getByRole("button", { name: "刷新订单", exact: true }).click();
  await expect(warning(page)).toContainText("测试列表读取失败");
  await page.clock.fastForward(10_001);
  await expect.poll(() => state.listReads).toContain("attention");
  await expect(warning(page)).toContainText("测试列表读取失败");
  state.listFailures.clear();
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(warning(page)).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("manual retry immediately verifies attention recovery and stays busy until it finishes", async ({
  page,
}) => {
  const state = await fixture(page);
  state.orders = [order("recovered")];
  await page.goto("/orders");
  await expect(card(page, "recovered")).toBeVisible();
  await page.clock.pauseAt(
    new Date((await page.evaluate(() => Date.now())) + 1_000),
  );
  state.listFailures.add("attention");
  await page.clock.fastForward(10_001);
  await expect(warning(page)).toContainText("测试列表读取失败");

  state.listFailures.clear();
  let held: Route | undefined;
  let attentionReads = 0;
  await page.route("**/api/v1/orders?**", (route) => {
    if (
      new URL(route.request().url()).searchParams.get("filter") !== "attention"
    )
      return route.fallback();
    attentionReads++;
    held = route;
  });
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect.poll(() => attentionReads).toBe(1);
  const refresh = page.getByRole("button", { name: "刷新订单", exact: true });
  await expect(refresh).toBeDisabled();
  await expect(warning(page)).toContainText("测试列表读取失败");
  await refresh.evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  expect(attentionReads).toBe(1);
  await held!.fulfill({
    json: {
      results: state.orders,
      next: null,
      counts: { all: 1, active: 1, attention: 1 },
    },
  });
  await expect(warning(page)).toHaveCount(0);
  await expect(refresh).toBeEnabled();
  expect(attentionReads).toBe(1);
  expect(state.unexpected).toEqual([]);
});

test("manual retry verifies an unresolved order outside the selected filter without waiting for the timer", async ({
  page,
}) => {
  const state = await fixture(page);
  state.orders = [order("outside"), order("history", "completed")];
  await page.goto("/orders");
  await expect(card(page, "outside")).toBeVisible();
  await page.clock.pauseAt(
    new Date((await page.evaluate(() => Date.now())) + 1_000),
  );
  state.orders = [order("outside", "cancelled"), order("history", "completed")];
  state.detailFailures.set("outside", "offline");
  await page.clock.fastForward(10_001);
  await expect(warning(page)).toBeVisible();
  await page.getByRole("button", { name: "已完成", exact: true }).click();
  await expect(card(page, "history")).toBeVisible();
  await expect(warning(page)).toBeVisible();
  state.detailFailures.clear();
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(warning(page)).toHaveCount(0);
  expect(state.detailReads.filter((id) => id === "outside")).toHaveLength(2);
  await page.getByRole("button", { name: "全部订单", exact: true }).click();
  await expect(card(page, "outside")).toContainText("已取消");
  expect(state.unexpected).toEqual([]);
});

for (const status of [403, 404]) {
  test(`order sync removes an inaccessible ${status} order from every cached filter`, async ({
    page,
  }) => {
    const state = await fixture(page);
    state.orders = [order("removed", "ready", true)];
    await page.goto("/orders");
    await expect(card(page, "removed")).toBeVisible();
    for (const name of ["进行中", "退款与待处理"]) {
      await page.getByRole("button", { name, exact: false }).click();
      await expect(card(page, "removed")).toBeVisible();
    }
    state.orders = [];
    state.detailFailures.set("removed", status);
    await page.clock.fastForward(10_001);
    await expect.poll(() => state.detailReads).toContain("removed");
    await expect(card(page, "removed")).toHaveCount(0);
    const reads = state.listReads.length;
    for (const name of ["全部订单", "进行中", "退款与待处理"]) {
      await page.getByRole("button", { name, exact: false }).click();
      await expect(card(page, "removed")).toHaveCount(0);
    }
    expect(state.listReads).toHaveLength(reads);
    await expect(warning(page)).toHaveCount(0);
    expect(state.unexpected).toEqual([]);
  });
}
