import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow } from "../helpers";

// Synthetic responses only. Unrecognized API requests fail rather than reach a DB.
async function fixture(page: Page, path = "/merchant/orders?filter=all") {
  const user = { id: 1801, username: "merchant_sync_fixture", is_merchant: true, is_staff: false };
  const stall = { id: 1801, name: "南门合成饭摊", address: "南门东侧第三个摊位", latitude: 30, longitude: 120,
    status: "open", session_status: "open", products: [], reviews: [], image: "", prep_minutes: 10,
    accepting_orders: true, transaction_enabled: true, is_visible: true, can_order: true,
    activation: { has_location: true, steps: [] }, services: { mode: "live" },
    last_confirmed_at: "2026-10-12T10:00:00+08:00", closes_at: "2026-10-12T20:00:00+08:00",
    stop_orders_at: "2026-10-12T19:30:00+08:00" };
  const make = (id: string, status: string, date: string) => ({ id, number: id, stall_id: stall.id, stall_name: stall.name,
    mode: "live", fulfillment_type: "pickup", status, payment_method: "offline", payment_status: "paid",
    payment: null, refund: null, review: null, cancel_requested: false, payment_review_required: false,
    created_at: date, total_cents: 1000, pickup_address: stall.address,
    items: [{ product_id: 1, name: `合成餐点${id}`, image: "", quantity: 1, unit_price_cents: 1000, portions: [] }] });
  const state = { rows: [make("remote-complete", "preparing", "2026-10-12T09:03:00Z"),
    make("remote-cancel", "ready", "2026-10-12T09:02:00Z"), make("old-completed", "completed", "2026-10-12T09:01:00Z")],
    failures: {} as Record<string, number>, attentionFailed: false, reads: [] as string[], unexpected: [] as string[], stall,
    stalls: [stall] };
  await page.route("https://**/*", route => route.abort());
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url()), endpoint = url.pathname.replace("/api/v1", "");
    state.reads.push(endpoint + url.search);
    const send = (json: unknown) => route.fulfill({ json });
    if (endpoint === "/config") return send({ user, demo_mode: false, areas: [], stale_minutes: 60, amap_key: "" });
    if (endpoint === "/auth/me") return send(user);
    if (endpoint === "/events") return send({});
    if (endpoint === "/merchant/stalls") return send(state.stalls);
    if (endpoint === "/merchant/reviews") return send(url.searchParams.get("mode") === "simulation"
      ? [{ id: 2, mode: "simulation", rating: 5, content: "合成模拟评价", display_name: "演练顾客", merchant_reply: "", replied_at: null, created_at: "2026-10-12T09:00:00Z" }]
      : [{ id: 1, mode: "live", rating: 3, content: "合成真实评价", display_name: "顾客", merchant_reply: "", replied_at: null, created_at: "2026-10-12T09:00:00Z" }]);
    if (endpoint === "/merchant/orders") {
      const filter = url.searchParams.get("filter");
      if (filter === "attention" && state.attentionFailed) return route.fulfill({ status: 500, json: { detail: "合成断网" } });
      const stallRows = url.searchParams.get("stall") === String(stall.id) ? state.rows : [];
      const active = stallRows.filter(row => ["preparing", "ready"].includes(row.status));
      let rows = filter === "attention" ? active : filter === "all" ? stallRows : stallRows.filter(row => row.status === filter);
      const more = url.searchParams.has("cursor");
      const paginated = filter === "all";
      const next = paginated && !more && rows.length > 1 ? "second" : null;
      if (paginated) rows = more ? rows.slice(1) : rows.slice(0, 1);
      return send({ results: rows, next, counts: { all: state.rows.length, attention: active.length,
        preparing: active.filter(row => row.status === "preparing").length, ready: active.filter(row => row.status === "ready").length,
        completed: state.rows.filter(row => row.status === "completed").length, cancelled: state.rows.filter(row => row.status === "cancelled").length } });
    }
    const id = endpoint.match(/^\/merchant\/orders\/([^/]+)$/)?.[1];
    if (id) {
      if (state.failures[id]) return route.fulfill({ status: state.failures[id], json: { detail: "合成补读失败" } });
      const order = state.rows.find(row => row.id === id);
      return order ? send(order) : route.fulfill({ status: 404, json: { detail: "不存在" } });
    }
    state.unexpected.push(endpoint);
    return route.fulfill({ status: 500, json: { detail: "Unexpected fixture request" } });
  });
  await page.goto(path);
  await expect(page.getByRole("button", { name: "刷新工作台" })).toBeEnabled();
  return state;
}
const stages = (page: Page) => page.getByRole("group", { name: "订单阶段" });
const history = (page: Page, name: string) => page.getByRole("group", { name: "其他订单记录" }).getByRole("button", { name });
async function refresh(page: Page) {
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await expect(page.getByRole("button", { name: "刷新工作台" })).toBeEnabled();
}

