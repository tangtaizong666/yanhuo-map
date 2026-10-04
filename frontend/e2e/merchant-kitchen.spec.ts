import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Every API route is intercepted. No running demo records are read or written.
async function fixture(
  page: Page,
  path = "/merchant/orders",
  opts: { heartbeat?: boolean; closed?: boolean } = {},
) {
  let user: any = {
    id: 901,
    username: "kitchen_fixture",
    display_name: "南门小厨",
    is_merchant: true,
    is_staff: false,
  };
  const product: any = {
    id: 902,
    name: "招牌煎饼",
    image: "/images/food-cold-noodles.jpg",
    description: "",
    category: "小吃",
    price_cents: 800,
    stock: 10,
    stock_version: 3,
    is_active: true,
    sale_paused: false,
    taste_options: [],
  };
  const stall: any = {
    id: 901,
    name: "南门小厨",
    description: "",
    contact_phone: "",
    image: product.image,
    category: "小吃",
    area_name: "南门",
    address: "南门橙色棚",
    latitude: 30,
    longitude: 120,
    last_confirmed_at: new Date().toISOString(),
    closes_at: new Date(Date.now() + 7200000).toISOString(),
    status: opts.closed ? "closed" : "open",
    session_status: opts.closed ? "closed" : "open",
    accepting_orders: true,
    transaction_enabled: true,
    prep_minutes: 12,
    rating: null,
    review_count: 0,
    arrival_note: "",
    products: [product],
    reviews: [],
    prep_capacity: null,
    prep_active_orders: 6,
    stop_orders_at: null,
    business_session_id: 601,
    ...(opts.heartbeat
      ? { receiving_seen_at: null, receiving_status: "unknown" }
      : {}),
  };
  const makeOrder = (
    id: string,
    status: string,
    seconds: number,
    extra: any = {},
  ) => ({
    id,
    number: id.toUpperCase(),
    stall_id: 901,
    stall_name: stall.name,
    mode: "live",
    status,
    fulfillment_type: "pickup",
    payment_method: "offline",
    payment_status: "unpaid",
    payment_review_required: false,
    cancel_requested: false,
    created_at: new Date(Date.now() - 900000).toISOString(),
    expires_at: new Date(Date.now() + seconds * 1000).toISOString(),
    accepted_at: new Date(Date.now() - 600000).toISOString(),
    ready_at: new Date(Date.now() - 60000).toISOString(),
    estimated_ready_at:
      status === "preparing"
        ? new Date(Date.now() + seconds * 1000).toISOString()
        : null,
    prep_delay_reason: "",
    pickup_address: stall.address,
    total_cents: 1600,
    items_total_cents: 1600,
    delivery_fee_cents: 0,
    note: "分开装袋",
    items: [
      {
        product_id: product.id,
        name: product.name,
        image: product.image,
        quantity: 2,
        unit_price_cents: 800,
        portions: [
          { options: { 辣度: "不辣" }, note: "不要香菜" },
          { options: { 辣度: "微辣" }, note: "" },
        ],
      },
    ],
    ...extra,
  });
  const orders: any[] = [
    makeOrder("pending-later", "pending", 290),
    makeOrder("prep-later", "preparing", 1200),
    makeOrder("ready-order", "ready", 600),
    makeOrder("pending-first", "pending", 120),
    makeOrder("prep-first", "preparing", 600),
    makeOrder("payment-order", "pending_payment", 300, {
      payment_method: "wechat",
    }),
    makeOrder("delivery-order", "delivering", 600, {
      fulfillment_type: "delivery",
      payment_method: "wechat",
      payment_status: "paid",
    }),
    makeOrder("history-order", "completed", 0),
    makeOrder("prep-noeta", "preparing", 1, { estimated_ready_at: null }),
  ];
  const writes: { path: string; body: any }[] = [],
    heartbeats: any[] = [],
    unexpected: string[] = [],
    lookups: any[] = [];
  const corrections = new Set<string>();
  let loseCorrection = false,
    correctionResponse: any = undefined,
    lookupResponse: any = undefined,
    collision = false,
    csrfFailure = false,
    ordersFail = false,
    holdCsrf = false,
    release: (() => void) | undefined;
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      endpoint = url.pathname.replace("/api/v1", ""),
      send = (json: any) => route.fulfill({ json }),
      fail = (status: number, code: string, detail: string, extra = {}) =>
        route.fulfill({ status, json: { code, detail, ...extra } });
    if (endpoint === "/config")
      return send({
        brand: "烟火地图",
        demo_mode: true,
        user,
        areas: [],
        stale_minutes: 60,
        amap_key: "",
        amap_proxy: "",
      });
    if (endpoint === "/auth/me") return send(user);
    if (endpoint === "/auth/csrf") {
      if (holdCsrf)
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (csrfFailure) return fail(400, "csrf_unavailable", "安全校验暂不可用");
      return fulfillCsrf(route);
    }
    if (endpoint === "/merchant/stalls") return send([stall]);
    if (endpoint === "/merchant/orders")
      return ordersFail
        ? fail(503, "unavailable", "订单暂未同步")
        : send(orders);
    if (endpoint === "/merchant/metrics")
      return send({
        mode: "live",
        today: {
          revenue_cents: 0,
          orders_created: orders.length,
          orders_completed: 1,
        },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    if (endpoint === "/merchant/stalls/901/receiving-heartbeat") {
      heartbeats.push(req.postDataJSON());
      stall.receiving_seen_at = new Date().toISOString();
      stall.receiving_status = "recent";
      return send({
        receiving_seen_at: stall.receiving_seen_at,
        receiving_status: "recent",
      });
    }
    if (endpoint === "/merchant/stalls/901/pickup-lookup") {
      const body = req.postDataJSON();
      lookups.push(body);
      if (lookupResponse !== undefined) return send(lookupResponse);
      if (body.pickup_code !== "12345678")
        return fail(
          404,
          "pickup_order_not_found",
          "未找到本摊已出餐的自取订单",
        );
      if (collision && body.number !== "READY-ORDER")
        return fail(409, "pickup_code_ambiguous", "取餐码重复");
      return send(orders.find((o) => o.id === "ready-order"));
    }
    if (endpoint === "/merchant/products/902/stock-correction") {
      const body = req.postDataJSON();
      writes.push({ path: endpoint, body });
      if (correctionResponse !== undefined) return send(correctionResponse);
      if (corrections.has(body.idempotency_key))
        return send({ product, replayed: true });
      if (body.expected_stock_version !== product.stock_version)
        return fail(409, "stock_version_conflict", "线上份数已变化", {
          product,
        });
      product.stock = body.stock;
      product.stock_version++;
      corrections.add(body.idempotency_key);
      if (loseCorrection) {
        loseCorrection = false;
        return route.abort();
      }
      return send({ product, replayed: false });
    }
    if (endpoint === "/merchant/products/902") {
      const body = req.postDataJSON();
      writes.push({ path: endpoint, body });
      if ("stock" in body)
        return fail(400, "stock_edit_requires_correction", "请使用更正入口");
      Object.assign(product, body);
      return send(product);
    }
    if (
      endpoint === "/merchant/stalls/901/profile" ||
      endpoint === "/merchant/stalls/901/status"
    ) {
      const body = req.postDataJSON();
      writes.push({ path: endpoint, body });
      if (body.cutoff_only) {
        if (body.expected_session_id !== stall.business_session_id)
          return fail(
            409,
            "business_session_changed",
            "营业场次已变化，请刷新后重试",
          );
        if (stall.session_status === "closed")
          return fail(409, "business_session_ended", "本场营业已结束");
        expect(Object.keys(body).sort()).toEqual([
          "cutoff_only",
          "expected_session_id",
          "stop_orders_at",
        ]);
        stall.stop_orders_at = body.stop_orders_at;
        return send(stall);
      }
      if (body.status === "open" && stall.session_status === "closed")
        stall.stop_orders_at = body.stop_orders_at ?? null;
      Object.assign(stall, body);
      if (body.status) stall.session_status = body.status;
      return send(stall);
    }
    if (/^\/merchant\/orders\/[^/]+\/action$/.test(endpoint)) {
      const body = req.postDataJSON();
      writes.push({ path: endpoint, body });
      const order = orders.find((o) => endpoint.includes(`/${o.id}/`));
      if (body.action === "confirm_payment") order.payment_status = "paid";
      if (body.action === "complete") {
        if (order.payment_status !== "paid" || body.pickup_code !== "12345678")
          return fail(409, "pickup_invalid", "请先付款并核对取餐码");
        order.status = "completed";
      }
      return send(order);
    }
    unexpected.push(`${req.method()} ${endpoint}`);
    return fail(500, "unexpected_fixture", "Unexpected isolated request");
  });
  await page.goto(path);
  await expect(
    page.getByRole("heading", { name: /经营首页|订单处理|商品管理/ }).first(),
  ).toBeVisible();
  return {
    product,
    stall,
    orders,
    writes,
    heartbeats,
    unexpected,
    lookups,
    setLoseCorrection: () => {
      loseCorrection = true;
    },
    setCorrectionResponse: (v: any) => {
      correctionResponse = v;
    },
    setLookupResponse: (v: any) => {
      lookupResponse = v;
    },
    setCollision: () => {
      collision = true;
    },
    setCsrfFailure: (v: boolean) => {
      csrfFailure = v;
    },
    setOrdersFail: (v: boolean) => {
      ordersFail = v;
    },
    holdCsrf: () => {
      holdCsrf = true;
    },
    releaseCsrf: () => {
      holdCsrf = false;
      release?.();
    },
    csrfWaiting: () => !!release,
    switchUser: () => {
      user = { ...user, id: 903 };
    },
  };
}
async function correctionForm(page: Page, stock = "6") {
  await page.locator(".inventory-correction > summary").click();
  await page.getByLabel("招牌煎饼更正后线上可卖份数").fill(stock);
  await page.getByLabel("招牌煎饼库存更正原因").fill("盘点后可卖份数填写多了");
}
const correctionWrites = (data: Awaited<ReturnType<typeof fixture>>) =>
  data.writes.filter((w) => w.path.endsWith("/stock-correction"));
