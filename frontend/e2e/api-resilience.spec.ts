import { expect, test, type Route } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// No request in this suite reaches the business database or a payment provider.
async function harness(page: import("@playwright/test").Page) {
  await page.route("**/network-harness", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>Network fixture</title>",
    }),
  );
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({
      status: 500,
      json: { detail: "Unexpected fixture request" },
    }),
  );
  await page.goto("/network-harness");
  await page.evaluate(async () => {
    const modulePath = "/src/lib/api.ts";
    (window as any).client = (await import(modulePath)).api;
  });
}

test("a stalled request times out without retrying and a later request can recover", async ({
  page,
}) => {
  await harness(page);
  await page.clock.install();
  let calls = 0;
  let held: Route | undefined;
  await page.route("**/api/v1/probe", (route) => {
    calls++;
    held = route;
  });
  const first = page.evaluate(() =>
    (window as any)
      .client("/probe")
      .catch((error: any) => ({ status: error.status, code: error.code })),
  );
  await expect.poll(() => calls).toBe(1);
  await page.clock.fastForward(20_001);
  expect(await first).toEqual({ status: 0, code: "request_timeout" });
  expect(calls).toBe(1);
  await held?.abort().catch(() => {});
  await page.route("**/api/v1/probe", (route) =>
    route.fulfill({ json: { recovered: true } }),
  );
  expect(await page.evaluate(() => (window as any).client("/probe"))).toEqual({
    recovered: true,
  });
});

test("cancelling one CSRF waiter does not submit it or cancel another caller", async ({
  page,
}) => {
  await harness(page);
  let held: Route | undefined;
  let checks = 0;
  const writes: unknown[] = [];
  await page.route("**/api/v1/auth/csrf", (route) => {
    checks++;
    held = route;
  });
  await page.route("**/api/v1/write", (route) => {
    writes.push(route.request().postDataJSON());
    expect(route.request().headers()["x-csrftoken"]).toBe("fixture-csrf-token");
    return route.fulfill({ json: { saved: true } });
  });
  await page.evaluate(() => {
    const w = window as any;
    w.controller = new AbortController();
    w.first = w
      .client("/write", {
        method: "POST",
        body: { caller: 1 },
        signal: w.controller.signal,
      })
      .catch((error: any) => ({
        code: error.code,
        submitted: error.data.submitted,
      }));
    w.second = w.client("/write", { method: "POST", body: { caller: 2 } });
  });
  await expect.poll(() => checks).toBe(1);
  await page.evaluate(() => (window as any).controller.abort());
  expect(await page.evaluate(() => (window as any).first)).toEqual({
    code: "request_aborted",
    submitted: false,
  });
  expect(writes).toEqual([]);
  await fulfillCsrf(held!);
  expect(await page.evaluate(() => (window as any).second)).toEqual({
    saved: true,
  });
  expect(checks).toBe(1);
  expect(writes).toEqual([{ caller: 2 }]);
});

test("a stalled shared CSRF check expires and does not poison later writes", async ({
  page,
}) => {
  await harness(page);
  await page.clock.install();
  let checks = 0;
  let writes = 0;
  let held: Route | undefined;
  await page.route("**/api/v1/auth/csrf", (route) => {
    checks++;
    held = route;
  });
  await page.route("**/api/v1/write", (route) => {
    writes++;
    return route.fulfill({ json: { saved: true } });
  });
  const first = page.evaluate(() =>
    (window as any)
      .client("/write", { method: "POST", body: {} })
      .catch((error: any) => ({
        code: error.code,
        submitted: error.data.submitted,
      })),
  );
  await expect.poll(() => checks).toBe(1);
  await page.clock.fastForward(20_001);
  expect(await first).toEqual({ code: "request_timeout", submitted: false });
  expect(writes).toBe(0);
  await held?.abort().catch(() => {});
  await page.route("**/api/v1/auth/csrf", (route) => {
    checks++;
    return fulfillCsrf(route);
  });
  expect(
    await page.evaluate(() =>
      (window as any).client("/write", { method: "POST", body: {} }),
    ),
  ).toEqual({ saved: true });
  expect(checks).toBe(2);
  expect(writes).toBe(1);
});

