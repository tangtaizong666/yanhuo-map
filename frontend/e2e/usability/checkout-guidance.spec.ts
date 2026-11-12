import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../helpers";

const food = {
  id: 481,
  name: "鸡蛋煎饼",
  description: "现摊鸡蛋饼",
  image: "/images/food-jianbing.jpg",
  price_cents: 1600,
  availability: "available",
  max_order_quantity: 10,
  sale_paused: false,
  is_active: true,
  taste_options: [{ name: "辣度", choices: ["微辣"] }],
};
async function fixture(
  page: Page,
  rows = [{ product: food, quantity: 1, portions: [] as any[] }],
) {
  const user = {
    id: 481,
    username: "guidance",
    is_merchant: false,
    is_staff: false,
  };
  const stall: any = {
    id: 481,
    name: "南门煎饼摊",
    products: rows.map((row) => structuredClone(row.product)),
    status: "open",
    can_order: true,
    transaction_enabled: true,
    address: "校园南门橙色棚",
    area_name: "校园南门",
    prep_minutes: 10,
    reviews: [],
    review_count: 0,
    receiving_status: "recent",
    receiving_valid_for_seconds: 30,
    order_unavailable_reason: "",
    wechat_payment: {
      available: true,
      supported: true,
      mode: "live",
      channels: ["wechat"],
    },
    delivery: {
      enabled: true,
      available: true,
      mode: "live",
      reason: "",
      min_order_cents: 3000,
      fee_cents: 200,
      starts_at: "00:00",
      ends_at: "23:59",
      eta_min_minutes: 15,
      eta_max_minutes: 30,
      points: [{ id: 1, name: "南门交接点", address: "南门入口" }],
    },
  };
  const state = {
    stall,
    writes: [] as any[],
    unexpected: [] as string[],
    behavior: "hold" as "hold" | "price",
    release: () => {},
  };
  await page.addInitScript((items) => {
    const key = "yanhuo-cart-v2:user:481";
    if (!localStorage.getItem(key))
      localStorage.setItem(key, JSON.stringify({ 481: items }));
  }, rows);
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config")
      return send({ user, areas: [], demo_mode: false, amap_key: "" });
    if (path === "/auth/me") return send(user);
    if (path === "/events") return send({});
    if (path === "/orders/active-summary")
      return send({
        user_id: user.id,
        orders: [],
        count: 0,
        status_counts: {},
      });
    if (path === "/stalls/481") return send(stall);
    if (path === "/orders" && request.method() === "POST") {
      state.writes.push(request.postDataJSON());
      if (state.behavior === "price") {
        stall.products[0].price_cents = 1700;
        return route.fulfill({
          status: 409,
          json: { code: "price_changed", detail: "价格更新，请重新确认。" },
        });
      }
      await new Promise<void>((resolve) => {
        state.release = resolve;
      });
      return route.abort("failed");
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  return state;
}
const guidance = (page: Page) => page.locator(".checkout-guidance");
const modify = (page: Page) =>
  guidance(page).getByRole("button", { name: "去修改", exact: true });
const submit = (page: Page) => page.locator(".checkout-submit");

test("stall admission precedes item and taste problems; correction advances to the next problem without posting", async ({
  page,
}) => {
  const second = { ...food, id: 482, name: "葱香煎饼" };
  const state = await fixture(page, [
    { product: food, quantity: 1, portions: [] },
    {
      product: second,
      quantity: 1,
      portions: [{ options: { 辣度: "特辣" }, note: "" }],
    },
  ]);
  state.stall.can_order = false;
  state.stall.order_unavailable_reason = "位置已过期，请确认商家仍在此处。";
  state.stall.products[0].sale_paused = true;
  await page.goto("/checkout/481");
  await expect(guidance(page)).toContainText(
    state.stall.order_unavailable_reason,
  );
  await expect(
    guidance(page).getByRole("link", { name: "查看摊位" }),
  ).toHaveAttribute("href", "/stalls/481");
  await expect(modify(page)).toHaveCount(0);
  await expect(submit(page)).toBeDisabled();
  state.stall.can_order = true;
  await page.reload();
  await expect(guidance(page)).toContainText("暂停了这道餐点");
  await expect(modify(page)).toHaveAttribute("type", "button");
  await modify(page).click();
  const remove = page.getByRole("button", {
    name: "减少鸡蛋煎饼",
    exact: true,
  });
  await expect(remove).toBeFocused();
  await remove.click();
  await expect(guidance(page)).toContainText("葱香煎饼的口味选项已变更");
  await modify(page).click();
  const dialog = page.getByRole("dialog", {
    name: "葱香煎饼每份口味与备注",
    exact: true,
  });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "移除原选" })).toBeFocused();
  await dialog.getByRole("button", { name: "移除原选" }).click();
  await dialog.getByRole("button", { name: "保存口味与备注" }).click();
  await expect(guidance(page)).toHaveCount(0);
  await expect(submit(page)).toBeEnabled();
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("the order count limit precedes invalid tastes", async ({ page }) => {
  const state = await fixture(page, [
    {
      product: food,
      quantity: 11,
      portions: [{ options: { 辣度: "特辣" }, note: "" }],
    },
  ]);
  await page.goto("/checkout/481");
  await expect(guidance(page)).toContainText("每单合计最多 10 份");
  await modify(page).click();
  await expect(
    page.getByRole("button", { name: "减少鸡蛋煎饼", exact: true }),
  ).toBeFocused();
  await page.getByRole("button", { name: "减少鸡蛋煎饼", exact: true }).click();
  await expect(guidance(page)).toContainText("口味选项已变更");
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("delivery correction follows minimum, point, recipient and phone order and restores submission", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/checkout/481");
  await page.getByRole("button", { name: /商家配送/ }).click();
  await expect(guidance(page)).toContainText("餐费还差 ¥14");
  await modify(page).click();
  const increase = page.getByRole("button", {
    name: "增加鸡蛋煎饼",
    exact: true,
  });
  await expect(increase).toBeFocused();
  await increase.click();
  await expect(guidance(page)).toContainText("请选择校园交接点");
  await modify(page).click();
  const point = page.getByLabel("校园交接点");
  await expect(point).toBeFocused();
  await point.selectOption("1");
  await expect(guidance(page)).toContainText("请填写收餐人称呼");
  await modify(page).click();
  const recipient = page.getByPlaceholder("方便交接时称呼你");
  await expect(recipient).toBeFocused();
  await recipient.fill("小林");
  await expect(guidance(page)).toContainText("有效的配送联系号码");
  await modify(page).click();
  const phone = page.getByPlaceholder("请填写可联系的手机号码");
  await expect(phone).toBeFocused();
  await phone.fill("invalid");
  await expect(submit(page)).toBeDisabled();
  await phone.fill("13800000000");
  await expect(guidance(page)).toHaveCount(0);
  await expect(submit(page)).toBeEnabled();
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("unavailable delivery precedes its minimum and missing contact fields", async ({
  page,
}) => {
  const state = await fixture(page);
  Object.assign(state.stall.delivery, {
    available: false,
    mode: "simulation",
    reason: "商家目前忙不过来，暂时停止配送。",
  });
  await page.goto("/checkout/481");
  await page.getByRole("button", { name: /商家配送/ }).click();
  await expect(guidance(page)).toContainText("暂时停止配送");
  await modify(page).click();
  const pickup = page.getByRole("button", { name: /到摊自取.*免配送费/ });
  await expect(pickup).toBeFocused();
  await pickup.click();
  await expect(guidance(page)).toHaveCount(0);
  await expect(submit(page)).toBeEnabled();
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("pending and unknown submissions never offer correction or a normal second submission", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/checkout/481");
  await submit(page).click();
  await expect.poll(() => state.writes.length).toBe(1);
  try {
    await expect(submit(page)).toBeDisabled();
    await expect(submit(page)).toHaveText("正在提交…");
    await expect(modify(page)).toHaveCount(0);
  } finally {
    state.release();
  }
  await expect(
    page.getByRole("heading", { name: "上一笔提交结果待确认", exact: true }),
  ).toBeVisible();
  await expect(submit(page)).toHaveCount(0);
  await expect(modify(page)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "确认原订单结果", exact: true }),
  ).toBeEnabled();
  expect(state.writes).toHaveLength(1);
  expect(state.unexpected).toEqual([]);
});

test("a confirmed price change keeps the explicit new-price submission action", async ({
  page,
}) => {
  const state = await fixture(page);
  state.behavior = "price";
  await page.goto("/checkout/481");
  await submit(page).click();
  await expect(submit(page)).toHaveText("确认新价格并提交");
  await expect(submit(page)).toBeEnabled();
  await expect(guidance(page)).toHaveCount(0);
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].items[0].expected_price_cents).toBe(1600);
  expect(state.unexpected).toEqual([]);
});