const openTools = (page: Page) =>
  page
    .getByRole("button", { name: "查取餐码 · 搜索与筛选", exact: true })
    .click();

test("kitchen groups prioritize expiry and ETA, preserve portions and delivery, and expose history", async ({
  page,
}, info) => {
  const data = await fixture(page);
  await expect(
    page.getByRole("button", { name: "出餐台", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".merchant-order .order-number")).toHaveText([
    "#PENDING-FIRST",
    "#PENDING-LATER",
    "#PREP-NOETA",
    "#PREP-FIRST",
    "#PREP-LATER",
    "#READY-ORDER",
    "#PAYMENT-ORDER",
    "#DELIVERY-ORDER",
  ]);
  const first = page.locator(".merchant-order").first();
  await expect(first).toContainText("不要香菜");
  await expect(first).toContainText("微辣");
  await expect(first).toContainText("分开装袋");
  await expect(
    first.getByRole("button", { name: "确认接单", exact: true }),
  ).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`kitchen-${width}.png`),
      fullPage: true,
    });
    if (width === 390) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: info.outputPath("kitchen-390-first-screen.png"),
      });
      const button = await first
        .getByRole("button", { name: "确认接单", exact: true })
        .boundingBox();
      const bottomNavigation = await page
        .getByRole("navigation", { name: "商家底部导航", exact: true })
        .boundingBox();
      expect(
        button!.y + button!.height,
        "First pending action must be above the mobile bottom navigation",
      ).toBeLessThanOrEqual(bottomNavigation!.y - 10);
    }
    const targets = await page
      .locator(
        ".m-board-tabs button:visible, .pickup-lookup button:visible, .pickup-lookup input:visible, .m-kitchen-tools-toggle:visible",
      )
      .evaluateAll((nodes) =>
        nodes.map((n) => n.getBoundingClientRect().height),
      );
    expect(targets.length).toBeGreaterThan(0);
    expect(targets.every((h) => h >= 44)).toBe(true);
  }
  await page.getByRole("button", { name: "全部订单", exact: true }).click();
  await expect(page.locator(".merchant-order")).toHaveCount(9);
  await expect(page.getByText("#HISTORY-ORDER")).toBeVisible();
  expect(data.unexpected).toEqual([]);
});

