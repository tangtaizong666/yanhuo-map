import { expect, test, type Page } from "@playwright/test";
import { fulfillCsrf, assertNoHorizontalOverflow } from "../helpers";

function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function fixture(page: Page) {
  const student = (id: number) => ({
    id,
    username: `profile_${id}`,
    display_name: `同学${id}`,
    is_merchant: false,
    is_staff: false,
  });
  const stall = (id: number) => ({
    id,
    name: `已关注小摊${id}`,
    image: "/images/food-cold-noodles.jpg",
    address: "校园南门",
    status: "open",
    is_followed: true,
  });
  const state = {
    user: student(1101),
    failFollows: false,
    failOrders: false,
    failMore: false,
    next: null as string | null,
    holdFollows: undefined as ReturnType<typeof deferred> | undefined,
    holdOrders: undefined as ReturnType<typeof deferred> | undefined,
    holdMore: undefined as ReturnType<typeof deferred> | undefined,
    reads: { follows: 0, orders: 0, more: 0 },
    unexpected: [] as string[],
  };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    const send = (json: unknown, status = 200) =>
      route.fulfill({ json, status });
    if (path === "/config")
      return send({
        user: state.user,
        demo_mode: true,
        areas: [],
        amap_key: "",
        services_simulation_enabled: false,
      });
    if (path === "/auth/me") return send(state.user);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/orders/active-summary")
      return send({
        user_id: state.user.id,
        counts: { total: 0 },
        order: null,
      });
    if (path === "/orders/recent-completed") return send([]);
    if (path === "/orders") {
      state.reads.orders++;
      const actor = state.user.id;
      const fail = state.failOrders;
      const held = state.holdOrders;
      state.holdOrders = undefined;
      if (held) await held.promise;
      return send(
        fail
          ? { detail: "订单统计暂时无法读取" }
          : {
              results: [],
              next: null,
              counts: { all: actor === 1101 ? 7 : 2, reviewed: 3 },
            },
        fail ? 503 : 200,
      );
    }
    if (path === "/follows") {
      const more = url.searchParams.has("cursor");
      const actor = state.user.id;
      state.reads[more ? "more" : "follows"]++;
      const fail = more ? state.failMore : state.failFollows;
      const held = more ? state.holdMore : state.holdFollows;
      if (more) state.holdMore = undefined;
      else state.holdFollows = undefined;
      // An overlapping cursor page can contain an older copy of the first row.
      const rows =
        actor === 1101
          ? more
            ? [stall(1), stall(2)]
            : [stall(1)]
          : [stall(3)];
      const next = more ? null : state.next;
      if (held) await held.promise;
      return send(
        fail
          ? { detail: more ? "更多关注暂时无法读取" : "关注列表暂时无法读取" }
          : {
              results: rows,
              next,
            },
        fail ? 503 : 200,
      );
    }
    const follow = path.match(/^\/stalls\/(\d+)\/follow$/);
    if (follow && route.request().method() === "DELETE")
      return send({ id: Number(follow[1]), is_followed: false });
    state.unexpected.push(path);
    return send({ detail: "Unexpected fixture request" }, 500);
  });
  return { state, student };
}

