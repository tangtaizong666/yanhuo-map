import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import {
  assertNoHorizontalOverflow,
  baseURL,
  createStudent,
  login,
  mutate,
} from "./helpers";

// This tour writes orders only in the explicitly disposable integration runner.
test.skip(
  process.env.E2E_ISOLATED !== "1",
  "Requires disposable database and media",
);
test.use({ isMobile: true, hasTouch: true });

async function capture(page: Page, info: TestInfo, name: string) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: info.outputPath(`${name}-top.png`), animations: 'disabled' });
  await page.screenshot({
    path: info.outputPath(`${name}-full.png`),
    fullPage: true,
    animations: 'disabled',
  });
  const metrics = await page.evaluate(() => {
    const visible = (element: Element) => {
      const rect = element.getBoundingClientRect(),
        style = getComputedStyle(element);
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== "hidden" &&
        style.display !== "none"
      );
    };
    const summary = (element: Element) => {
      const rect = element.getBoundingClientRect();
      return {
        tag: element.tagName,
        class: element.className,
        text: element.textContent?.trim().slice(0, 100),
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    };
    return {
      url: location.pathname,
      width: innerWidth,
      height: innerHeight,
      scrollHeight: document.documentElement.scrollHeight,
      headings: [...document.querySelectorAll("h1,h2")]
        .filter(visible)
        .map(summary),
      fixed: [...document.querySelectorAll("body *")]
        .filter(
          (element) =>
            visible(element) && getComputedStyle(element).position === "fixed",
        )
        .map(summary),
      actions: [...document.querySelectorAll("button,a,input,select,summary")]
        .filter(visible)
        .map(summary),
      firstOrder: document.querySelector(".merchant-order")
        ? summary(document.querySelector(".merchant-order")!)
        : null,
    };
  });
  await info.attach(name, {
    body: JSON.stringify(metrics, null, 2),
    contentType: "application/json",
  });
  await writeFile(
    info.outputPath(`${name}-metrics.json`),
    JSON.stringify(metrics, null, 2),
  );
  await assertNoHorizontalOverflow(page);
}

async function assertPrimaryActionVisible(
  page: Page,
  locator: ReturnType<Page["getByRole"]>,
  navigation = "商家底部导航",
) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(
    box!.y,
    "Primary action should not require scrolling past setup instructions",
  ).toBeGreaterThanOrEqual(0);
  expect(
    box!.y + box!.height,
    "Primary action should fit above the bottom navigation",
  ).toBeLessThanOrEqual(
    (await page
      .getByRole("navigation", { name: navigation, exact: true })
      .boundingBox())!.y,
  );
  expect(
    await locator.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return [rect.top + 3, rect.bottom - 3].every((y) =>
        element.contains(
          document.elementFromPoint(rect.left + rect.width / 2, y),
        ),
      );
    }),
    "Primary action is covered by a fixed surface",
  ).toBe(true);
}

