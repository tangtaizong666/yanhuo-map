import { publicResponse, mealResponse } from "./public-contracts";
import { expect, test, type Page } from "@playwright/test";
import { fulfillCsrf } from "./helpers";

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function fixture(page: Page, initiallyFollowed = false) {
  let user: any = {
    id: 901,
    username: "follow_fixture",
    display_name: "关注测试同学",
    is_merchant: false,
    is_staff: false,
  };
  const stalls = [1, 2].map((id) => ({
    id,
    name: `测试小摊${id}`,
    description: "独立页面验证餐点",
    category: "小吃",
    image: "/images/food-cold-noodles.jpg",
    area_id: 1,
    area_name: "测试校园",
    address: "校园南门",
    latitude: 31.2,
    longitude: 121.4,
    status: "open",
    session_status: "open",
    last_confirmed_at: new Date().toISOString(),
    closes_at: null,
    prep_minutes: 10,
    transaction_enabled: true,
    can_order: true,
    qualification_note: "示例",
    merchant_name: "测试商家",
    contact_phone: "",
    rating: null,
    review_count: 0,
    order_count: 0,
    distance_m: null,
    is_followed: initiallyFollowed,
    products: [],
    reviews: [],
    wechat_payment: { mode: "live", available: false, channels: [] },
    delivery: { enabled: false, available: false, points: [] },
  }));
  const writes: { id: number; method: string }[] = [];
  const unexpected: string[] = [];
  let heldWrite: ReturnType<typeof deferred> | undefined;
  let heldCsrf: ReturnType<typeof deferred> | undefined;
  let csrfReads = 0;
  let heldList: ReturnType<typeof deferred> | undefined;
  let failNext = false;
  let lists = 0;
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/auth/csrf") {
      csrfReads++;
      const wait = heldCsrf;
      heldCsrf = undefined;
      if (wait) await wait.promise;
      return fulfillCsrf(route);
    }
    if (path === "/config")
      return route.fulfill({
        json: {
          demo_mode: true,
          brand: "烟火地图",
          amap_key: "",
          areas: [
            { id: 1, name: "测试校园", latitude: 31.2, longitude: 121.4 },
          ],
          user,
        },
      });
    if (path === "/auth/me") return route.fulfill({ json: user });
    if (path === "/auth/logout") {
      user = null;
      return route.fulfill({ json: { detail: "已退出登录" } });
    }
    if (path === "/orders/active-summary")
      return route.fulfill({
        json: { user_id: user?.id, counts: { total: 0 }, order: null },
      });
    if (path === "/orders" || path === "/orders/recent-completed")
      return route.fulfill({ json: [] });
    if (path === "/events") return route.fulfill({ json: { ok: true } });
    const match = path.match(/^\/stalls\/(\d+)\/follow$/);
    if (match) {
      const id = Number(match[1]);
      writes.push({ id, method: route.request().method() });
      const wait = heldWrite;
      heldWrite = undefined;
      if (wait) await wait.promise;
      if (failNext) {
        failNext = false;
        return route.fulfill({
          status: 503,
          json: { detail: "关注暂未保存，请重试" },
        });
      }
      const stall = stalls.find((item) => item.id === id)!;
      stall.is_followed = route.request().method() === "POST";
      return route.fulfill({ json: publicResponse(path, stall) });
    }
    if (path === "/products")
      return route.fulfill({
        json: mealResponse(stalls, new URL(route.request().url()).searchParams),
      });
    if (path === "/stalls" || path === "/stalls/map") {
      lists++;
      const snapshot = structuredClone(stalls).map((stall) => ({
        ...stall,
        is_followed: !!user && stall.is_followed,
      }));
      const wait = heldList;
      heldList = undefined;
      if (wait) await wait.promise;
      return route.fulfill({ json: publicResponse(path, snapshot) });
    }
    if (path === "/follows")
      return route.fulfill({
        json: publicResponse(
          path,
          stalls.filter((stall) => stall.is_followed),
        ),
      });
    const detail = path.match(/^\/stalls\/(\d+)$/);
    if (detail)
      return route.fulfill({
        json: publicResponse(
          path,
          stalls.find((stall) => stall.id === Number(detail[1])),
        ),
      });
    unexpected.push(path);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected fixture request" },
    });
  });
  return {
    writes,
    unexpected,
    get lists() {
      return lists;
    },
    get csrfReads() {
      return csrfReads;
    },
    holdCsrf() {
      const wait = deferred();
      heldCsrf = wait;
      return wait;
    },
    holdWrite() {
      const wait = deferred();
      heldWrite = wait;
      return wait;
    },
    holdList() {
      const wait = deferred();
      heldList = wait;
      return wait;
    },
    failWrite() {
      failNext = true;
    },
  };
}

test("首页关注双击只提交一次，其他摊位仍可操作", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/");
  const button = page.getByRole("button", {
    name: "关注测试小摊1",
    exact: true,
  });
  const pending = state.holdWrite();
  await button.evaluate((element: HTMLButtonElement) => {
    element.click();
    element.click();
  });
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(button).toBeDisabled();
  await expect(button).toHaveAttribute("aria-busy", "true");
  await expect(
    page.getByRole("button", { name: "关注测试小摊2", exact: true }),
  ).toBeEnabled();
  pending.release();
  await expect(
    page.getByRole("button", { name: "取消关注测试小摊1", exact: true }),
  ).toBeEnabled();
  expect(state.writes).toEqual([{ id: 1, method: "POST" }]);
  expect(state.unexpected).toEqual([]);
});