test("duplicate pickup lookup requests order number and keeps payment and redemption separate", async ({
  page,
}) => {
  const data = await fixture(page);
  data.setCollision();
  await openTools(page);
  await page.getByLabel("8 位取餐码", { exact: true }).fill("12345678");
  await page.getByRole("button", { name: "查找待取餐订单" }).click();
  await expect(
    page.getByText("本摊有重复取餐码，请补充完整订单号再查找。"),
  ).toBeVisible();
  await page.getByLabel("完整订单号", { exact: true }).fill("READY-ORDER");
  await page.getByRole("button", { name: "查找待取餐订单" }).click();
  const details = page.getByRole("dialog", { name: "订单详情", exact: true });
  await expect(details).toBeVisible();
  expect(data.writes).toEqual([]);
  expect(data.lookups).toEqual([
    { pickup_code: "12345678" },
    { pickup_code: "12345678", number: "READY-ORDER" },
  ]);
  await expect(details.getByRole("button", { name: "核销并完成" })).toHaveCount(
    0,
  );
  await details.getByRole("button", { name: "确认已收到 ¥16" }).click();
  const confirm = page.getByRole("dialog", { name: "确认这笔线下收款" });
  await confirm.getByRole("button", { name: "确认收款", exact: true }).click();
  await expect(
    details.getByRole("button", { name: "核销并完成" }),
  ).toBeVisible();
  await expect(details.locator("#detail-pickup-code")).toHaveValue("12345678");
  await details.getByRole("button", { name: "核销并完成" }).click();
  await expect
    .poll(() => data.orders.find((o) => o.id === "ready-order").status)
    .toBe("completed");
  expect(data.writes.map((w) => w.body.action)).toEqual([
    "confirm_payment",
    "complete",
  ]);
  expect(data.unexpected).toEqual([]);
});

