import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Isolated API fixtures only. These tests never write to the running demo database.
async function fixture(
  page: Page,
  options: {
    path?: string;
    state?: string;
    accepting?: boolean;
    order?: string;
    loseAction?: boolean;
    secondStall?: boolean;
  } = {},
) {
  let user: any = {
    id: 979,
    username: "daily_fixture",
    display_name: "南门小厨",
    is_merchant: true,
    is_staff: false,
  };
  const stall: any = {
    id: 979,
    name: "南门小厨",
    description: "",
    contact_phone: "",
    category: "小吃",
    area_name: "校园南门",
    image: "/images/food-cold-noodles.jpg",
    address: "南门入口橙色棚",
    latitude: 30,
    longitude: 120,
    last_confirmed_at: new Date().toISOString(),
    closes_at: null,
    status: options.state || "open",
    session_status:
      options.state === "stale" ? "open" : options.state || "open",
    accepting_orders: options.accepting !== false,
    transaction_enabled: true,
    prep_minutes: 12,
    rating: null,
    review_count: 0,
    arrival_note: "",
    products: [
      {
        id: 980,
        name: "招牌煎饼",
        description: "",
        image: "/images/food-cold-noodles.jpg",
        category: "小吃",
        price_cents: 800,
        stock: 10,
        is_active: true,
        taste_options: [{ name: "辣度", choices: ["不辣", "微辣"] }],
      },
    ],
    reviews: [],
  };
  const order: any = {
    id: "daily-order",
    number: "DAILY001",
    stall_id: stall.id,
    stall_name: stall.name,
    mode: "live",
    status: options.order || "pending",
    fulfillment_type: "pickup",
    payment_method: "offline",
    payment_status: "unpaid",
    payment_review_required: false,
    cancel_requested: false,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 300000).toISOString(),
    pickup_address: stall.address,
    total_cents: 1600,
    items_total_cents: 1600,
    delivery_fee_cents: 0,
    note: "",
    items: [
      {
        product_id: 980,
        name: "招牌煎饼",
        image: stall.image,
        unit_price_cents: 800,
        quantity: 2,
        portions: [
          { options: { 辣度: "不辣" }, note: "不要香菜" },
          { options: { 辣度: "微辣" }, note: "" },
        ],
      },
    ],
    estimated_ready_at:
      options.order === "preparing"
        ? new Date(Date.now() + 600000).toISOString()
        : null,
    prep_delay_reason: "",
  };
  const writes: { path: string; body: any }[] = [],
    unexpected: string[] = [];
  const secondOrder = {
    ...order,
    id: "north-order",
    stall_id: 978,
    stall_name: "北门热食",
  };
  const seen = new Set<string>();
  let lose = !!options.loseAction,
    csrfFailure = false,
    csrfStatus = 503,
    actionResponse: any = undefined,
    holdCsrf = false,
    release: (() => void) | undefined;
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (path === "/config")
      return send({
        brand: "烟火地图",
        demo_mode: true,
        user,
        areas: [],
        stale_minutes: 60,
        amap_key: "",
        amap_proxy: "",
      });
    if (path === "/auth/me") return send(user);
    if (path === "/auth/csrf") {
      if (holdCsrf)
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (csrfFailure)
        return route.fulfill({
          status: csrfStatus,
          json: { detail: "测试安全校验失败" },
        });
      return fulfillCsrf(route);
    }
    if (path === "/merchant/stalls")
      return send(
        options.secondStall
          ? [stall, { ...stall, id: 978, name: "北门热食" }]
          : [stall],
      );
    if (path === "/merchant/stalls/979/services")
      return send({
        mode: "live",
        online_payment_enabled: false,
        delivery_enabled: false,
        delivery: {
          available: false,
          approved: false,
          reason: "尚未开通",
          available_points: [],
          point_ids: [],
        },
      });
    if (path === "/merchant/orders")
      return send(
        options.order === "none"
          ? []
          : [
              new URL(req.url()).searchParams.get("stall") === "978"
                ? secondOrder
                : order,
            ],
      );
    if (path === "/merchant/metrics")
      return send({
        mode: "live",
        today: { revenue_cents: 0, orders_created: 1, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    if (
      path === "/merchant/stalls/979/profile" ||
      path === "/merchant/stalls/979/status"
    ) {
      const body = req.postDataJSON();
      writes.push({ path, body });
      Object.assign(stall, body);
      if (body.status) stall.session_status = body.status;
      if (body.confirm_location)
        stall.last_confirmed_at = new Date().toISOString();
      return send(stall);
    }
    if (path === "/merchant/products/980") {
      const body = req.postDataJSON();
      writes.push({ path, body });
      Object.assign(stall.products[0], body);
      return send(stall.products[0]);
    }
    if (path === "/merchant/stalls/979/products") {
      const body = req.postDataJSON();
      writes.push({ path, body });
      return send({ id: 981, ...body });
    }
    if (path === "/merchant/orders/daily-order/action") {
      const body = req.postDataJSON();
      writes.push({ path, body });
      if (!seen.has(body.idempotency_key)) {
        seen.add(body.idempotency_key);
        if (body.action === "accept") order.status = "preparing";
        order.estimated_ready_at = new Date(
          Date.now() + body.prep_minutes * 60000,
        ).toISOString();
        order.prep_updated_at = new Date().toISOString();
        order.prep_delay_reason = body.reason || "";
      }
      if (lose) {
        lose = false;
        return route.abort("failed");
      }
      if (actionResponse !== undefined) return send(actionResponse);
      return send(order);
    }
    unexpected.push(`${req.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  await page.goto(options.path || "/merchant");
  await expect(
    page
      .getByRole("heading", { name: /经营首页|订单处理|商品管理|店铺设置/ })
      .first(),
  ).toBeVisible();
  return {
    stall,
    order,
    secondOrder,
    writes,
    unexpected,
    setCsrfFailure: (value: boolean, status = 503) => {
      csrfFailure = value;
      csrfStatus = status;
    },
    setActionResponse: (value: any) => {
      actionResponse = value;
    },
    holdCsrf: () => {
      holdCsrf = true;
    },
    releaseCsrf: () => release?.(),
    csrfWaiting: () => !!release,
    switchUser: () => {
      user = { ...user, id: 978, display_name: "另一个账号" };
    },
  };
}

test("open workbench prioritizes pending orders and idle never asks to reopen", async ({
  page,
}, info) => {
  const data = await fixture(page);
  const operations = page.getByRole("region", { name: "今天怎样营业" });
  await expect(
    operations.getByRole("link", { name: /处理待接单 1/ }),
  ).toBeVisible();
  await expect(
    operations.getByRole("button", { name: /开始出摊/ }),
  ).toHaveCount(0);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    if (width === 390 || width === 1440)
      await page.screenshot({
        path: info.outputPath(`merchant-daily-${width}.png`),
        fullPage: true,
      });
  }
  data.order.status = "completed";
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await expect(operations.getByText("新订单到达后会在这里显示")).toBeVisible();
  await expect(
    operations.getByRole("button", { name: /开始出摊/ }),
  ).toHaveCount(0);
  expect(data.unexpected).toEqual([]);
});

test("changing merchant during csrf wait aborts old preparation action", async ({
  page,
}) => {
  const data = await fixture(page, {
    path: "/merchant/orders",
    order: "preparing",
  });
  data.holdCsrf();
  await page.getByRole("button", { name: "还要等一会，更新预估" }).click();
  const dialog = page.getByRole("dialog", { name: "更新预计出餐时间" });
  await dialog.getByLabel("从现在起还需（分钟）").fill("20");
  await dialog.getByLabel("处理说明").fill("需要重新制作一份");
  await dialog.getByRole("button", { name: "更新并告知顾客" }).click();
  await expect.poll(data.csrfWaiting).toBe(true);
  data.switchUser();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(dialog).not.toBeVisible();
  data.releaseCsrf();
  await expect(
    page.getByRole("button", { name: "还要等一会，更新预估" }),
  ).toBeVisible();
  const saved = await page.evaluate(() => ({
    old: sessionStorage.getItem("merchant-prep:979:979"),
    current: sessionStorage.getItem("merchant-prep:978:979"),
  }));
  expect(saved.old).toContain("需要重新制作一份");
  expect(saved.current).toBeNull();
  expect(data.writes).toHaveLength(0);
  expect(data.unexpected).toEqual([]);
});

test("usual hours are an optional profile plan and security is in more settings", async ({
  page,
}) => {
  const data = await fixture(page, { path: "/merchant/store", order: "none" });
  await page.getByText("店铺资料", { exact: false }).first().click();
  await page
    .getByLabel("通常出摊时段（选填）")
    .fill("通常周一至周五 17:00–21:00");
  await page.getByRole("button", { name: "保存店铺信息" }).click();
  await expect.poll(() => data.writes.length).toBe(1);
  expect(data.writes[0]!.body).toEqual({
    usual_hours: "通常周一至周五 17:00–21:00",
  });
  await page.getByText("更多设置", { exact: false }).last().click();
  await expect(
    page.getByRole("link", { name: "管理账号恢复码" }),
  ).toHaveAttribute("href", "/account-security");
  expect(data.unexpected).toEqual([]);
});

test("closed pause stale and accepting each have an honest primary action", async ({
  page,
}) => {
  const data = await fixture(page, { state: "closed", order: "preparing" });
  const operations = page.getByRole("region", { name: "今天怎样营业" });
  await expect(operations.getByText("还有 1 单未完成")).toBeVisible();
  await operations.getByRole("button", { name: "就在这里，开始出摊" }).click();
  await expect.poll(() => data.writes.length).toBe(1);
  expect(data.writes[0]!.body).toEqual({
    status: "open",
    confirm_location: true,
  });
  await operations.getByRole("button", { name: "暂时离开摊位" }).click();
  await operations.getByRole("button", { name: "回到摊位，恢复出摊" }).click();
  await expect.poll(() => data.writes.length).toBe(3);
  expect(data.writes[2]!.body).toEqual({
    status: "open",
    confirm_location: false,
  });
  data.stall.status = "stale";
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await operations
    .getByRole("button", { name: "核对过了，我仍在这里" })
    .click();
  await expect.poll(() => data.writes.length).toBe(4);
  expect(data.writes[3]!.body).toEqual({
    status: "open",
    confirm_location: true,
  });
  await operations.getByRole("button", { name: "忙不过来，暂停接单" }).click();
  await expect(
    operations.getByRole("button", { name: "恢复线上接单" }),
  ).toBeVisible();
  await operations.getByRole("button", { name: "恢复线上接单" }).click();
  await expect.poll(() => data.writes.length).toBe(6);
  expect(data.writes.slice(-2).map((write) => write.body)).toEqual([
    { accepting_orders: false },
    { accepting_orders: true },
  ]);
  await operations.getByRole("button", { name: "今日收摊" }).click();
  await expect(operations.getByText("还有 1 单未完成")).toBeVisible();
  data.stall.status = "open";
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await expect(operations.getByText("还有 1 单未完成")).toHaveCount(0);
  expect(data.unexpected).toEqual([]);
});

test("product taste settings are optional preserve values and reject duplicates", async ({
  page,
}, info) => {
  const data = await fixture(page, {
    path: "/merchant/products",
    order: "none",
  });
  await page.getByRole("button", { name: /^编辑商品：/ }).click();
  const modal = page.getByRole("dialog", { name: "编辑商品" });
  await modal.getByText("口味选择（选填、免费）", { exact: false }).click();
  await expect(modal.getByLabel("第 1 组名称")).toHaveValue("辣度");
  await modal.getByLabel("第 1 组选项").fill("不辣、微辣、特辣");
  await modal.getByRole("button", { name: /添加一组口味/ }).click();
  await modal.getByLabel("第 2 组名称").fill("辣度");
  await modal.getByLabel("第 2 组选项").fill("不加");
  await modal.getByRole("button", { name: "保存修改" }).click();
  await expect(modal.getByRole("alert")).toContainText("不能重复");
  expect(data.writes).toHaveLength(0);
  await modal.getByLabel("第 2 组名称").fill("香菜");
  await modal.getByLabel("第 2 组选项").fill("正常、不加");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    if (width === 390)
      await page.screenshot({
        path: info.outputPath("merchant-taste-390.png"),
      });
  }
  await modal.getByRole("button", { name: "保存修改" }).click();
  await expect(modal).not.toBeVisible();
  expect(data.writes[0]!.body).toEqual({
    taste_options: [
      { name: "辣度", choices: ["不辣", "微辣", "特辣"] },
      { name: "香菜", choices: ["正常", "不加"] },
    ],
  });
  expect(data.unexpected).toEqual([]);
});

test("accept uses shop default estimate and each portion is visible", async ({
  page,
}, info) => {
  const data = await fixture(page, { path: "/merchant/orders" });
  await expect(
    page.getByText("第 1 份 · 辣度：不辣 · 不要香菜", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("预计约 12 分钟出餐", { exact: false }),
  ).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    if (width === 390)
      await page.screenshot({
        path: info.outputPath("merchant-portions-390.png"),
        fullPage: true,
      });
  }
  await page.getByRole("button", { name: "确认接单", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "还要等一会，更新预估" }),
  ).toBeVisible();
  expect(data.writes[0]!.body).toMatchObject({
    action: "accept",
    prep_minutes: 12,
  });
  expect(data.writes[0]!.body.idempotency_key.length).toBeGreaterThan(8);
  await page.getByRole("button", { name: "查看订单 DAILY001 详情" }).click();
  await expect(
    page
      .getByRole("dialog")
      .getByText("第 1 份 · 辣度：不辣 · 不要香菜", { exact: true }),
  ).toBeVisible();
  expect(data.unexpected).toEqual([]);
});

test("unknown accept survives page reload and replays original estimate once", async ({
  page,
}) => {
  const data = await fixture(page, {
    path: "/merchant/orders",
    loseAction: true,
  });
  await page.getByText("预计约 12 分钟出餐", { exact: false }).click();
  await page.getByLabel("接单后约需（分钟）").fill("18");
  await page.getByRole("button", { name: "确认接单", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "确认原操作结果" }),
  ).toBeVisible();
  const estimate = data.order.estimated_ready_at;
  await page.reload();
  await page.getByRole("button", { name: "确认原操作结果" }).click();
  await expect(
    page.getByRole("button", { name: "确认原操作结果" }),
  ).toHaveCount(0);
  expect(data.writes).toHaveLength(2);
  expect(data.writes[1]!.body).toEqual(data.writes[0]!.body);
  expect(data.order.estimated_ready_at).toBe(estimate);
  expect(data.unexpected).toEqual([]);
});

test("preparation revision preserves reason and key through unknown response and csrf failure", async ({
  page,
}, info) => {
  const data = await fixture(page, {
    path: "/merchant/orders",
    order: "preparing",
    loseAction: true,
  });
  await page.getByRole("button", { name: "还要等一会，更新预估" }).click();
  const dialog = page.getByRole("dialog", { name: "更新预计出餐时间" });
  await dialog.getByLabel("从现在起还需（分钟）").fill("15");
  await dialog.getByLabel("处理说明").fill("前面几单正在制作，还需要约一刻钟");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    if (width === 390)
      await page.screenshot({
        path: info.outputPath("merchant-delay-390.png"),
      });
  }
  await dialog.getByRole("button", { name: "更新并告知顾客" }).click();
  await expect.poll(() => data.writes.length).toBe(1);
  await expect(dialog.getByRole("alert")).toContainText("连接不上");
  await expect(dialog.getByLabel("从现在起还需（分钟）")).toBeDisabled();
  await expect(dialog.getByLabel("处理说明")).toBeDisabled();
  await page.context().clearCookies();
  data.setCsrfFailure(true);
  await dialog.getByRole("button", { name: "确认原操作结果" }).click();
  await expect(dialog.getByRole("alert")).toContainText("安全校验");
  const estimate = data.order.estimated_ready_at;
  data.setCsrfFailure(false);
  await dialog.getByRole("button", { name: "确认原操作结果" }).click();
  await expect(dialog).not.toBeVisible();
  expect(data.writes).toHaveLength(2);
  expect(data.writes[1]!.body).toEqual(data.writes[0]!.body);
  expect(data.order.estimated_ready_at).toBe(estimate);
  expect(data.unexpected).toEqual([]);
});

test("offline state is prominent and does not pretend reminders work", async ({
  page,
}) => {
  const data = await fixture(page, { order: "none" });
  await page.evaluate(() => {
    Object.defineProperty(navigator, "onLine", {
      configurable: true,
      get: () => false,
    });
    window.dispatchEvent(new Event("offline"));
  });
  await expect(
    page.getByRole("alert").filter({ hasText: "当前已断网，无法收到新订单" }),
  ).toBeVisible();
  await expect(page.getByText("声音提醒尚未开启")).toBeVisible();
  expect(data.unexpected).toEqual([]);
});

test("unknown preparation retains its original request through csrf 400 and 404", async ({
  page,
}) => {
  const data = await fixture(page, {
    path: "/merchant/orders",
    order: "preparing",
    loseAction: true,
  });
  await page.getByRole("button", { name: "还要等一会，更新预估" }).click();
  const dialog = page.getByRole("dialog", { name: "更新预计出餐时间" });
  await dialog.getByLabel("从现在起还需（分钟）").fill("16");
  await dialog.getByLabel("处理说明").fill("炉火调整，需要再等一会");
  await dialog.getByRole("button", { name: "更新并告知顾客" }).click();
  await expect.poll(() => data.writes.length).toBe(1);
  await expect(dialog.getByRole("alert")).toContainText("连接不上");
  const original = data.writes[0]!.body,
    estimate = data.order.estimated_ready_at;
  await page.context().clearCookies();
  for (const status of [400, 404]) {
    data.setCsrfFailure(true, status);
    await dialog.getByRole("button", { name: "确认原操作结果" }).click();
    await expect(dialog.getByRole("alert")).toContainText("安全校验");
    await expect(dialog.getByLabel("从现在起还需（分钟）")).toBeDisabled();
    const saved = await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem("merchant-prep:979:979") || "{}"),
    );
    expect(saved["daily-order"]).toEqual(original);
    expect(data.writes).toHaveLength(1);
  }
  data.setCsrfFailure(false);
  await dialog.getByRole("button", { name: "确认原操作结果" }).click();
  await expect(dialog).not.toBeVisible();
  expect(data.writes[1]!.body).toEqual(original);
  expect(data.order.estimated_ready_at).toBe(estimate);
  expect(data.unexpected).toEqual([]);
});

test("invalid successful preparation response never consumes the original request or reports success", async ({
  page,
}) => {
  const data = await fixture(page, {
    path: "/merchant/orders",
    order: "preparing",
  });
  data.setActionResponse({});
  await page.getByRole("button", { name: "还要等一会，更新预估" }).click();
  const dialog = page.getByRole("dialog", { name: "更新预计出餐时间" });
  await dialog.getByLabel("从现在起还需（分钟）").fill("14");
  await dialog.getByLabel("处理说明").fill("前单正在出餐");
  await dialog.getByRole("button", { name: "更新并告知顾客" }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "未能确认服务器返回的订单",
  );
  const original = data.writes[0]!.body,
    estimate = data.order.estimated_ready_at;
  for (const invalid of [
    { ...data.order, id: "other-order" },
    { ...data.order, stall_id: 978 },
    { ...data.order, status: "unknown" },
    { id: data.order.id, stall_id: 979, status: "preparing" },
  ]) {
    data.setActionResponse(invalid);
    await dialog.getByRole("button", { name: "确认原操作结果" }).click();
    await expect(dialog.getByRole("alert")).toContainText(
      "未能确认服务器返回的订单",
    );
    await expect(dialog.getByLabel("从现在起还需（分钟）")).toBeDisabled();
    expect(data.writes.at(-1)!.body).toEqual(original);
  }
  expect(data.writes).toHaveLength(5);
  await expect(
    page.getByText("已更新预计出餐时间和说明", { exact: true }),
  ).toHaveCount(0);
  data.setActionResponse(undefined);
  await dialog.getByRole("button", { name: "确认原操作结果" }).click();
  await expect(dialog).not.toBeVisible();
  expect(data.writes[5]!.body).toEqual(original);
  expect(data.order.estimated_ready_at).toBe(estimate);
  expect(data.unexpected).toEqual([]);
});

test("changing managed stall resets notification baseline instead of alerting on historical orders", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const notifications: any[] = [];
    (window as any).__notifications = notifications;
    (window as any).Notification = class {
      static permission = "granted";
      static async requestPermission() {
        return "granted";
      }
    };
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        register: async () => ({
          active: { state: "activated" },
          showNotification: async (title: string, options: any) =>
            notifications.push({ title, options }),
          getNotifications: async () => [],
        }),
      },
    });
  });
  const data = await fixture(page, { secondStall: true });
  await page.locator(".session-notifications > summary").click();
  await page.getByRole("button", { name: "开启并发送试提醒" }).click();
  await expect(
    page.getByRole("button", { name: "关闭本次会话提醒" }),
  ).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => (window as any).__notifications.length))
    .toBe(1);
  await page.getByLabel("选择管理的摊位").selectOption("978");
  await expect(
    page
      .getByRole("region", { name: "今天怎样营业" })
      .getByRole("link", { name: /处理待接单 1/ }),
  ).toBeVisible();
  await page.locator(".session-notifications > summary").click();
  await expect(
    page.getByRole("button", { name: "开启并发送试提醒" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "开启并发送试提醒" }).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).__notifications.length))
    .toBe(2);
  data.secondOrder.id = "new-north-order";
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await expect
    .poll(() => page.evaluate(() => (window as any).__notifications.length))
    .toBe(3);
  expect(
    await page.evaluate(() =>
      (window as any).__notifications.map((n: any) => n.title),
    ),
  ).toEqual(["烟火地图 · 提醒测试", "烟火地图 · 提醒测试", "有新订单待接单"]);
  expect(data.unexpected).toEqual([]);
});
