import { expect, test, type Page, type Route } from "@playwright/test";
import { fulfillCsrf } from "./helpers";

// Model another tab changing the shared session cookie. Every request stays in
// this fixture; no development account, order or payment is touched.
async function fixture(page: Page) {
  const first = {
    id: 9701,
    username: "identity_a",
    display_name: "同学 A",
    is_merchant: false,
    is_staff: false,
  };
  const second = {
    ...first,
    id: 9702,
    username: "identity_b",
    display_name: "同学 B",
  };
  const product = {
    id: 9701,
    name: "身份隔离测试餐点",
    description: "",
    image: "",
    price_cents: 1000,
    availability: "available",
    max_order_quantity: 10,
  };
  const stall = {
    id: 9701,
    name: "身份隔离测试小摊",
    products: [product],
    image: "",
    description: "",
    area_name: "校园",
    category: "小吃",
    address: "南门",
    status: "open",
    can_order: true,
    transaction_enabled: true,
    prep_minutes: 10,
    reviews: [],
    review_count: 0,
    rating: 0,
    contact_phone: "",
    wechat_payment: { available: false, channels: [], reason: "尚未开通" },
  };
  const state = {
    user: first,
    holdIdentity: false,
    identityChecks: [] as Route[],
    holdCsrf: false,
    csrfChecks: [] as Route[],
    enforceActor: false,
    rejectedActors: [] as string[],
    writes: [] as {
      path: string;
      actor: number;
      expectedActor: string | undefined;
      body: any;
    }[],
    unexpected: [] as string[],
  };
  await page.route("**/api/v1/**", (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    if (path === "/auth/csrf") {
      if (state.holdCsrf) {
        state.csrfChecks.push(route);
        return;
      }
      return fulfillCsrf(route);
    }
    if (path === "/config")
      return route.fulfill({
        json: { user: state.user, areas: [], demo_mode: true, amap_key: "" },
      });
    if (path === "/auth/me") {
      if (state.holdIdentity) {
        state.identityChecks.push(route);
        return;
      }
      return route.fulfill({ json: state.user });
    }
    if (path === "/stalls/9701") return route.fulfill({ json: stall });
    if (path === "/orders/active-summary")
      return route.fulfill({
        json: { user_id: state.user.id, counts: { total: 0 }, order: null },
      });
    if (path === "/events") return route.fulfill({ json: {} });
    if (["POST", "PATCH", "DELETE"].includes(request.method())) {
      const expectedActor = request.headers()["x-yanhuo-actor"];
      if (state.enforceActor && expectedActor !== String(state.user.id)) {
        state.rejectedActors.push(expectedActor || "missing");
        return route.fulfill({
          status: 409,
          json: {
            code: "session_changed",
            submitted: false,
            detail: "账号已变化，本次操作未提交。",
          },
        });
      }
      state.writes.push({
        path,
        actor: state.user.id,
        expectedActor,
        body: request.postDataJSON(),
      });
      if (path !== "/orders") return route.fulfill({ json: { saved: true } });
      return route.fulfill({
        status: 409,
        json: {
          code: "stall_reservation_limit",
          detail: "测试写入已被记录",
          order_ids: [],
        },
      });
    }
    state.unexpected.push(path);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected fixture request" },
    });
  });
  await page.addInitScript(
    ({ product, userId }) => {
      if (!localStorage.getItem(`yanhuo-cart-v2:user:${userId}`))
        localStorage.setItem(
          `yanhuo-cart-v2:user:${userId}`,
          JSON.stringify({ 9701: [{ product, quantity: 1 }] }),
        );
    },
    { product, userId: first.id },
  );
  await page.goto("/checkout/9701");
  await expect(
    page.getByRole("button", { name: "提交自取订单", exact: true }),
  ).toBeEnabled();
  await page.evaluate(async () => {
    const modulePath = "/src/lib/api.ts";
    (window as any).identityClient = (await import(modulePath)).api;
  });
  return { state, first, second };
}

test("focus identity verification prevents an old cart from being submitted under another account", async ({
  page,
}, info) => {
  const { state, second } = await fixture(page);
  state.user = second;
  state.holdIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.identityChecks.length).toBe(1);
  await page.getByRole("button", { name: "提交自取订单", exact: true }).click();
  // Keep auth/me in flight while the user attempts a mutation.
  await page.waitForTimeout(250);
  await info.attach("identity-write-observation", {
    body: JSON.stringify(state.writes, null, 2),
    contentType: "application/json",
  });
  expect(state.writes).toEqual([]);
  await state.identityChecks[0]!.fulfill({ json: second });
  await expect(page.getByText("同学 B", { exact: true })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => sessionStorage.getItem("yanhuo-checkout-9701-9701")),
    )
    .toBeNull();
  expect(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("yanhuo-cart-v2:user:9701")!)[9701][0]
          .quantity,
    ),
  ).toBe(1);
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("all mutating methods wait for the same identity check and reads remain available", async ({
  page,
}) => {
  const { state, first } = await fixture(page);
  state.holdIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.identityChecks.length).toBe(1);
  await page.evaluate(() => {
    const w = window as any;
    w.identityWrites = Promise.all(
      ["POST", "PATCH", "DELETE"].map((method) =>
        w.identityClient(`/probe/${method}`, { method, body: { method } }),
      ),
    );
  });
  expect(
    await page.evaluate(() =>
      (window as any)
        .identityClient("/stalls/9701")
        .then((value: any) => value.id),
    ),
  ).toBe(9701);
  expect(state.writes).toEqual([]);
  await state.identityChecks[0]!.fulfill({ json: first });
  expect(await page.evaluate(() => (window as any).identityWrites)).toEqual([
    { saved: true },
    { saved: true },
    { saved: true },
  ]);
  expect(state.writes.map((write) => write.path).sort()).toEqual([
    "/probe/DELETE",
    "/probe/PATCH",
    "/probe/POST",
  ]);
  expect(state.writes.every((write) => write.actor === first.id)).toBe(true);
  expect(
    state.writes.every((write) => write.expectedActor === String(first.id)),
  ).toBe(true);
});