test("pickup lookup cannot expose another stall or promote invalid data to a completed order", async ({
  page,
}) => {
  const data = await fixture(page);
  await openTools(page);
  await page.getByLabel("8 位取餐码", { exact: true }).fill("87654321");
  await page.getByRole("button", { name: "查找待取餐订单" }).click();
  await expect(page.getByText("未找到本摊已出餐的自取订单")).toBeVisible();
  data.setLookupResponse({ ...data.orders[2], stall_id: 999 });
  await page.getByRole("button", { name: "查找待取餐订单" }).click();
  await expect(page.getByText("查单结果未确认，请重试。")).toBeVisible();
  await expect(
    page.getByRole("dialog", { name: "订单详情", exact: true }),
  ).not.toBeVisible();
  expect(data.writes).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test("product editing never patches inventory and pausing supply preserves positive stock", async ({
  page,
}) => {
  const data = await fixture(page, "/merchant/products");
  await page.getByRole("button", { name: /^编辑商品：/ }).click();
  const dialog = page.getByRole("dialog", { name: "编辑商品", exact: true });
  await expect(dialog.getByLabel("线上剩余可卖份数")).toHaveCount(0);
  await dialog.getByLabel("商品名称", { exact: false }).fill("招牌煎饼（新）");
  await dialog.getByRole("button", { name: "保存修改" }).click();
  await expect(dialog).not.toBeVisible();
  expect(data.writes[0].body).not.toHaveProperty("stock");
  expect(data.product.stock).toBe(10);
  await page.getByRole("button", { name: "暂停供应", exact: true }).click();
  await expect.poll(() => data.product.sale_paused).toBe(true);
  expect(data.product.stock).toBe(10);
  data.product.stock += 4;
  data.product.stock_version++;
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await expect(
    page.getByRole("button", { name: "恢复供应", exact: true }),
  ).toBeVisible();
  expect(data.product.stock).toBe(14);
  await page.getByRole("button", { name: "恢复供应", exact: true }).click();
  await expect.poll(() => data.product.sale_paused).toBe(false);
  expect(data.writes.every((w) => !("stock" in w.body))).toBe(true);
  expect(data.unexpected).toEqual([]);
});

test("inventory correction conflict requires a fresh count and version before a new request", async ({
  page,
}, info) => {
  const data = await fixture(page, "/merchant/products");
  await correctionForm(page);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`inventory-${width}.png`),
      fullPage: true,
    });
  }
  data.product.stock = 9;
  data.product.stock_version++;
  await page
    .getByRole("button", { name: "确认更正线上可卖份数", exact: true })
    .click();
  await expect(
    page.getByText(
      "期间发生了下单、取消或补货，原数量已过时；本次未更正，请按最新数量重新核对。",
    ),
  ).toBeVisible();
  expect(data.product.stock).toBe(9);
  await expect(page.getByLabel("招牌煎饼更正后线上可卖份数")).toBeDisabled();
  await page.getByRole("button", { name: "按最新数量重新填写" }).click();
  await expect(page.getByLabel("招牌煎饼更正后线上可卖份数")).toHaveValue("");
  await page.getByLabel("招牌煎饼更正后线上可卖份数").fill("7");
  await page
    .getByRole("button", { name: "确认更正线上可卖份数", exact: true })
    .click();
  await expect(page.getByText(/线上剩余可卖份数已确认为 7 份/)).toBeVisible();
  const attempts = correctionWrites(data);
  expect(attempts).toHaveLength(2);
  expect(attempts[0].body.expected_stock_version).toBe(3);
  expect(attempts[1].body.expected_stock_version).toBe(4);
  expect(attempts[1].body.idempotency_key).not.toBe(
    attempts[0].body.idempotency_key,
  );
  expect(data.unexpected).toEqual([]);
});

