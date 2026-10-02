import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// All APIs are intercepted. No live applications, stock, images or order data are changed.
async function setup(
  page: Page,
  options: {
    ordinary?: boolean;
    empty?: boolean;
    application?: any;
    path?: string;
    missingLocation?: boolean;
    staleMinutes?: number;
    ageMinutes?: number;
    lostRestock?: boolean;
    map?: boolean;
    metrics?: any;
  } = {},
) {
  const user = {
    id: 880,
    username: "operations_fixture",
    display_name: "开摊测试商家",
    is_merchant: !options.ordinary,
    is_staff: false,
  };
  let application = options.application || null;
  const writes: { path: string; body: any }[] = [],
    reads: string[] = [],
    unexpected: string[] = [];
  const delivery = {
    mode: "simulation",
    enabled: false,
    available: false,
    approved: false,
    reason: "未开启",
    fee_cents: 200,
    point_ids: [],
    available_points: [],
    points: [],
    min_order_cents: 0,
    capacity: 5,
    starts_at: "00:00",
    ends_at: "23:59",
    eta_min_minutes: 20,
    eta_max_minutes: 40,
  };
  const services = {
    mode: "simulation",
    simulation_available: true,
    online_payment_enabled: false,
    delivery_enabled: false,
    wechat_payment: { mode: "simulation", available: false },
    delivery,
  };
  const stall: any = {
    id: 880,
    name: "南门煎饼",
    description: "",
    category: "小吃",
    image: "/images/food-cold-noodles.jpg",
    area_id: 1,
    area_name: "校园南门",
    status: "open",
    session_status: "open",
    last_confirmed_at: new Date(
      Date.now() - (options.ageMinutes || 0) * 60000,
    ).toISOString(),
    transaction_enabled: true,
    accepting_orders: true,
    order_unavailable_reason: "",
    prep_minutes: 10,
    address: options.missingLocation ? "" : "南门入口橙色棚",
    latitude: options.missingLocation ? null : 30,
    longitude: options.missingLocation ? null : 120,
    location_draft_address: "",
    arrival_note: "",
    arrival_image: "",
    contact_phone: "",
    closes_at: "",
    merchant_name: "南门早餐店",
    services,
    delivery,
    products: [
      {
        id: 881,
        name: "杂粮煎饼",
        description: "",
        category: "小吃",
        image: "/images/food-cold-noodles.jpg",
        price_cents: 800,
        stock: 10,
        is_active: true,
      },
    ],
    reviews: [],
    rating: null,
    review_count: 0,
  };
  const orders = [
    {
      id: "operations-order",
      number: "OPS001",
      stall_id: 880,
      stall_name: stall.name,
      status: "preparing",
      fulfillment_type: "pickup",
      mode: "live",
      total_cents: 800,
      payment_method: "offline",
      payment_status: "unpaid",
      created_at: new Date().toISOString(),
      items: [
        {
          name: "杂粮煎饼",
          quantity: 1,
          unit_price_cents: 800,
          image: stall.image,
        },
      ],
      cancel_requested: false,
    },
  ];
  const replayed = new Set<string>();
  let lose = !!options.lostRestock;
  let csrfMode = "ok",
    releaseCsrf: (() => void) | undefined;
  if (options.map)
    await page.addInitScript(() => {
      (window as any).AMap = {
        Map: class {
          constructor() {
            (window as any).__testMap = this;
          }
          callback: any;
          on(_: string, callback: any) {
            this.callback = callback;
          }
          add() {}
          destroy() {}
        },
        Marker: class {
          setPosition() {}
        },
      };
    });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (value: any) => route.fulfill({ json: value });
    if (request.method() === "GET") reads.push(path);
    if (path === "/config")
      return send({
        brand: "烟火地图",
        demo_mode: true,
        services_simulation_enabled: true,
        stale_minutes: options.staleMinutes || 60,
        public_base_url: "",
        user,
        amap_key: options.map ? "fixture-key" : "",
        amap_proxy: "",
        areas: [{ id: 1, name: "校园南门", latitude: 30, longitude: 120 }],
      });
    if (path === "/auth/me") return send(user);
    if (path === "/auth/csrf") {
      if (csrfMode === "hold")
        await new Promise<void>((resolve) => {
          releaseCsrf = resolve;
        });
      if (csrfMode === "fail")
        return route.fulfill({
          status: 503,
          json: { detail: "隔离测试：安全校验失败" },
        });
      return fulfillCsrf(route);
    }
    if (path === "/merchant/application") {
      if (request.method() === "PATCH") {
        const body = request.postDataJSON();
        writes.push({ path, body });
        application = {
          id: 1,
          status: "draft",
          source: "self",
          ...application,
          ...body,
        };
      }
      return send({ application });
    }
    if (path === "/merchant/application/submit") {
      writes.push({ path, body: null });
      application.status = "submitted";
      return send({ application });
    }
    if (path === "/merchant/stalls") return send(options.empty ? [] : [stall]);
    if (path === "/merchant/orders") return send(orders);
    if (path === "/merchant/metrics")
      return send({
        mode: "simulation",
        today: { revenue_cents: 0, orders_created: 1, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
        ...options.metrics,
      });
    if (path === "/merchant/stalls/880/services") return send(services);
    if (path === "/merchant/stalls/880/delivery") return send(delivery);
    if (path === "/merchant/stalls/880/location-reports")
      return send({
        unresolved_count: 1,
        reports: [
          {
            id: 1,
            kind: "not_found",
            location_snapshot: { address: "同学看到的旧南门位置" },
            created_at: new Date().toISOString(),
            resolved: false,
            snapshot_source: "user_reported_display",
          },
        ],
      });
    if (path === "/merchant/stalls/880/profile") {
      const body = request.postDataJSON();
      writes.push({ path, body });
      Object.assign(stall, body);
      return send(stall);
    }
    if (path === "/merchant/stalls/880/status") {
      const body = request.postDataJSON();
      writes.push({ path, body });
      Object.assign(stall, body, { session_status: body.status });
      if (body.confirm_location)
        stall.last_confirmed_at = new Date().toISOString();
      return send(stall);
    }
    if (path === "/merchant/stalls/880/image") {
      writes.push({ path, body: "multipart" });
      return send({ url: "/images/arrival-fixture.png" });
    }
    if (path === "/merchant/stalls/880/products") {
      const body = request.postDataJSON();
      writes.push({ path, body });
      const product = { ...body, id: stall.products.length + 900 };
      stall.products.push(product);
      return send(product);
    }
    if (path === "/merchant/stalls/880/restock") {
      const body = request.postDataJSON();
      writes.push({ path, body });
      const replay = replayed.has(body.idempotency_key);
      if (!replay) {
        for (const item of body.items)
          stall.products.find((p: any) => p.id === item.product_id).stock +=
            item.quantity;
        replayed.add(body.idempotency_key);
      }
      if (lose) {
        lose = false;
        return route.abort("failed");
      }
      return send({ products: stall.products, replayed: replay });
    }
    unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  await page.goto(options.path || "/merchant");
  return {
    user,
    stall,
    writes,
    reads,
    unexpected,
    application: () => application,
    setCsrf: (mode: string) => {
      csrfMode = mode;
    },
    releaseCsrf: () => {
      csrfMode = "ok";
      releaseCsrf?.();
    },
  };
}
async function fillApplication(page: Page) {
  await page.getByLabel("经营主体名称").fill("南门早餐个体户");
  await page.getByLabel("摊位名称", { exact: true }).fill("南门煎饼");
  await page.getByLabel("联系电话", { exact: true }).fill("13800000000");
  await page.getByLabel("所在校园区域").selectOption("1");
  await page.getByLabel("主要经营品类").fill("煎饼");
  await page.getByLabel("通常在哪里出摊").fill("南门入口橙色棚");
}

test("ordinary applicant saves and submits without invoking merchant management APIs", async ({
  page,
}) => {
  const fixture = await setup(page, {
    ordinary: true,
    path: "/merchant/apply",
  });
  await fillApplication(page);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("草稿已保存，可以稍后继续填写。")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("摊位名称", { exact: true })).toHaveValue(
    "南门煎饼",
  );
  await page.getByRole("button", { name: "提交入驻资料", exact: true }).click();
  await expect(page.getByText("已提交，等待团队核验")).toBeVisible();
  expect(
    fixture.reads.filter(
      (p) => p.startsWith("/merchant/") && p !== "/merchant/application",
    ),
  ).toEqual([]);
  expect(fixture.reads).not.toContain("/orders/active-summary");
  expect(fixture.application().status).toBe("submitted");
  expect(fixture.unexpected).toEqual([]);
});