test("a long server admission reason grows the mobile bar without covering the last explanation", async ({
  page,
}, info) => {
  const state = await fixture(page);
  state.stall.can_order = false;
  state.stall.order_unavailable_reason =
    "商家最近确认的位置已经超过有效期，目前暂停接收新的线上订单。请先联系商家确认实际出摊位置及营业安排，再决定是否前往；已提交的订单仍可在订单页继续查看和处理。";
  await page.setViewportSize({ width: 360, height: 844 });
  await page.goto("/checkout/481");
  await expect(guidance(page)).toContainText(
    state.stall.order_unavailable_reason,
  );
  await page.evaluate(() =>
    window.scrollTo(0, document.documentElement.scrollHeight),
  );
  await expect
    .poll(async () => {
      const copy = (await page.locator(".summary-note").boundingBox())!;
      const bar = (await page.locator(".checkout-action-bar").boundingBox())!;
      return copy.y + copy.height - bar.y;
    })
    .toBeLessThanOrEqual(0);
  await assertNoHorizontalOverflow(page);
  await page.screenshot({
    path: info.outputPath("checkout-guidance-long-reason-360.png"),
    animations: "disabled",
  });
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

for (const width of [360, 390, 768, 1440]) {
  test(`guidance keeps the correction target and final copy clear of the action bar at ${width}px`, async ({
    page,
  }, info) => {
    const state = await fixture(page, [
      { product: food, quantity: 2, portions: [] },
    ]);
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/checkout/481");
    await page.getByRole("button", { name: /商家配送/ }).click();
    await page.getByLabel("校园交接点").selectOption("1");
    await modify(page).click();
    const target = page.getByPlaceholder("方便交接时称呼你");
    await expect(target).toBeFocused();
    await assertNoHorizontalOverflow(page);
    const targetBox = (await target.boundingBox())!,
      barBox = (await page.locator(".checkout-action-bar").boundingBox())!;
    if (width <= 800) {
      expect(targetBox.y).toBeGreaterThan(70);
      expect(targetBox.y + targetBox.height).toBeLessThanOrEqual(barBox.y);
    }
    expect((await modify(page).boundingBox())!.height).toBeGreaterThanOrEqual(
      44,
    );
    await page.screenshot({
      path: info.outputPath(`checkout-guidance-${width}.png`),
      animations: "disabled",
    });
    await page.evaluate(() =>
      window.scrollTo(0, document.documentElement.scrollHeight),
    );
    if (width <= 800) {
      await expect
        .poll(async () => {
          const copy = (await page.locator(".summary-note").boundingBox())!;
          const bar = (await page
            .locator(".checkout-action-bar")
            .boundingBox())!;
          return copy.y + copy.height - bar.y;
        })
        .toBeLessThanOrEqual(0);
    }
    await page.screenshot({
      path: info.outputPath(`checkout-guidance-bottom-${width}.png`),
      animations: "disabled",
    });
    expect(state.writes).toEqual([]);
    expect(state.unexpected).toEqual([]);
  });
}