test("a server account mismatch refreshes identity without replaying a write after an old identity response", async ({
  page,
}) => {
  const { state, first, second } = await fixture(page);
  state.holdIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.identityChecks.length).toBe(1);
  // This successful reply reflects the cookie at read time. The cookie now
  // belongs to B, so waiting for this old reply alone cannot protect the write.
  state.user = second;
  state.enforceActor = true;
  state.holdIdentity = false;
  await state.identityChecks[0]!.fulfill({ json: first });
  await page.getByRole("button", { name: "提交自取订单", exact: true }).click();
  await expect(page.getByText("同学 B", { exact: true })).toBeVisible();
  expect(state.rejectedActors).toEqual([String(first.id)]);
  expect(state.writes).toEqual([]);
  await expect
    .poll(() =>
      page.evaluate(() => sessionStorage.getItem("yanhuo-checkout-9701-9701")),
    )
    .toBeNull();
  expect(
    await page.evaluate(
      () =>
        JSON.parse(localStorage.getItem("yanhuo-cart-v2:user:9701")!)[9701][0]
          .quantity,
    ),
  ).toBe(1);
});

test("a failed identity check blocks writes and a later retry verifies again before sending", async ({
  page,
}) => {
  const { state, first } = await fixture(page);
  state.holdIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.identityChecks.length).toBe(1);
  await page.evaluate(() => {
    const w = window as any;
    w.failedIdentityWrite = w
      .identityClient("/probe/failed", { method: "POST", body: {} })
      .catch((error: any) => ({
        code: error.code,
        submitted: error.data.submitted,
      }));
  });
  await state.identityChecks[0]!.fulfill({
    status: 503,
    json: { detail: "Identity service unavailable" },
  });
  expect(
    await page.evaluate(() => (window as any).failedIdentityWrite),
  ).toEqual({ code: "identity_unverified", submitted: false });
  expect(state.writes).toEqual([]);
  await page.evaluate(() => {
    const w = window as any;
    w.retryIdentityWrites = Promise.all(
      [1, 2].map((caller) =>
        w.identityClient("/probe/retry", { method: "POST", body: { caller } }),
      ),
    );
  });
  await expect.poll(() => state.identityChecks.length).toBe(2);
  expect(state.writes).toEqual([]);
  await state.identityChecks[1]!.fulfill({ json: first });
  expect(
    await page.evaluate(() => (window as any).retryIdentityWrites),
  ).toEqual([{ saved: true }, { saved: true }]);
  expect(state.writes).toHaveLength(2);
});

test("cancelling one identity waiter does not cancel another operation or their shared check", async ({
  page,
}) => {
  const { state, first } = await fixture(page);
  state.holdIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.identityChecks.length).toBe(1);
  await page.evaluate(() => {
    const w = window as any;
    w.identityController = new AbortController();
    w.cancelledIdentityWrite = w
      .identityClient("/probe/cancelled", {
        method: "POST",
        body: {},
        signal: w.identityController.signal,
      })
      .catch((error: any) => ({
        code: error.code,
        submitted: error.data.submitted,
      }));
    w.remainingIdentityWrite = w.identityClient("/probe/remaining", {
      method: "PATCH",
      body: {},
    });
    w.identityController.abort();
  });
  expect(
    await page.evaluate(() => (window as any).cancelledIdentityWrite),
  ).toEqual({ code: "request_aborted", submitted: false });
  expect(state.writes).toEqual([]);
  await state.identityChecks[0]!.fulfill({ json: first });
  expect(
    await page.evaluate(() => (window as any).remainingIdentityWrite),
  ).toEqual({ saved: true });
  expect(state.writes.map((write) => write.path)).toEqual(["/probe/remaining"]);
});

test("an identity check started during CSRF waiting is rechecked before the write dispatches", async ({
  page,
}) => {
  const { state, second } = await fixture(page);
  state.holdCsrf = true;
  await page.evaluate(() => {
    const w = window as any;
    w.csrfIdentityWrite = w
      .identityClient("/probe/after-csrf", { method: "POST", body: {} })
      .catch((error: any) => ({
        code: error.code,
        submitted: error.data.submitted,
      }));
  });
  await expect.poll(() => state.csrfChecks.length).toBe(1);
  state.user = second;
  state.holdIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.identityChecks.length).toBe(1);
  await fulfillCsrf(state.csrfChecks[0]!);
  await page.waitForTimeout(100);
  expect(state.writes).toEqual([]);
  await state.identityChecks[0]!.fulfill({ json: second });
  expect(await page.evaluate(() => (window as any).csrfIdentityWrite)).toEqual({
    code: "request_aborted",
    submitted: false,
  });
  expect(state.writes).toEqual([]);
});

test("explicit logout can replace a failed identity check without leaving the old account active", async ({
  page,
}) => {
  const { state } = await fixture(page);
  state.holdIdentity = true;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.identityChecks.length).toBe(1);
  await state.identityChecks[0]!.fulfill({
    status: 503,
    json: { detail: "Identity service unavailable" },
  });
  const result = await page.evaluate(async () => {
    const modulePath = "/src/stores/session.ts";
    const session = (await import(modulePath)).useSession();
    await session.logout();
    return session.user;
  });
  expect(result).toBeNull();
  expect(state.writes.map((write) => write.path)).toEqual(["/auth/logout"]);
  expect(state.identityChecks).toHaveLength(1);
});