test("assisted draft requires merchant confirmation and preserves review feedback", async ({
  page,
}) => {
  const fixture = await setup(page, {
    ordinary: true,
    path: "/merchant/apply",
    application: {
      status: "needs_changes",
      source: "assisted",
      review_note: "请补充常用出摊位置",
      business_name: "团队代录主体",
    },
  });
  await expect(
    page.getByText("团队协助录入，请核对后确认提交。"),
  ).toBeVisible();
  await expect(page.getByText("团队反馈：请补充常用出摊位置")).toBeVisible();
  await fillApplication(page);
  await page
    .getByRole("button", { name: "确认资料并提交", exact: true })
    .click();
  await expect(page.getByText("已提交，等待团队核验")).toBeVisible();
  expect(fixture.writes[0]!.body.source).toBeUndefined();
  expect(fixture.unexpected).toEqual([]);
});

test("empty merchant and approved ordinary applicant receive real next steps", async ({
  page,
}) => {
  const fixture = await setup(page, {
    ordinary: true,
    path: "/merchant/apply",
    application: {
      status: "approved",
      source: "self",
      approved_stall_id: 880,
      stall_name: "南门煎饼",
      address_note: "南门",
      contact_phone: "13800000000",
    },
  });
  await expect(
    page.getByRole("button", { name: "进入开摊准备" }),
  ).toBeVisible();
  fixture.user.is_merchant = true;
  fixture.stall.products = [];
  await page.getByRole("button", { name: "进入开摊准备" }).click();
  await expect(page.getByRole("region", { name: "开摊准备" })).toBeVisible();
  await expect(page.getByText("1. 添加第一道可售商品")).toBeVisible();
  expect(fixture.unexpected).toEqual([]);
});

