import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { assertNoHorizontalOverflow, fulfillCsrf } from "../helpers";

// Every API is intercepted, including writes; these flows never use a business DB.
async function fixture(page: Page, options: { map?: boolean; located?: boolean } = {}) {
  const user = { id: 1071, username: "location_flow", is_merchant: true, is_staff: false };
  const stall: any = {
    id: 1071, name: "南门饭摊", merchant_name: "位置流程测试商家", description: "", image: "",
    category: "主食", area_name: "校园南门", area_id: 1, prep_minutes: 10,
    address: options.located ? "南门路口橙色棚" : "位置尚未确认",
    latitude: options.located ? 30 : null, longitude: options.located ? 120 : null,
    location_draft_address: "南门路口橙色棚", status: "closed", session_status: "closed",
    last_confirmed_at: options.located ? "2026-10-07T08:00:00+08:00" : null,
    closes_at: null, products: [], reviews: [], is_visible: true, transaction_enabled: false,
    accepting_orders: true, can_order: false, contact_phone: "", public_phone_enabled: false,
    capabilities: { pickup_orders: { eligible: false, available: false, reason: "仅提供找摊信息服务" } },
    activation: { has_location: !!options.located, steps: [] },
  };
  const state = { stall, writes: [] as { path: string; body: any }[], unexpected: [] as string[] };
  const serializeStall = () => ({ ...stall, opening_closes_at:
    stall.status === "closed" && stall.closes_at && new Date(stall.closes_at).getTime() > Date.now()
      ? stall.closes_at : null });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config") return send({ user, areas: [], demo_mode: false, stale_minutes: 60, amap_key: options.map ? "fixture-map" : "" });
    if (path === "/auth/me") return send(user);
    if (path === "/events") return send({});
    if (path === "/merchant/stalls") return send([serializeStall()]);
    if (path === "/merchant/orders") return send([]);
    if (path === "/merchant/stalls/1071/profile" && request.method() === "PATCH") {
      const body = request.postDataJSON(); state.writes.push({ path, body }); Object.assign(stall, body);
      return send(serializeStall());
    }
    if (path === "/merchant/stalls/1071/status" && request.method() === "POST") {
      const body = request.postDataJSON(); state.writes.push({ path, body });
      if (!stall.activation.has_location && ["address", "latitude", "longitude"].some((key) => !(key in body)))
        return route.fulfill({ status: 400, json: { code: "location_required", detail: "首次设置位置需完整地址和经纬度。" } });
      const startsNewSession = body.status === "open" && (stall.session_status === "closed" ||
        (stall.closes_at && new Date(stall.closes_at).getTime() <= Date.now()));
      Object.assign(stall, body);
      // Match the API: a new session does not silently inherit an old cutoff.
      if (startsNewSession) stall.closes_at = body.closes_at ?? null;
      stall.session_status = body.status;
      if (body.confirm_location) stall.last_confirmed_at = new Date().toISOString();
      if (["address", "latitude", "longitude"].every((key) => key in body)) stall.activation.has_location = true;
      return send(serializeStall());
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 500, json: { detail: "Unexpected isolated request" } });
  });
  return state;
}

async function screenshot(page: Page, name: string) {
  const folder = process.env.LOCATION_AUDIT_DIR;
  if (!folder) return;
  await mkdir(folder, { recursive: true });
  await page.screenshot({ path: `${folder}/merchant-${name}-${process.env.LOCATION_AUDIT_PHASE || "after"}.png`, fullPage: true, animations: "disabled" });
  await page.locator("#location").evaluate((element) => element.scrollIntoView({ block: "start", behavior: "instant" }));
  await page.screenshot({ path: `${folder}/merchant-${name}-viewport-${process.env.LOCATION_AUDIT_PHASE || "after"}.png`, animations: "disabled" });
}

