import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { baseURL, createStudent, mutate } from "./helpers";

test("the real session rejects an old account write even when its successful identity response arrives after a cookie switch", async ({
  page,
  context,
  browser,
}, info) => {
  test.skip(
    process.env.E2E_ISOLATED !== "1",
    "Requires the disposable browser/API integration database.",
  );
  const first = await createStudent(context);
  const second = await createStudent(context);
  const firstLogin = await mutate(context, "/auth/login", first);
  expect(firstLogin.ok(), await firstLogin.text()).toBeTruthy();
  const firstUser = await firstLogin.json();
  const merchant = await browser.newContext({ baseURL });
  let releaseIdentity = () => {};
  let stall: any;
  let product: any;
  try {
    const merchantLogin = await mutate(merchant, "/auth/login", {
      username: "vendor",
      password: "demo12345",
    });
    expect(merchantLogin.ok(), await merchantLogin.text()).toBeTruthy();
    stall = (
      await (
        await merchant.request.get(`${baseURL}/api/v1/merchant/stalls`)
      ).json()
    ).find((value: any) => value.transaction_enabled);
    expect(stall).toBeTruthy();
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
    const created = await mutate(
      merchant,
      `/merchant/stalls/${stall.id}/products`,
      {
        name: `身份边界验证-${Date.now()}`,
        price_cents: 1000,
        stock: 3,
        image:
          stall.products.find((value: any) => value.image)?.image ||
          stall.image,
        category: "测试餐点",
        description: "隔离 Cookie 身份边界验证",
      },
    );
    expect(created.status(), await created.text()).toBe(201);
    product = await created.json();
    await page.goto(`/stalls/${stall.id}`);
    await page
      .getByRole("button", { name: `添加${product.name}`, exact: true })
      .click();
    await page.getByRole("link", { name: "去结算", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "提交自取订单", exact: true }),
    ).toBeEnabled();
    await page.locator(".checkout-contact-card > summary").click();
    await page
      .getByPlaceholder("例如：餐具按需提供（每份口味请在上方分别填写）")
      .fill("保留 A 的结算草稿");

    let capturedIdentity!: (value: any) => void;
    const captured = new Promise<any>((resolve) => {
      capturedIdentity = resolve;
    });
    const gate = new Promise<void>((resolve) => {
      releaseIdentity = resolve;
    });
    await page.route(
      "**/api/v1/auth/me",
      async (route) => {
        const response = await route.fetch();
        const body = await response.body();
        capturedIdentity(JSON.parse(body.toString()));
        await gate;
        await route.fulfill({ response, body });
      },
      { times: 1 },
    );
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    expect((await captured).id).toBe(firstUser.id);
    const secondLogin = await mutate(context, "/auth/login", second);
    expect(secondLogin.ok(), await secondLogin.text()).toBeTruthy();
    const secondUser = await secondLogin.json();
    const oldIdentityResponse = page.waitForResponse((response) =>
      response.url().endsWith("/api/v1/auth/me"),
    );
    releaseIdentity();
    expect((await (await oldIdentityResponse).json()).id).toBe(firstUser.id);
    await expect(page.locator(".header-user")).toContainText(
      firstUser.display_name,
    );

    const writeResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/orders") &&
        response.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "提交自取订单", exact: true })
      .click();
    const rejected = await writeResponse;
    const rejection = await rejected.json();
    expect(rejected.request().headers()["x-yanhuo-actor"]).toBe(
      String(firstUser.id),
    );
    expect(rejected.status()).toBe(409);
    expect(rejection).toMatchObject({
      code: "session_changed",
      submitted: false,
    });
    await expect(page.locator(".header-user")).toContainText(
      secondUser.display_name,
    );
    await expect
      .poll(() =>
        page.evaluate(async () => {
          const modulePath = "/src/stores/session.ts";
          return (await import(modulePath)).useSession().user?.id;
        }),
      )
      .toBe(secondUser.id);
    expect(
      await (await context.request.get(`${baseURL}/api/v1/orders`)).json(),
    ).toEqual([]);
    const originalCart = await page.evaluate(
      (userId) =>
        JSON.parse(
          localStorage.getItem(`yanhuo-cart-v2:user:${userId}`) || "{}",
        ),
      firstUser.id,
    );
    expect(originalCart[stall.id]).toHaveLength(1);
    expect(originalCart[stall.id][0].quantity).toBe(1);
    await expect
      .poll(() =>
        page.evaluate(
          (key) => sessionStorage.getItem(key),
          `yanhuo-checkout-${firstUser.id}-${stall.id}`,
        ),
      )
      .toBeNull();
    expect((await mutate(context, "/auth/login", first)).ok()).toBeTruthy();
    expect(
      await (await context.request.get(`${baseURL}/api/v1/orders`)).json(),
    ).toEqual([]);
    const updatedStall = (
      await (
        await merchant.request.get(`${baseURL}/api/v1/merchant/stalls`)
      ).json()
    ).find((value: any) => value.id === stall.id);
    expect(
      updatedStall.products.find((value: any) => value.id === product.id).stock,
    ).toBe(3);
    const evidence = JSON.stringify(
      {
        status: rejected.status(),
        rejection,
        expected_actor: firstUser.id,
        cookie_actor: secondUser.id,
        first_account_order_count: 0,
        second_account_order_count: 0,
        original_cart_quantity: 1,
        stock_before: 3,
        stock_after: 3,
      },
      null,
      2,
    );
    const evidencePath = info.outputPath(
      "real-cookie-account-precondition.json",
    );
    await mkdir(dirname(evidencePath), { recursive: true });
    await writeFile(evidencePath, evidence, "utf-8");
    await info.attach("real-cookie-account-precondition", {
      path: evidencePath,
      contentType: "application/json",
    });
  } finally {
    releaseIdentity();
    await page.unroute("**/api/v1/auth/me");
    if (product)
      expect(
        (
          await mutate(
            merchant,
            `/merchant/products/${product.id}`,
            { is_active: false },
            "PATCH",
          )
        ).ok(),
      ).toBeTruthy();
    if (stall)
      expect(
        (
          await mutate(merchant, `/merchant/stalls/${stall.id}/status`, {
            status: stall.session_status || "closed",
            confirm_location: true,
            closes_at:
              stall.closes_at && Date.parse(stall.closes_at) > Date.now()
                ? stall.closes_at
                : null,
          })
        ).ok(),
      ).toBeTruthy();
    await merchant.close();
  }
});