test("unknown correction survives reload and CSRF preflight rejection with the exact original payload", async ({
  page,
}) => {
  const data = await fixture(page, "/merchant/products");
  data.setLoseCorrection();
  await correctionForm(page);
  await page
    .getByRole("button", { name: "确认更正线上可卖份数", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "确认原更正结果" }),
  ).toBeVisible();
  expect(data.product.stock).toBe(6);
  expect(data.product.stock_version).toBe(4);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "确认原更正结果" }),
  ).toBeVisible();
  await expect(page.getByLabel("招牌煎饼更正后线上可卖份数")).toBeDisabled();
  data.setCsrfFailure(true);
  await page.context().clearCookies();
  await page.getByRole("button", { name: "确认原更正结果" }).click();
  await expect(page.getByText(/本次更正结果尚未确认/)).toBeVisible();
  expect(correctionWrites(data)).toHaveLength(1);
  data.setCsrfFailure(false);
  await page.getByRole("button", { name: "确认原更正结果" }).click();
  await expect(page.getByText(/线上剩余可卖份数已确认为 6 份/)).toBeVisible();
  expect(correctionWrites(data)).toHaveLength(2);
  expect(correctionWrites(data)[1].body).toEqual(
    correctionWrites(data)[0].body,
  );
  expect(data.product.stock_version).toBe(4);
  expect(data.unexpected).toEqual([]);
});

test("malformed successful correction response keeps a safe retry instead of claiming success", async ({
  page,
}) => {
  const data = await fixture(page, "/merchant/products");
  data.setCorrectionResponse({ ok: true });
  await correctionForm(page);
  await page
    .getByRole("button", { name: "确认更正线上可卖份数", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "确认原更正结果" }),
  ).toBeVisible();
  expect(data.product.stock).toBe(10);
  data.setCorrectionResponse(undefined);
  await page.getByRole("button", { name: "确认原更正结果" }).click();
  await expect(page.getByText(/线上剩余可卖份数已确认为 6 份/)).toBeVisible();
  expect(correctionWrites(data)[1].body).toEqual(
    correctionWrites(data)[0].body,
  );
  expect(data.unexpected).toEqual([]);
});