for (const width of [390, 1440]) {
  test(`no map and no coordinates gives a useful address draft path at ${width}px`, async ({ page }) => {
    const state = await fixture(page);
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/merchant/store#location");
    await expect(page.getByLabel("详细取餐地址")).toHaveValue(state.stall.location_draft_address);
    await screenshot(page, `location-unconfigured-${width}`);
    await expect(page.getByRole("button", { name: "获取当前位置", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "在地图上选择位置", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "确认并保存位置与时间", exact: true })).toHaveCount(0);
    const draft = page.getByRole("button", { name: "保存地址草稿", exact: true });
    await expect(draft).toHaveClass(/btn-primary/);
    await page.getByLabel("详细取餐地址").fill("东门入口蓝色棚");
    if (width === 1440) await page.getByLabel("详细取餐地址").press("Enter");
    else await draft.click();
    await expect.poll(() => state.writes.length).toBe(1);
    expect(state.writes[0]).toEqual({ path: "/merchant/stalls/1071/profile", body: { location_draft_address: "东门入口蓝色棚" } });
    expect(state.stall.last_confirmed_at).toBeNull();
    expect(state.stall.status).toBe("closed");
    expect(state.stall.activation.has_location).toBe(false);
    await expect(page.getByText("地址草稿已保存，尚未确认出摊位置", { exact: true })).toBeVisible();
    await assertNoHorizontalOverflow(page);
    expect(state.unexpected).toEqual([]);
  });
}

test("manual coordinates enable explicit confirmation without losing drafts on collapse or refresh", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/store#location");
  await page.getByText("手动填写地图坐标", { exact: true }).click();
  const latitude = page.getByLabel("纬度（高德坐标）", { exact: true });
  const longitude = page.getByLabel("经度（高德坐标）", { exact: true });
  const confirm = page.getByRole("button", { name: "确认并保存位置与时间", exact: true });
  await latitude.fill("30");
  await expect(confirm).toHaveCount(0);
  await longitude.fill("181");
  await expect(confirm).toHaveCount(0);
  await longitude.fill("120");
  await page.getByLabel("详细取餐地址").fill("东门入口蓝色棚");
  await page.getByLabel("预计收摊时间").fill("2026-10-08T21:00");
  await page.locator("#location > summary").click();
  await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
  await page.locator("#location > summary").click();
  await expect(latitude).toHaveValue("30");
  await expect(longitude).toHaveValue("120");
  await expect(page.getByLabel("详细取餐地址")).toHaveValue("东门入口蓝色棚");
  await expect(page.getByLabel("预计收摊时间")).toHaveValue("2026-10-08T21:00");
  expect(state.writes).toEqual([]);
  await screenshot(page, "location-manual-390");
  await confirm.click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]!.body).toMatchObject({ status: "closed", confirm_location: true, address: "东门入口蓝色棚", latitude: 30, longitude: 120 });
  expect(state.stall.last_confirmed_at).not.toBeNull();
  expect(state.stall.status).toBe("closed");
  expect(state.stall.transaction_enabled).toBe(false);
  expect(state.stall.location_draft_address).toBe("南门路口橙色棚");
  expect(state.unexpected).toEqual([]);
});