test("merchant loaded history reconciles remote completion and cancellation without discarding pages", async ({ page }) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: /加载更多/ }).click();
  await expect(page.locator(".m-orders")).toContainText("合成餐点old-completed");
  await history(page, /已完成/).click();
  await history(page, /全部记录/).click();
  state.rows[0]!.status = "completed";
  state.rows[1]!.status = "cancelled";
  await refresh(page);
  await expect(stages(page).getByRole("button", { name: /制作中/ })).toContainText("0");
  await expect(stages(page).getByRole("button", { name: /待取餐/ })).toContainText("0");
  await expect(page.locator(".m-orders-card")).toHaveCount(3);
  await expect(page.locator(".m-orders")).toContainText("合成餐点old-completed");
  await history(page, /已完成/).click();
  await expect(page.locator(".m-orders-card")).toHaveCount(2);
  await expect(page.locator(".m-orders")).toContainText("合成餐点remote-complete");
  const details = state.reads.filter(path => /^\/merchant\/orders\//.test(path)).length;
  await refresh(page);
  expect(state.reads.filter(path => /^\/merchant\/orders\//.test(path))).toHaveLength(details);
  expect(state.unexpected).toEqual([]);
});

test("merchant history partial failure stays unsynced across tabs and refresh until verified", async ({ page }) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: /加载更多/ }).click();
  state.rows[0]!.status = "completed"; state.rows[1]!.status = "cancelled";
  state.failures["remote-complete"] = 500;
  await refresh(page);
  await expect(page.locator(".m-orders-warning").filter({ hasText: "笔已加载订单" })).toContainText("暂未同步");
  state.attentionFailed = true;
  await refresh(page);
  await expect(page.locator(".m-orders-warning").last()).toContainText("暂未同步");
  state.attentionFailed = false;
  await expect(stages(page).getByRole("button", { name: /待取餐/ })).toContainText("0");
  await expect(page.getByRole("button", { name: "做好了", exact: true })).toHaveCount(0);
  await history(page, /已完成/).click();
  await expect(page.locator(".m-orders-warning").filter({ hasText: "笔已加载订单" })).toContainText("暂未同步");
  await refresh(page);
  await expect(page.locator(".m-orders-warning").filter({ hasText: "笔已加载订单" })).toContainText("暂未同步");
  delete state.failures["remote-complete"];
  await refresh(page);
  await expect(page.locator(".m-orders-warning")).toHaveCount(0);
  await expect(page.locator(".m-orders")).toContainText("合成餐点remote-complete");
  expect(state.unexpected).toEqual([]);
});

for (const status of [403, 404]) test(`merchant inaccessible ${status} historical order is removed from every cached filter`, async ({ page }) => {
  const state = await fixture(page);
  await page.getByRole("button", { name: /加载更多/ }).click();
  state.rows[0]!.status = "completed"; state.failures["remote-complete"] = status;
  await refresh(page);
  await expect(page.locator(".m-orders")).not.toContainText("合成餐点remote-complete");
  await history(page, /已完成/).click(); await history(page, /全部记录/).click();
  await expect(page.locator(".m-orders")).not.toContainText("合成餐点remote-complete");
  expect(state.unexpected).toEqual([]);
});

test("merchant home shows current location confirmation and session times on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, "/merchant");
  const summary = page.locator(".m-business-compact");
  await expect(summary).toContainText("南门东侧第三个摊位");
  await expect(summary).toContainText("位置确认");
  await expect(summary).toContainText("预计收摊");
  await expect(summary).toContainText("停止接单");
  await stages(page).getByRole("button", { name: /制作中/ }).click();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await assertNoHorizontalOverflow(page);
    await page.screenshot({ path: test.info().outputPath(`merchant-home-${width}.png`), fullPage: true });
  }
});

test("merchant delayed history verification cannot return another stall's order", async ({ page }) => {
  const state = await fixture(page);
  state.stalls.push({ ...state.stall, id: 1802, name: "北门合成饭摊" });
  await refresh(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let requested = false;
  await page.route("**/api/v1/merchant/orders/remote-complete?*", async route => {
    requested = true;
    await held;
    await route.fulfill({ json: { ...state.rows[0], status: "completed" } }).catch(() => {});
  });
  state.rows[0]!.status = "completed";
  await refresh(page);
  await expect.poll(() => requested).toBe(true);
  await page.getByRole("combobox", { name: "选择管理的摊位" }).selectOption("1802");
  await expect(page.locator(".m-business-compact")).toContainText("北门合成饭摊");
  release();
  await refresh(page);
  await expect(page.locator(".m-orders")).not.toContainText("合成餐点remote-complete");
  await expect(page.locator(".m-orders-warning")).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

for (const route of ["apply", "unknown-section"]) {
  test(`merchant direct ${route} entry returns to workbench`, async ({ page }) => {
    await fixture(page, `/merchant/${route}`);
    await expect(page).toHaveURL(/\/merchant$/);
    await expect(page.locator(".m-business-compact")).toBeVisible();
  });
}

test("merchant review mode separates real ratings from simulation", async ({ page }) => {
  await fixture(page, "/merchant/reviews");
  await expect(page.locator(".average-score")).toContainText("3.0");
  await expect(page.locator(".reviews-panel")).toContainText("合成真实评价");
  await page.getByRole("button", { name: "模拟评价", exact: true }).click();
  await expect(page.locator(".average-score")).toContainText("5.0");
  await expect(page.locator(".reviews-panel")).toContainText("合成模拟评价");
  await expect(page.locator(".average-score")).not.toContainText("真实订单评价");
  await page.getByRole("button", { name: "真实评价", exact: true }).click();
  await expect(page.locator(".average-score")).toContainText("3.0");
  await expect(page.locator(".reviews-panel")).not.toContainText("合成模拟评价");
});