test("merchant without an assigned stall can use application form", async ({
  page,
}) => {
  const fixture = await setup(page, { empty: true });
  await expect(
    page.getByRole("heading", { name: "把小摊带到校园地图上" }),
  ).toBeVisible();
  await expect(page.getByLabel("经营主体名称")).toBeVisible();
  expect(fixture.reads).not.toContain("/merchant/orders");
  expect(fixture.unexpected).toEqual([]);
});

test("online pause is separate from physical status and closing leaves unfinished orders", async ({
  page,
}) => {
  const fixture = await setup(page);
  const confirmed = fixture.stall.last_confirmed_at;
  await page.getByRole("button", { name: "忙不过来，暂停接单" }).click();
  await expect(
    page.getByRole("button", { name: "恢复线上接单" }),
  ).toBeVisible();
  expect(fixture.writes[0]!.body).toEqual({ accepting_orders: false });
  expect(fixture.stall.status).toBe("open");
  expect(fixture.stall.last_confirmed_at).toBe(confirmed);
  await page.getByRole("button", { name: "暂时离开摊位", exact: true }).click();
  await expect.poll(() => fixture.stall.status).toBe("paused");
  expect(fixture.writes[1]!.body).toEqual({
    status: "paused",
    confirm_location: false,
  });
  await page.getByRole("button", { name: "今日收摊", exact: true }).click();
  await expect(
    page.getByText("还有 1 单未完成", { exact: true }),
  ).toBeVisible();
  expect(fixture.writes[2]!.body).toEqual({
    status: "closed",
    confirm_location: false,
  });
  expect(fixture.stall.last_confirmed_at).toBe(confirmed);
  await page.getByRole("button", { name: "就在这里，开始出摊" }).click();
  await expect.poll(() => fixture.stall.status).toBe("open");
  expect(fixture.writes[3]!.body).toEqual({
    status: "open",
    confirm_location: true,
  });
  expect(fixture.unexpected).toEqual([]);
});