test("confirmed locations remain confirmable without a map while address drafts never refresh them", async ({ page }) => {
  const state = await fixture(page, { located: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/store#location");
  const originalTime = state.stall.last_confirmed_at;
  await page.getByLabel("详细取餐地址").fill("下次可能搬去东门");
  await page.getByRole("button", { name: "仅保存地址草稿", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.stall.last_confirmed_at).toBe(originalTime);
  expect(state.stall.address).toBe("南门路口橙色棚");
  await page.reload();
  await expect(page.getByLabel("详细取餐地址")).toHaveValue("南门路口橙色棚");
  await screenshot(page, "location-existing-390");
  await page.getByRole("button", { name: "确认并保存位置与时间", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2);
  expect(state.writes[1]!.body).toEqual({ status: "closed", confirm_location: true });
  expect(state.stall.last_confirmed_at).not.toBe(originalTime);
  expect(state.stall.location_draft_address).toBe("下次可能搬去东门");
  expect(state.stall.status).toBe("closed");
  expect(state.unexpected).toEqual([]);
});

test("configured maps keep positioning actions available", async ({ page }) => {
  const state = await fixture(page, { map: true });
  await page.goto("/merchant/store#location");
  await expect(page.getByRole("button", { name: "获取当前位置", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "在地图上选择位置", exact: true })).toBeVisible();
  expect(state.writes).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("invalid coordinates and failed positioning preserve the address draft without confirming a location", async ({ page }) => {
  const state = await fixture(page, { map: true });
  await page.goto("/merchant/store#location");
  await page.getByText("手动填写地图坐标", { exact: true }).click();
  const latitude = page.getByLabel("纬度（高德坐标）", { exact: true });
  const longitude = page.getByLabel("经度（高德坐标）", { exact: true });
  const confirm = page.getByRole("button", { name: "确认并保存位置与时间", exact: true });
  for (const [lat, lng] of [["", "120"], ["30", ""], ["NaN", "120"], ["30", "Infinity"], ["91", "120"], ["-91", "120"], ["30", "181"], ["30", "-181"]]) {
    await latitude.fill(lat!);
    await longitude.fill(lng!);
    await expect(confirm).toHaveCount(0);
  }
  await page.getByLabel("详细取餐地址").fill("东门入口蓝色棚");
  await latitude.fill("30");
  await longitude.fill("");
  // The isolated fixture aborts the map script rather than contacting a map API.
  await page.getByRole("button", { name: "获取当前位置", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("暂时未能定位。可在地图上选点，或先保存地址草稿，请团队协助核实位置。");
  await expect(latitude).toHaveValue("30");
  await expect(longitude).toHaveValue("");
  await expect(page.getByLabel("详细取餐地址")).toHaveValue("东门入口蓝色棚");
  expect(state.writes).toEqual([]);
  await page.getByRole("button", { name: "保存地址草稿", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]).toEqual({ path: "/merchant/stalls/1071/profile", body: { location_draft_address: "东门入口蓝色棚" } });
  expect(state.stall.activation.has_location).toBe(false);
  expect(state.stall.last_confirmed_at).toBeNull();
  expect(state.stall.status).toBe("closed");
  expect(state.unexpected).toEqual([]);
});

test("a pending first location confirmation suppresses repeated submission", async ({ page }) => {
  const state = await fixture(page);
  let submissions = 0;
  let release!: () => void;
  const responseGate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/merchant/stalls/1071/status", async (route) => {
    submissions++;
    await responseGate;
    return route.fallback();
  });
  await page.goto("/merchant/store#location");
  await page.getByText("手动填写地图坐标", { exact: true }).click();
  await page.getByLabel("纬度（高德坐标）", { exact: true }).fill("30");
  await page.getByLabel("经度（高德坐标）", { exact: true }).fill("120");
  const confirm = page.getByRole("button", { name: "确认并保存位置与时间", exact: true });
  await confirm.click();
  await expect.poll(() => submissions).toBe(1);
  await expect(confirm).toBeDisabled();
  await page.getByLabel("详细取餐地址").press("Enter");
  expect(submissions).toBe(1);
  release();
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(confirm).toBeEnabled();
  expect(state.stall.activation.has_location).toBe(true);
  expect(state.stall.status).toBe("closed");
  expect(state.unexpected).toEqual([]);
});

test("a lost first location response can be checked by refresh without a second write", async ({ page }) => {
  const state = await fixture(page);
  await page.route("**/api/v1/merchant/stalls/1071/status", async (route) => {
    const body = route.request().postDataJSON();
    state.writes.push({ path: "/merchant/stalls/1071/status", body });
    Object.assign(state.stall, body, { session_status: body.status, last_confirmed_at: new Date().toISOString() });
    state.stall.activation.has_location = true;
    return route.abort("failed");
  }, { times: 1 });
  await page.goto("/merchant/store#location");
  await page.getByText("手动填写地图坐标", { exact: true }).click();
  await page.getByLabel("纬度（高德坐标）", { exact: true }).fill("30");
  await page.getByLabel("经度（高德坐标）", { exact: true }).fill("120");
  await page.getByRole("button", { name: "确认并保存位置与时间", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText("取餐位置和收摊时间已保存", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
  await expect(page.getByRole("button", { name: "在老地方开摊", exact: true })).toBeVisible();
  await expect(page.getByLabel("详细取餐地址")).toHaveValue("南门路口橙色棚");
  expect(state.writes).toHaveLength(1);
  expect(state.stall.status).toBe("closed");
  expect(state.stall.transaction_enabled).toBe(false);
  expect(state.unexpected).toEqual([]);
});

for (const clockSkewHours of [0, -24, 24]) {
test(`first opening preserves the closing time explicitly saved with the first location (phone clock ${clockSkewHours}h)`, async ({ page }, info) => {
  const state = await fixture(page);
  const serverTime = Date.now();
  await page.clock.setFixedTime(new Date(serverTime + clockSkewHours * 60 * 60 * 1000));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/store#location");
  await page.getByText("手动填写地图坐标", { exact: true }).click();
  await page.getByLabel("纬度（高德坐标）", { exact: true }).fill("30");
  await page.getByLabel("经度（高德坐标）", { exact: true }).fill("120");
  const closing = await page.evaluate((serverNow) => {
    const date = new Date(serverNow + 2 * 60 * 60 * 1000);
    date.setSeconds(0, 0);
    const pad = (value: number) => String(value).padStart(2, "0");
    return { iso: date.toISOString(), local: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}` };
  }, serverTime);
  await page.getByLabel("预计收摊时间").fill(closing.local);
  await page.getByRole("button", { name: "确认并保存位置与时间", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]!.body).toEqual({ status: "closed", confirm_location: true,
    address: "南门路口橙色棚", latitude: 30, longitude: 120, closes_at: closing.iso });
  await expect(page.getByRole("button", { name: "在老地方开摊", exact: true })).toBeVisible();
  expect(state.stall.status).toBe("closed");
  expect(state.stall.closes_at).toBe(closing.iso);
  await page.reload();
  await expect(page.getByLabel("预计收摊时间")).toHaveValue(closing.local);
  await expect(page.getByText(/^本次预计/)).toBeVisible();
  if (clockSkewHours === 0) {
    const operations = page.getByRole("region", { name: "今天出摊", exact: true });
    for (const width of [360, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
      await assertNoHorizontalOverflow(page);
      await operations.screenshot({ path: info.outputPath(`opening-plan-${width}.png`), animations: "disabled" });
    }
    await page.setViewportSize({ width: 390, height: 844 });
  }
  await page.getByRole("button", { name: "在老地方开摊", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2);
  expect(state.writes[1]!.body).toEqual({ status: "open", confirm_location: true, closes_at: closing.iso });
  expect(state.stall.closes_at).toBe(closing.iso);
  expect(state.stall.status).toBe("open");
  expect(state.stall.transaction_enabled).toBe(false);
  expect(state.stall.can_order).toBe(false);
  expect(state.unexpected).toEqual([]);
});
}

test("an expired saved closing time is not carried into opening even when the phone clock is slow", async ({ page }) => {
  const state = await fixture(page, { located: true });
  state.stall.closes_at = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  state.stall.stop_orders_at = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
  await page.clock.setFixedTime(new Date(Date.now() - 24 * 60 * 60 * 1000));
  await page.goto("/merchant/store");
  await expect(page.getByText(/^本次预计/)).toHaveCount(0);
  await page.getByRole("button", { name: "在老地方开摊", exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]!.body).toEqual({ status: "open", confirm_location: true });
  expect(state.stall.closes_at).toBeNull();
  expect(state.stall.status).toBe("open");
  expect(state.unexpected).toEqual([]);
});