for (const status of [200, 503]) {
  test(`CSRF response ${status} without a cookie never proceeds to a write`, async ({
    page,
  }) => {
    await harness(page);
    let writes = 0;
    await page.route("**/api/v1/auth/csrf", (route) =>
      route.fulfill({ status, json: {} }),
    );
    await page.route("**/api/v1/write", (route) => {
      writes++;
      return route.fulfill({ json: {} });
    });
    const result = await page.evaluate(() =>
      (window as any)
        .client("/write", { method: "POST", body: {} })
        .catch((error: any) => ({
          status: error.status,
          code: error.code,
          submitted: error.data.submitted,
        })),
    );
    expect(result).toEqual({
      status,
      code: "csrf_unavailable",
      submitted: false,
    });
    expect(writes).toBe(0);
  });
}

test("an invalid success body is not treated as a saved operation and is not retried", async ({
  page,
}) => {
  await harness(page);
  let writes = 0;
  await page.route("**/api/v1/auth/csrf", fulfillCsrf);
  await page.route("**/api/v1/write", (route) => {
    writes++;
    return route.fulfill({
      contentType: "text/html",
      body: "<h1>Proxy page</h1>",
    });
  });
  const result = await page.evaluate(() =>
    (window as any)
      .client("/write", { method: "POST", body: {} })
      .catch((error: any) => ({
        status: error.status,
        code: error.code,
        submitted: error.data.submitted,
      })),
  );
  expect(result).toEqual({
    status: 200,
    code: "invalid_response",
    submitted: true,
  });
  expect(writes).toBe(1);
});

test("a deadline covers an unfinished response body, not only its headers", async ({
  page,
}) => {
  await harness(page);
  await page.clock.install();
  await page.evaluate(() => {
    const original = window.fetch;
    window.fetch = async (input, init) => {
      if (String(input) !== "/api/v1/stream") return original(input, init);
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{"saved":'));
          init?.signal?.addEventListener("abort", () =>
            controller.error(new DOMException("Aborted", "AbortError")),
          );
        },
      });
      return new Response(stream, {
        headers: { "Content-Type": "application/json" },
      });
    };
    (window as any).result = (window as any)
      .client("/stream")
      .catch((error: any) => error.code);
  });
  await page.clock.fastForward(20_001);
  expect(await page.evaluate(() => (window as any).result)).toBe(
    "request_timeout",
  );
});

test("orders recover after timeout and never show an empty-order claim for a failed read", async ({
  page,
}, testInfo) => {
  const user = {
    id: 991,
    username: "network_fixture",
    display_name: "试用同学",
    is_merchant: false,
    is_staff: false,
  };
  let held: Route | undefined;
  let orders = 0;
  await page.route("**/api/v1/**", (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/config")
      return route.fulfill({
        json: {
          demo_mode: true,
          services_simulation_enabled: true,
          brand: "烟火地图",
          amap_key: "",
          areas: [],
          user,
        },
      });
    if (path === "/auth/me") return route.fulfill({ json: user });
    if (path === "/orders") {
      orders++;
      held = route;
      return;
    }
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected fixture request" },
    });
  });
  await page.clock.install();
  await page.goto("/orders");
  await expect.poll(() => orders).toBe(1);
  await expect(page.getByRole("button", { name: "刷新订单" })).toBeDisabled();
  await page.clock.fastForward(20_001);
  await expect(page.getByRole("alert")).toContainText("请求等待超时");
  await expect(
    page.getByRole("heading", { name: "第一份好味道，等你发现" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "刷新订单" })).toBeEnabled();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 390, height: 900 });
  await page.screenshot({
    path: testInfo.outputPath("orders-timeout-mobile.png"),
  });
  await held?.abort().catch(() => {});
  await page.route("**/api/v1/orders", (route) => route.fulfill({ json: [] }));
  await page.getByRole("button", { name: "重新加载" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "第一份好味道，等你发现" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("最近同步");
});