test("configured expiry threshold warns ahead and explicit confirmation clears it", async ({
  page,
}) => {
  const fixture = await setup(page, { staleMinutes: 30, ageMinutes: 25 });
  await expect(page.getByText(/位置将在 10 分钟内需要重新确认/)).toBeVisible();
  await page.getByRole("button", { name: "我还在这里", exact: true }).click();
  await expect(page.getByText(/位置将在 10 分钟内需要重新确认/)).toBeHidden();
  expect(fixture.writes[0]!.body.confirm_location).toBe(true);
  expect(fixture.unexpected).toEqual([]);
});

test("unconfigured map saves an address draft without confirming or fabricating coordinates", async ({
  page,
}) => {
  const fixture = await setup(page, {
    path: "/merchant/store#location",
    missingLocation: true,
  });
  await page.getByLabel("详细取餐地址").fill("食堂东门第二个棚子");
  await page
    .getByRole("button", { name: "仅保存地址草稿", exact: true })
    .click();
  await expect(
    page.getByText("地址草稿已保存，尚未确认出摊位置"),
  ).toBeVisible();
  expect(fixture.writes).toEqual([
    {
      path: "/merchant/stalls/880/profile",
      body: { location_draft_address: "食堂东门第二个棚子" },
    },
  ]);
  expect(fixture.stall.latitude).toBeNull();
  expect(fixture.stall.address).toBe("");
  expect(fixture.unexpected).toEqual([]);
});

test("map point requires explicit save and sends the selected coordinates", async ({
  page,
}) => {
  const fixture = await setup(page, {
    path: "/merchant/store#location",
    missingLocation: true,
    map: true,
  });
  await page
    .getByRole("button", { name: "在地图上选择位置", exact: true })
    .click();
  await page.evaluate(() =>
    (window as any).__testMap.callback({
      lnglat: { getLat: () => 30.5, getLng: () => 120.8 },
    }),
  );
  expect(fixture.writes).toHaveLength(0);
  await page.getByLabel("详细取餐地址").fill("东门橙色棚");
  await page
    .getByRole("button", { name: "确认并保存位置与时间", exact: true })
    .click();
  await expect.poll(() => fixture.writes.length).toBe(1);
  expect(fixture.writes[0]!.body).toMatchObject({
    latitude: 30.5,
    longitude: 120.8,
    address: "东门橙色棚",
    confirm_location: true,
  });
  expect(fixture.unexpected).toEqual([]);
});

test("arrival photo and directions update without replacing the merchant cover", async ({
  page,
}) => {
  const fixture = await setup(page, { path: "/merchant/store" });
  const cover = fixture.stall.image;
  await page.locator("summary").filter({ hasText: "店铺资料" }).click();
  await page.getByLabel("怎样更容易找到你").fill("橙色棚，校园南门左侧");
  await page
    .getByLabel("上传找摊参照照片")
    .setInputFiles({
      name: "arrival.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==",
        "base64",
      ),
    });
  await expect(
    page.getByText("找摊照片已上传，保存店铺信息后生效，不会替换封面"),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存店铺信息", exact: true }).click();
  await expect
    .poll(() => fixture.stall.arrival_image)
    .toBe("/images/arrival-fixture.png");
  expect(fixture.writes.find((w) => w.path.endsWith("/profile"))!.body).toEqual(
    {
      arrival_note: "橙色棚，校园南门左侧",
      arrival_image: "/images/arrival-fixture.png",
    },
  );
  expect(fixture.stall.image).toBe(cover);
  expect(fixture.unexpected).toEqual([]);
});