test("关注失败保留原状态且允许重试", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/");
  state.failWrite();
  const button = page.getByRole("button", {
    name: "关注测试小摊1",
    exact: true,
  });
  await button.click();
  await expect(
    page.getByText("关注暂未保存，请重试", { exact: true }),
  ).toBeVisible();
  await expect(button).toBeEnabled();
  await button.click();
  await expect(
    page.getByRole("button", { name: "取消关注测试小摊1", exact: true }),
  ).toBeEnabled();
  expect(state.writes.map((write) => write.method)).toEqual(["POST", "POST"]);
});

test("地图刷新替换列表对象后，关注结果更新当前卡片", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/map");
  const pending = state.holdWrite();
  await page
    .getByRole("button", { name: "关注测试小摊1", exact: true })
    .click();
  await expect.poll(() => state.writes.length).toBe(1);
  const refreshed = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/stalls/map",
  );
  await page.getByRole("button", { name: "刷新摊位", exact: true }).click();
  await refreshed;
  await expect(
    page.getByRole("button", { name: "关注测试小摊1", exact: true }),
  ).toBeDisabled();
  pending.release();
  await expect(
    page.getByRole("button", { name: "取消关注测试小摊1", exact: true }),
  ).toBeEnabled();
  expect(state.writes).toHaveLength(1);
});

test("晚到的旧地图列表不能覆盖已确认的关注结果", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/map");
  await expect(
    page.getByRole("button", { name: "关注测试小摊1", exact: true }),
  ).toBeVisible();
  const before = state.lists;
  const oldList = state.holdList();
  await page.getByRole("button", { name: "刷新摊位", exact: true }).click();
  await expect.poll(() => state.lists).toBeGreaterThan(before);
  await page
    .getByRole("button", { name: "关注测试小摊1", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "取消关注测试小摊1", exact: true }),
  ).toBeEnabled();
  const delivered = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/stalls/map",
  );
  oldList.release();
  await delivered;
  await expect(
    page.getByRole("button", { name: "取消关注测试小摊1", exact: true }),
  ).toBeEnabled();
  expect(state.writes).toHaveLength(1);
});

test("摊位详情双击只提交一次，切换摊位后旧结果不污染新摊位", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/stalls/1");
  const pending = state.holdWrite();
  const button = page.getByRole("button", { name: "关注小摊", exact: true });
  await button.evaluate((element: HTMLButtonElement) => {
    element.click();
    element.click();
  });
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(button).toBeDisabled();
  await page.evaluate(() => {
    history.pushState({}, "", "/stalls/2");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(
    page.getByRole("heading", { name: "测试小摊2", exact: true }),
  ).toBeVisible();
  pending.release();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    page.getByRole("button", { name: "关注小摊", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "关注小摊", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  await expect(
    page.getByText("已关注，下次找摊更方便。", { exact: true }),
  ).toHaveCount(0);
});

test("我的关注取消双击只有一次 DELETE", async ({ page }) => {
  const state = await fixture(page, true);
  await page.goto("/me");
  const pending = state.holdWrite();
  const button = page.getByRole("button", {
    name: "取消关注测试小摊1",
    exact: true,
  });
  await button.evaluate((element: HTMLButtonElement) => {
    element.click();
    element.click();
  });
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(button).toBeDisabled();
  await expect(button).toHaveAttribute("aria-busy", "true");
  pending.release();
  await expect(button).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "取消关注测试小摊2", exact: true }),
  ).toBeEnabled();
  expect(state.writes).toEqual([{ id: 1, method: "DELETE" }]);
});

test("退出登录后，旧账号取消关注响应不写入游客页面", async ({ page }) => {
  const state = await fixture(page, true);
  await page.goto("/me");
  const pending = state.holdWrite();
  await page
    .getByRole("button", { name: "取消关注测试小摊1", exact: true })
    .click();
  await expect.poll(() => state.writes.length).toBe(1);
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page).not.toHaveURL((url) => url.pathname === "/me");
  pending.release();
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "关注测试小摊1", exact: true }),
  ).toBeEnabled();
  await expect(page.getByText("已取消关注", { exact: true })).toHaveCount(0);
});

test("离开页面会取消等待 CSRF 的关注操作，不在校验恢复后补发写请求", async ({
  page,
}) => {
  const state = await fixture(page, true);
  await page.goto("/me");
  await expect(
    page.getByRole("button", { name: "取消关注测试小摊1", exact: true }),
  ).toBeVisible();
  await page.context().clearCookies();
  const pending = state.holdCsrf();
  const before = state.csrfReads;
  await page
    .getByRole("button", { name: "取消关注测试小摊1", exact: true })
    .click();
  await expect.poll(() => state.csrfReads).toBeGreaterThan(before);
  expect(state.writes).toEqual([]);
  await page.evaluate(() => {
    history.pushState({}, "", "/");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole("heading", { name: "今天的好味，就在附近。", exact: true }),
  ).toBeVisible();
  const delivered = page.waitForResponse("**/api/v1/auth/csrf");
  pending.release();
  await delivered;
  await expect(
    page.getByRole("button", { name: "取消关注测试小摊1", exact: true }),
  ).toBeEnabled();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  expect(state.writes).toEqual([]);
  await expect(page.getByText("已取消关注", { exact: true })).toHaveCount(0);
});
