import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Every API request is intercepted. These tests never change live merchant data.
async function setup(page: Page) {
  let user = {
    id: 996,
    username: "draft_fixture",
    display_name: "隔离商家",
    is_merchant: true,
    is_staff: false,
  };
  const point = {
    id: 96,
    name: "模拟南门交接点",
    address: "示例校园南门雨棚",
    area_id: 1,
  };
  const make = (fee = 200) => ({
    mode: "simulation",
    enabled: true,
    approved: false,
    available: true,
    reason: "",
    fee_cents: fee,
    min_order_cents: 0,
    capacity: 5,
    starts_at: "00:00",
    ends_at: "23:59",
    eta_min_minutes: 20,
    eta_max_minutes: 40,
    point_ids: [96],
    points: [point],
    available_points: [point],
  });
  const settings: Record<number, ReturnType<typeof make>> = {
    996: make(),
    997: make(900),
  };
  const timestamp = new Date().toISOString();
  const writes: { id: number; body: any }[] = [];
  const unexpected: string[] = [];
  let paused = false;
  let release: (() => void) | undefined;
  const services = (id: number) => ({
    mode: "simulation",
    simulation_available: true,
    online_payment_enabled: true,
    delivery_enabled: settings[id]!.enabled,
    wechat_payment: { mode: "simulation", available: true },
    delivery: settings[id],
  });
  const stall = (id: number) => ({
    id,
    name: `隔离测试摊 ${id}`,
    image: "/images/food-cold-noodles.jpg",
    area_name: "示例校园",
    status: "open",
    last_confirmed_at: timestamp,
    transaction_enabled: true,
    prep_minutes: 10,
    address: "示例取餐点",
    latitude: 30,
    longitude: 120,
    description: "",
    contact_phone: "",
    closes_at: "",
    merchant_name: "隔离商户",
    services: services(id),
    delivery: settings[id],
    products: [],
    reviews: [],
  });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname.replace("/api/v1", "");
    const send = (body: any) => route.fulfill({ json: body });
    if (path === "/config")
      return send({
        brand: "烟火地图",
        demo_mode: true,
        services_simulation_enabled: true,
        user,
        areas: [],
        amap_key: "",
        amap_proxy: "",
      });
    if (path === "/auth/me") return send(user);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/merchant/stalls") return send([stall(996), stall(997)]);
    if (path === "/merchant/orders")
      return send({
        results: [],
        next: null,
        counts: {
          all: 0,
          active: 0,
          followup: 0,
          attention: 0,
          completed: 0,
          cancelled: 0,
        },
      });
    if (path === "/merchant/metrics")
      return send({
        mode: "simulation",
        today: { revenue_cents: 0, orders_created: 0, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    const match = path.match(
      /^\/merchant\/stalls\/(996|997)\/(services|delivery)$/,
    );
    if (match) {
      const id = Number(match[1]);
      if (match[2] === "services") {
        if (req.method() === "PATCH")
          settings[id] = {
            ...settings[id]!,
            enabled: req.postDataJSON().delivery_enabled,
          };
        return send(services(id));
      }
      if (req.method() === "PATCH") {
        const body = req.postDataJSON();
        writes.push({ id, body });
        if (paused)
          await new Promise<void>((resolve) => {
            release = resolve;
          });
        settings[id] = { ...settings[id]!, ...body };
      }
      return send(settings[id]);
    }
    unexpected.push(`${req.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  await page.goto("/merchant/store");
  await page.locator('.store-services > summary').click();
  await expect(page.getByLabel("配送费（元）", { exact: true })).toHaveValue(
    "2",
  );
  return {
    settings,
    writes,
    unexpected,
    hold: () => {
      paused = true;
    },
    release: () => {
      paused = false;
      release?.();
    },
    changeAccount: () => {
      user = { ...user, id: 1996, display_name: "另一商家账号" };
    },
  };
}
const fee = (page: Page) => page.getByLabel("配送费（元）", { exact: true });
const save = (page: Page) =>
  page.getByRole("button", { name: "保存配送设置", exact: true });
async function refresh(page: Page) {
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/merchant/stalls/996/services") &&
      r.request().method() === "GET",
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await response;
}

test("remote settings merge untouched fields, preserve drafts and save only changed fields", async ({
  page,
}, testInfo) => {
  const fixture = await setup(page);
  await fee(page).fill("3");
  fixture.settings[996] = {
    ...fixture.settings[996]!,
    fee_cents: 500,
    capacity: 2,
    starts_at: "09:00",
  };
  await refresh(page);
  await expect(fee(page)).toHaveValue("3");
  await expect(page.locator(".delivery-sync")).toContainText(
    "其他设备更新了配送费",
  );
  await expect(page.locator(".delivery-advanced > summary")).toContainText(
    "同时 2 单",
  );
  await expect(page.locator(".delivery-advanced > summary")).toContainText(
    "09:00",
  );
  await expect(
    page.getByLabel("同时配送容量（单）", { exact: true }),
  ).toBeHidden();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }
  await page
    .getByRole("region", { name: "经营服务", exact: true })
    .screenshot({ path: testInfo.outputPath("merchant-draft-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".merchant-delivery-settings").evaluate((element) =>
    window.scrollTo({
      top: element.getBoundingClientRect().top + window.scrollY - 84,
      behavior: "instant",
    }),
  );
  await page.screenshot({
    path: testInfo.outputPath("merchant-draft-mobile.png"),
  });
  await save(page).click();
  await expect
    .poll(() => fixture.writes)
    .toEqual([{ id: 996, body: { fee_cents: 300 } }]);
  await expect(page.locator(".delivery-sync")).toContainText("当前设置已保存");
  expect(fixture.settings[996]!.capacity).toBe(2);
  expect(fixture.settings[996]!.starts_at).toBe("09:00");
  expect(fixture.unexpected).toEqual([]);
});

test("unedited fields follow another device and an unchanged save does not write", async ({
  page,
}) => {
  const fixture = await setup(page);
  fixture.settings[996] = {
    ...fixture.settings[996]!,
    fee_cents: 500,
    capacity: 2,
  };
  await refresh(page);
  await expect(fee(page)).toHaveValue("5");
  await expect(page.locator(".delivery-sync")).toContainText(
    "已同步最新配送设置",
  );
  await fee(page).fill("5.00");
  await save(page).click();
  await expect(
    page.getByText("没有需要保存的配送修改", { exact: true }),
  ).toBeVisible();
  expect(fixture.writes).toEqual([]);
});

test("changing fee cannot silently reopen delivery paused on another device", async ({
  page,
}) => {
  const fixture = await setup(page);
  await fee(page).fill("3");
  // The remote toggle is newer than this page's last poll.
  fixture.settings[996] = {
    ...fixture.settings[996]!,
    enabled: false,
    available: false,
  };
  await save(page).click();
  await expect.poll(() => fixture.writes.length).toBe(1);
  expect(fixture.writes[0]!.body).toEqual({ fee_cents: 300 });
  expect(fixture.settings[996]!.enabled).toBe(false);
  await expect(
    page.getByRole("switch", { name: "外卖配送", exact: true }),
  ).not.toBeChecked();
});

test("service switches wait for a pending delivery save and draft survives remote pause", async ({
  page,
}) => {
  const fixture = await setup(page);
  await fee(page).fill("3");
  fixture.settings[996] = {
    ...fixture.settings[996]!,
    enabled: false,
    available: false,
  };
  await refresh(page);
  await expect(fee(page)).toBeHidden();
  fixture.settings[996] = {
    ...fixture.settings[996]!,
    enabled: true,
    available: true,
  };
  await refresh(page);
  await expect(fee(page)).toHaveValue("3");
  fixture.hold();
  await save(page).click();
  await expect.poll(() => fixture.writes.length).toBe(1);
  await expect(
    page.getByRole("switch", { name: "线上支付", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("switch", { name: "外卖配送", exact: true }),
  ).toBeDisabled();
  fixture.release();
  await expect(
    page.getByRole("switch", { name: "外卖配送", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".delivery-sync")).toContainText("当前设置已保存");
});

test("late save from a previous stall cannot replace the selected stall's form", async ({
  page,
}) => {
  const fixture = await setup(page);
  fixture.hold();
  await fee(page).fill("3");
  await save(page).click();
  await expect.poll(() => fixture.writes.length).toBe(1);
  await page.getByLabel("选择管理的摊位", { exact: true }).selectOption("997");
  await page.locator('.store-services > summary').click();
  await expect(fee(page)).toHaveValue("9");
  fixture.release();
  await expect(fee(page)).toHaveValue("9");
  await expect(page.locator(".delivery-sync")).toContainText("当前设置已保存");
  expect(fixture.writes.map((w) => w.id)).toEqual([996]);
});

test("account change discards the previous account's unsaved draft", async ({
  page,
}) => {
  const fixture = await setup(page);
  await fee(page).fill("7");
  fixture.changeAccount();
  const changedIdentity = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/auth/me",
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  expect((await (await changedIdentity).json()).id).toBe(1996);
  await expect(page.locator('.store-services')).not.toHaveAttribute('open');
  await page.locator('.store-services > summary').click();
  await expect(fee(page)).toHaveValue("2");
  await expect(page.locator(".delivery-sync")).toContainText("当前设置已保存");
  expect(fixture.writes).toEqual([]);
});