test("first dish starts with blank stock and accurately explains zero stock", async ({
  page,
}) => {
  const fixture = await setup(page, { path: "/merchant/products" });
  await page.getByRole("button", { name: "添加商品", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("线上剩余可卖份数")).toHaveValue("");
  await dialog.getByLabel("商品名称").fill("红糖豆浆");
  await dialog.getByLabel("单价（元）").fill("4");
  await dialog.getByLabel("线上剩余可卖份数").fill("0");
  await expect(
    dialog.getByText("当前填了 0 份：可以保存，但顾客暂时不能购买。"),
  ).toBeVisible();
  await dialog
    .getByRole("button", { name: "保存并继续添加", exact: true })
    .click();
  await expect(dialog.getByLabel("商品名称")).toHaveValue("");
  await expect(dialog.getByLabel("线上剩余可卖份数")).toHaveValue("");
  expect(fixture.writes[0]!.body).toMatchObject({
    name: "红糖豆浆",
    stock: 0,
    price_cents: 400,
  });
  await expect(
    page.getByText("商品已保存，当前 0 份；补货后才可购买"),
  ).toBeVisible();
  expect(fixture.unexpected).toEqual([]);
});

test("lost restock result persists across refresh and reuses the exact original batch", async ({
  page,
}) => {
  const fixture = await setup(page, {
    path: "/merchant/products",
    lostRestock: true,
  });
  await page.locator("summary").filter({ hasText: "今天补货" }).click();
  await page.getByLabel("杂粮煎饼本次新增份数").fill("5");
  await page.getByRole("button", { name: "确认本次补货", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "重试确认这批补货" }),
  ).toBeVisible();
  await expect(page.getByLabel("杂粮煎饼本次新增份数")).toBeDisabled();
  await page.reload();
  await expect(page.getByLabel("杂粮煎饼本次新增份数")).toHaveValue("5");
  await expect(page.getByLabel("杂粮煎饼本次新增份数")).toBeDisabled();
  await page.getByRole("button", { name: "重试确认这批补货" }).click();
  await expect(
    page.getByText("本次补货已确认，线上可卖份数已按新增数量更新。"),
  ).toBeVisible();
  expect(fixture.writes[1]!.body).toEqual(fixture.writes[0]!.body);
  expect(fixture.stall.products[0].stock).toBe(15);
  expect(fixture.unexpected).toEqual([]);
});

test("location reports remain unverified and mobile desktop operations fit", async ({
  page,
}, testInfo) => {
  const fixture = await setup(page);
  await page.getByRole("button", { name: "查看找摊反馈", exact: true }).click();
  await expect(page.getByText("1 条找摊反馈待核实")).toBeVisible();
  await expect(
    page.getByText("同学当时看到的位置：同学看到的旧南门位置"),
  ).toBeVisible();
  await expect(
    page.getByText(/声音仅在此页面打开且处于前台时生效/),
  ).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 980 });
    await assertNoHorizontalOverflow(page);
  }
  await page.screenshot({
    path: testInfo.outputPath("merchant-operations-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: testInfo.outputPath("merchant-operations-mobile.png"),
    fullPage: true,
  });
  expect(fixture.unexpected).toEqual([]);
});

test("unknown restock survives a later CSRF failure and never replaces its key", async ({
  page,
}) => {
  const fixture = await setup(page, {
    path: "/merchant/products",
    lostRestock: true,
  });
  await page.locator("summary").filter({ hasText: "今天补货" }).click();
  await page.getByLabel("杂粮煎饼本次新增份数").fill("5");
  await page.getByRole("button", { name: "确认本次补货", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "重试确认这批补货" }),
  ).toBeVisible();
  await page.context().clearCookies();
  fixture.setCsrf("fail");
  await page.getByRole("button", { name: "重试确认这批补货" }).click();
  await expect(
    page.getByRole("button", { name: "重试确认这批补货" }),
  ).toBeEnabled();
  await expect(page.getByLabel("杂粮煎饼本次新增份数")).toBeDisabled();
  expect(fixture.writes).toHaveLength(1);
  fixture.setCsrf("ok");
  await page.getByRole("button", { name: "重试确认这批补货" }).click();
  await expect(
    page.getByText("本次补货已确认，线上可卖份数已按新增数量更新。"),
  ).toBeVisible();
  expect(fixture.writes[1]!.body).toEqual(fixture.writes[0]!.body);
  expect(fixture.stall.products[0].stock).toBe(15);
});