test("switching merchant during CSRF preflight aborts the old inventory correction", async ({
  page,
}) => {
  const data = await fixture(page, "/merchant/products");
  await correctionForm(page);
  data.holdCsrf();
  await page
    .getByRole("button", { name: "确认更正线上可卖份数", exact: true })
    .click();
  await expect.poll(data.csrfWaiting).toBe(true);
  data.switchUser();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    page.getByRole("button", { name: "确认原更正结果" }),
  ).toHaveCount(0);
  data.releaseCsrf();
  await expect(
    page.getByRole("button", { name: /^编辑商品：/ }),
  ).toBeVisible();
  expect(correctionWrites(data)).toEqual([]);
  expect(data.product.stock).toBe(10);
  expect(data.unexpected).toEqual([]);
});

test("capacity begins disabled, suggests five and counts each occupying state separately", async ({
  page,
}, info) => {
  const data = await fixture(page, "/merchant");
  const settings = page.locator(".queue-settings");
  await expect(settings.locator(".queue-counts")).toHaveText(
    /待付款占位 1待接单 2制作中 3/,
  );
  await expect(settings).toContainText("备餐容量限制未开启");
  await settings.locator("summary").click();
  await expect(settings.getByLabel("限制同时备餐订单")).not.toBeChecked();
  await settings.getByLabel("限制同时备餐订单").check();
  await expect(settings.getByLabel("最多占位（单）")).toHaveValue("5");
  await settings.getByLabel("最多占位（单）").fill("8");
  await settings.getByRole("button", { name: "保存备餐上限" }).click();
  await expect(
    settings.getByText("备餐上限已保存，已有订单继续处理。"),
  ).toBeVisible();
  expect(data.writes[0].body).toEqual({ prep_capacity: 8 });
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: info.outputPath(`capacity-${width}.png`),
      fullPage: true,
    });
  }
  expect(data.orders.filter((o) => o.status === "pending")).toHaveLength(2);
  expect(data.unexpected).toEqual([]);
});

test("session cutoff cannot exceed closing and never refreshes position or carry into reopening", async ({
  page,
}) => {
  const data = await fixture(page, "/merchant");
  const settings = page.locator(".queue-settings");
  await settings.locator("summary").click();
  const cutoff = settings.getByLabel("本场停止接新单时间（选填）");
  const local = (date: Date) =>
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}T${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  await cutoff.fill(local(new Date(Date.now() + 10800000)));
  await settings.getByRole("button", { name: "保存本场截止时间" }).click();
  expect(data.writes).toEqual([]);
  const confirmed = data.stall.last_confirmed_at;
  await cutoff.fill(local(new Date(Date.now() + 3600000)));
  await settings.getByRole("button", { name: "保存本场截止时间" }).click();
  await expect(
    settings.getByText("本场接单截止已保存，位置确认时间和已有订单未改变。"),
  ).toBeVisible();
  expect(data.writes[0].body).toMatchObject({
    cutoff_only: true,
    expected_session_id: 601,
  });
  expect(data.writes[0].body).not.toHaveProperty("status");
  expect(data.writes[0].body).not.toHaveProperty("confirm_location");
  expect(data.stall.last_confirmed_at).toBe(confirmed);
  data.stall.status = data.stall.session_status = "closed";
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await page.getByRole("button", { name: /就在这里，开始出摊/ }).click();
  await expect.poll(() => data.stall.status).toBe("open");
  expect(data.stall.stop_orders_at).toBeNull();
  expect(data.writes.at(-1)?.body).not.toHaveProperty("stop_orders_at");
  expect(data.unexpected).toEqual([]);
});