test("profile keeps loaded follows when order counts fail and retries only the failed section", async ({
  page,
}) => {
  const { state } = await fixture(page);
  state.failOrders = true;
  await page.goto("/me");
  await expect(
    page.getByRole("heading", { name: "已关注小摊1", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("alert").filter({ hasText: "订单统计暂时无法读取" }),
  ).toBeVisible();
  await expect(page.locator(".profile-stats a").first()).toContainText("—");
  await expect(
    page.getByText("把喜欢的小摊，留在这里", { exact: true }),
  ).toHaveCount(0);
  state.failOrders = false;
  await page.getByRole("button", { name: "重试订单统计", exact: true }).click();
  await expect(page.locator(".profile-stats a").first()).toContainText("7");
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(state.reads).toEqual({ follows: 1, orders: 2, more: 0 });
  expect(state.unexpected).toEqual([]);
});

test("profile keeps successful counts and offers a retry instead of an empty follow state", async ({
  page,
}) => {
  const { state } = await fixture(page);
  state.failFollows = true;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/me");
  await expect(page.locator(".profile-stats a").first()).toContainText("7");
  await expect(
    page.getByRole("alert").filter({ hasText: "关注列表暂时无法读取" }),
  ).toBeVisible();
  await expect(
    page.getByText("把喜欢的小摊，留在这里", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("0 个关注", { exact: true })).toHaveCount(0);
  await expect(page.locator(".profile-stats button")).toContainText("—");
  state.failFollows = false;
  await page.getByRole("button", { name: "重试关注列表", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "已关注小摊1", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(state.reads).toEqual({ follows: 2, orders: 1, more: 0 });
  await assertNoHorizontalOverflow(page);
  expect(state.unexpected).toEqual([]);
});

test("profile renders follows while order statistics are still loading", async ({
  page,
}) => {
  const { state } = await fixture(page);
  const held = deferred();
  state.holdOrders = held;
  await page.goto("/me");
  try {
    await expect(
      page.getByRole("heading", { name: "已关注小摊1", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("status").filter({ hasText: "正在读取订单统计" }),
    ).toBeVisible();
    await expect(page.locator(".profile-stats a").first()).toContainText("—");
  } finally {
    held.release();
  }
  await expect(page.locator(".profile-stats a").first()).toContainText("7");
  expect(state.unexpected).toEqual([]);
});

test("profile preserves existing follows and clears the pagination error after a successful retry", async ({
  page,
}) => {
  const { state } = await fixture(page);
  state.next = "next-page";
  state.failMore = true;
  await page.goto("/me");
  await page.getByRole("button", { name: "加载更多关注", exact: true }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "更多关注暂时无法读取" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "已关注小摊1", exact: true }),
  ).toBeVisible();
  state.failMore = false;
  await page
    .getByRole("button", { name: "重试加载更多关注", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "已关注小摊2", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /加载更多关注/ })).toHaveCount(
    0,
  );
  expect(state.reads).toEqual({ follows: 1, orders: 1, more: 2 });
  expect(state.unexpected).toEqual([]);
});

test("profile does not restore an unfollowed row from a delayed overlapping cursor page", async ({
  page,
}) => {
  const { state } = await fixture(page);
  state.next = "next-page";
  const held = deferred();
  state.holdMore = held;
  await page.goto("/me");
  await page.getByRole("button", { name: "加载更多关注", exact: true }).click();
  await expect.poll(() => state.reads.more).toBe(1);
  try {
    await page
      .getByRole("button", { name: "取消关注已关注小摊1", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "已关注小摊1", exact: true }),
    ).toHaveCount(0);
  } finally {
    held.release();
  }
  await expect(
    page.getByRole("heading", { name: "已关注小摊2", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "已关注小摊1", exact: true }),
  ).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});

test("profile ignores old account follow and count responses after an identity refresh", async ({
  page,
}) => {
  const { state, student } = await fixture(page);
  const followRead = deferred(),
    orderRead = deferred();
  state.holdFollows = followRead;
  state.holdOrders = orderRead;
  await page.goto("/me");
  await expect
    .poll(() => state.reads)
    .toEqual({ follows: 1, orders: 1, more: 0 });
  state.user = student(1102);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  try {
    await expect(
      page.getByRole("heading", { name: "同学1102", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "已关注小摊3", exact: true }),
    ).toBeVisible();
  } finally {
    followRead.release();
    orderRead.release();
  }
  await expect(page.locator(".profile-stats a").first()).toContainText("2");
  await expect(
    page.getByRole("heading", { name: "已关注小摊1", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(state.unexpected).toEqual([]);
});