test("account switch during CSRF prevents a restock POST from being sent under the new account", async ({
  page,
}) => {
  const fixture = await setup(page, { path: "/merchant/products" });
  fixture.setCsrf("hold");
  await page.locator("summary").filter({ hasText: "今天补货" }).click();
  await page.getByLabel("杂粮煎饼本次新增份数").fill("5");
  await page.getByRole("button", { name: "确认本次补货", exact: true }).click();
  await expect.poll(() => fixture.reads.includes("/auth/csrf")).toBe(true);
  fixture.user.id = 1880;
  fixture.user.display_name = "另一位商家";
  const refreshed = page.waitForResponse((response) =>
    response.url().endsWith("/auth/me"),
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await refreshed;
  await expect(
    page.getByRole("button", { name: "店铺设置", exact: true }),
  ).toHaveText("另");
  fixture.releaseCsrf();
  await page.locator("summary").filter({ hasText: "今天补货" }).click();
  await expect(page.getByLabel("杂粮煎饼本次新增份数")).toHaveValue("");
  await expect(page.getByLabel("杂粮煎饼本次新增份数")).toBeEnabled();
  expect(fixture.writes).toHaveLength(0);
  expect(fixture.stall.products[0].stock).toBe(10);
});

test("account switch while application waits for CSRF cannot submit the previous form", async ({
  page,
}) => {
  const fixture = await setup(page, {
    ordinary: true,
    path: "/merchant/apply",
  });
  await fillApplication(page);
  fixture.setCsrf("hold");
  await page.getByRole("button", { name: "提交入驻资料", exact: true }).click();
  await expect.poll(() => fixture.reads.includes("/auth/csrf")).toBe(true);
  fixture.user.id = 1880;
  const refreshed = page.waitForResponse((response) =>
    response.url().endsWith("/auth/me"),
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await refreshed;
  await expect(page.getByLabel("经营主体名称")).toHaveValue("");
  fixture.releaseCsrf();
  await expect(
    page.getByRole("button", { name: "提交入驻资料", exact: true }),
  ).toBeEnabled();
  expect(fixture.writes).toHaveLength(0);
  expect(fixture.unexpected).toEqual([]);
});

test("merchant metrics labels returning customers and client source counts honestly", async ({
  page,
}) => {
  const fixture = await setup(page, {
    path: "/merchant/analytics",
    metrics: {
      completed_customer_count: 4,
      returning_customer_count: 2,
      returning_customer_rate: 0.5,
      source_counts: [{ source: "stall_qr", event_type: "qr_open", count: 3 }],
    },
  });
  await expect(
    page.getByRole("heading", { name: "回头客", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".returning-stats")).toContainText("50%");
  await expect(page.locator(".source-counts")).toContainText("二维码链接打开");
  await expect(page.getByText(/次数不代表独立人数或真实扫码量/)).toBeVisible();
  expect(fixture.unexpected).toEqual([]);
});

test("onboarding essential fields and mobile touch targets fit all supported widths", async ({
  page,
}, testInfo) => {
  const fixture = await setup(page, {
    ordinary: true,
    path: "/merchant/apply",
  });
  await expect(page.getByLabel("经营主体名称")).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 980 });
    await assertNoHorizontalOverflow(page);
    expect(
      (await page
        .getByRole("button", { name: "提交入驻资料", exact: true })
        .boundingBox())!.height,
    ).toBeGreaterThanOrEqual(44);
  }
  await page.screenshot({
    path: testInfo.outputPath("merchant-onboarding-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: testInfo.outputPath("merchant-onboarding-mobile.png"),
    fullPage: true,
  });
  expect(fixture.unexpected).toEqual([]);
});