test("receiving heartbeat needs a visible successful sync, is throttled and never updates location", async ({
  page,
}) => {
  const data = await fixture(page, "/merchant", { heartbeat: true });
  await expect.poll(() => data.heartbeats.length).toBe(1);
  const confirmed = data.stall.last_confirmed_at;
  await page.clock.install();
  await page.getByRole("button", { name: "刷新工作台" }).click();
  expect(data.heartbeats).toHaveLength(1);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.fastForward(40000);
  expect(data.heartbeats).toHaveLength(1);
  data.setOrdersFail(true);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(
    page.getByText("订单暂未同步", { exact: true }).first(),
  ).toBeVisible();
  expect(data.heartbeats).toHaveLength(1);
  data.setOrdersFail(false);
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await expect.poll(() => data.heartbeats.length).toBe(2);
  expect(data.stall.last_confirmed_at).toBe(confirmed);
  expect(data.stall.accepting_orders).toBe(true);
  expect(data.writes).toEqual([]);
  expect(data.heartbeats.every((body) => Object.keys(body).length === 0)).toBe(
    true,
  );
  expect(data.unexpected).toEqual([]);
});

test("account change during lookup preflight cannot send the previous customer's code", async ({
  page,
}) => {
  const data = await fixture(page);
  await openTools(page);
  data.holdCsrf();
  await page.getByLabel("8 位取餐码", { exact: true }).fill("12345678");
  await page.getByRole("button", { name: "查找待取餐订单" }).click();
  await expect.poll(data.csrfWaiting).toBe(true);
  data.switchUser();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByLabel("8 位取餐码", { exact: true })).toHaveValue("");
  data.releaseCsrf();
  if (
    await page
      .getByRole("button", { name: "查取餐码 · 搜索与筛选", exact: true })
      .isVisible()
  )
    await openTools(page);
  await expect(
    page.getByRole("button", { name: "查找待取餐订单" }),
  ).toBeEnabled();
  expect(data.lookups).toEqual([]);
  expect(data.writes).toEqual([]);
  expect(data.unexpected).toEqual([]);
});

test("cutoff-only save cannot restore a stall paused on another device", async ({
  page,
}) => {
  const data = await fixture(page, "/merchant");
  const settings = page.locator(".queue-settings");
  await settings.locator("summary").click();
  data.stall.session_status = data.stall.status = "paused";
  const confirmed = data.stall.last_confirmed_at;
  await settings.getByRole("button", { name: "保存本场截止时间" }).click();
  await expect(
    settings.getByText("本场接单截止已保存，位置确认时间和已有订单未改变。"),
  ).toBeVisible();
  expect(data.writes).toHaveLength(1);
  expect(data.writes[0].body).toEqual({
    cutoff_only: true,
    expected_session_id: 601,
    stop_orders_at: null,
  });
  expect(data.stall.status).toBe("paused");
  expect(data.stall.last_confirmed_at).toBe(confirmed);
  expect(data.unexpected).toEqual([]);
});

test("cutoff rejects a replaced session and old API data never sends a status fallback", async ({
  page,
}) => {
  const data = await fixture(page, "/merchant");
  const settings = page.locator(".queue-settings");
  await settings.locator("summary").click();
  const field = settings.getByLabel("本场停止接新单时间（选填）");
  const date = new Date(Date.now() + 600000);
  const local = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}T${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  await field.fill(local);
  data.stall.business_session_id = 602;
  await settings.getByRole("button", { name: "保存本场截止时间" }).click();
  await expect(
    settings.getByText("营业场次已变化，请刷新后重试"),
  ).toBeVisible();
  await expect(field).toHaveValue("");
  expect(data.stall.stop_orders_at).toBeNull();
  expect(data.writes[0].body.expected_session_id).toBe(601);
  delete data.stall.business_session_id;
  await page.getByRole("button", { name: "刷新工作台" }).click();
  await settings.getByRole("button", { name: "保存本场截止时间" }).click();
  await expect(
    settings.getByText("暂未获取当前营业场次，请刷新工作台后再设置截止时间。"),
  ).toBeVisible();
  expect(data.writes).toHaveLength(1);
  expect(data.unexpected).toEqual([]);
});