for (const width of [360, 390])
  test(`mobile ${width}: student discovery to completed pickup and merchant daily routes`, async ({
    browser,
    page,
    context,
  }, info) => {
    test.setTimeout(150_000);
    await page.setViewportSize({ width, height: 844 });
    const credentials = await createStudent(context);
    const merchantContext = await browser.newContext({
      baseURL,
      viewport: { width, height: 844 },
      isMobile: true,
      hasTouch: true,
    });
    const merchant = await merchantContext.newPage();
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    merchant.on("pageerror", (error) => errors.push(error.message));
    try {
      await merchant.goto("/login?returnTo=/merchant");
      await expect(
        merchant.getByRole("textbox", { name: "账号", exact: true }),
      ).toBeVisible();
      await capture(merchant, info, "m-login");
      // Prepare only disposable fixture data before mounting the dashboard and
      // its receiving heartbeat. SQLite cannot promote concurrent read/write
      // transactions; preparation is not part of the user action under test.
      const preparationLogin = await mutate(merchantContext, "/auth/login", {
        username: "vendor",
        password: "demo12345",
      });
      expect(preparationLogin.ok(), await preparationLogin.text()).toBeTruthy();
      const stalls = await (
        await merchantContext.request.get(`${baseURL}/api/v1/merchant/stalls`)
      ).json();
      const eligibleStalls = stalls.filter(
        (value: any) =>
          value.transaction_enabled &&
          value.products.some(
            (product: any) =>
              product.stock > 0 && product.is_active && !product.sale_paused,
          ),
      );
      // A failing width must not leave a preceding order in the next width's queue.
      const stall = eligibleStalls[width === 360 ? 0 : 1];
      expect(stall).toBeTruthy();
      const product = stall.products.find(
        (value: any) =>
          value.stock > 0 && value.is_active && !value.sale_paused,
      );
      const prepared = await mutate(
        merchantContext,
        `/merchant/stalls/${stall.id}/status`,
        {
          status: "open",
          confirm_location: true,
          closes_at: new Date(Date.now() + 7200000).toISOString(),
        },
      );
      expect(prepared.ok(), await prepared.text()).toBeTruthy();
      expect(
        (await mutate(merchantContext, "/auth/logout", {})).ok(),
      ).toBeTruthy();
      await login(merchant, "vendor", "demo12345");
      await expect(merchant).toHaveURL((url) => url.pathname === "/merchant");
      await merchant.reload();
      await merchant
        .getByLabel("选择管理的摊位")
        .selectOption(String(stall.id));
      await expect(
        merchant.getByRole("group", { name: "订单阶段", exact: true }),
      ).toBeVisible();
      await capture(merchant, info, "m-dashboard");
      await expect(
        merchant
          .getByRole("navigation", { name: "商家底部导航" })
          .getByRole("link"),
      ).toHaveCount(3);
      for (const path of ["products", "more", "store", "orders"]) {
        await merchant.goto(`/merchant/${path}`);
        await expect(merchant.getByLabel("选择管理的摊位")).toBeVisible();
        await merchant
          .getByLabel("选择管理的摊位")
          .selectOption(String(stall.id));
        await capture(merchant, info, `m-${path}`);
      }
      await page.goto("/login");
      await login(page, credentials.username, credentials.password);
      await page.goto("/");
      await expect(
        page.getByRole("link", { name: new RegExp(stall.name) }).first(),
      ).toBeVisible();
      await capture(page, info, "s-home");
      await page.goto(`/stalls/${stall.id}`);
      await expect(
        page.getByRole("button", { name: `添加${product.name}`, exact: true }),
      ).toBeVisible();
      await capture(page, info, "s-stall");
      await page
        .getByRole("link", { name: `查看${product.name}详情`, exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: product.name, exact: true }),
      ).toBeVisible();
      await capture(page, info, "s-product");
      await page.getByRole("button", { name: /加入餐袋/ }).click();
      await page.goto("/cart");
      await expect(page.getByRole("link", { name: /去结算/ })).toBeVisible();
      await capture(page, info, "s-cart");
      await assertPrimaryActionVisible(
        page,
        page.getByRole("link", { name: /去结算/ }),
        "底部导航",
      );
      await page.getByRole("link", { name: /去结算/ }).click();
      const submit = page.getByRole("button", {
        name: "提交自取订单",
        exact: true,
      });
      await expect(submit).toBeEnabled();
      await capture(page, info, "s-checkout");
      await page.locator(".checkout-contact-card > summary").click();
      await page
        .getByPlaceholder("例如：餐具按需提供（每份口味请在上方分别填写）")
        .fill("手机全流程隔离验收，少辣");
      const createdPromise = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          /\/api\/v1\/orders$/.test(response.url()),
      );
      await submit.click();
      const created = await createdPromise;
      expect(created.status(), await created.text()).toBe(201);
      const order = await created.json();
      await expect(page).toHaveURL(new RegExp(`/orders/${order.id}$`));
      await expect(
        page.getByRole("button", { name: "提交自取订单", exact: true }),
      ).toHaveCount(0);
      await expect(page.locator(".cart-shortcut-count")).toHaveCount(0);
      await capture(page, info, "s-pending");
      await page.goto("/orders");
      await expect(
        page.locator(`.order-card[href="/orders/${order.id}"]`),
      ).toBeVisible();
      await capture(page, info, "s-orders");
      await page.goto(`/orders/${order.id}`);
      await merchant.goto("/merchant/orders");
      await merchant
        .getByLabel("选择管理的摊位")
        .selectOption(String(stall.id));
      const card = merchant
        .locator("article.merchant-order")
        .filter({ hasText: order.number });
      await expect(card).toBeVisible();
      await capture(merchant, info, "m-pending");
      await assertPrimaryActionVisible(
        merchant,
        card.getByRole("button", { name: "接单开始做", exact: true }),
      );
      await card
        .getByRole("button", { name: "接单开始做", exact: true })
        .click();
      await merchant
        .getByRole("group", { name: "订单阶段", exact: true })
        .getByRole("button", { name: /制作中/ })
        .click();
      await expect(card.locator(".merchant-order-status")).toHaveText("制作中");
      await capture(merchant, info, "m-preparing");
      await card.getByRole("button", { name: "做好了", exact: true }).click();
      await merchant
        .getByRole("group", { name: "订单阶段", exact: true })
        .getByRole("button", { name: /待取餐/ })
        .click();
      await expect(card.locator(".merchant-order-status")).toHaveText("待取餐");
      await capture(merchant, info, "m-ready-unpaid");
      await page.getByRole("button", { name: "刷新订单状态" }).click();
      await expect(page.locator(".pickup-code strong")).toHaveText(/^\d{8}$/);
      await capture(page, info, "s-ready");
      const code = await page.locator(".pickup-code strong").innerText();
      await card.getByRole("button", { name: /^收款 ¥/ }).click();
      await capture(merchant, info, "m-receipt-confirm");
      await merchant
        .getByRole("dialog", { name: "确认这笔线下收款" })
        .getByRole("button", { name: "确认收款", exact: true })
        .click();
      await expect(
        card.getByRole("button", { name: "核对取餐码", exact: true }),
      ).toBeVisible();
      await capture(merchant, info, "m-ready-paid");
      await card
        .getByRole("button", { name: "核对取餐码", exact: true })
        .click();
      const detail = merchant.getByRole("dialog", {
        name: "订单详情",
        exact: true,
      });
      await detail.getByPlaceholder("输入取餐码").fill(code);
      await detail
        .getByRole("button", { name: "核销并完成", exact: true })
        .click();
      await expect(card).toHaveCount(0);
      await page.getByRole("button", { name: "刷新订单状态" }).click();
      await expect(
        page.getByRole("heading", { name: "这一餐，刚刚好" }),
      ).toBeVisible();
      await capture(page, info, "s-completed");
      const finalOrder = await (
        await context.request.get(`${baseURL}/api/v1/orders/${order.id}`)
      ).json();
      expect(finalOrder.status).toBe("completed");
      expect(finalOrder.payment_status).toBe("paid");
      expect(errors).toEqual([]);
    } finally {
      await merchantContext.close();
    }
  });
