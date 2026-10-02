import { test, expect, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Every business request is intercepted. These tests never mutate the shared demo.
async function fixture(page: Page, publicBase = "") {
  const state = {
    feedback: [] as any[],
    events: [] as any[],
    failFeedback: false,
    forbiddenFeedback: false,
    stall: {
      id: 831,
      name: "南门煎饼",
      description: "现做的热乎煎饼",
      category: "小吃",
      image: "/images/food-jianbing.jpg",
      address: "南门外第二棵树旁",
      arrival_note: "认准绿色棚顶，面向南门右手边。",
      arrival_image: "",
      accepting_orders: false,
      order_unavailable_reason: "商家忙碌，暂时停止线上接单，可线下到访。",
      latitude: 45.752,
      longitude: 126.632,
      area_id: 1,
      area_name: "示例校园",
      status: "open",
      last_confirmed_at: new Date(Date.now() - 120000).toISOString(),
      closes_at: new Date(Date.now() + 3600000).toISOString(),
      prep_minutes: 10,
      transaction_enabled: true,
      can_order: false,
      qualification_note: "示例资料",
      merchant_name: "示例商家",
      contact_phone: "13800138000",
      rating: null,
      review_count: 0,
      order_count: 0,
      distance_m: null,
      is_followed: false,
      products: [
        {
          id: 832,
          name: "原味煎饼",
          description: "",
          image: "/images/food-jianbing.jpg",
          price_cents: 800,
          stock: 8,
          is_active: true,
        },
      ],
      reviews: [],
      wechat_payment: {
        mode: "simulation",
        available: false,
        channels: [],
        reason: "未开放",
      },
    },
  };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname.replace("/api/v1", "");
    const send = (json: any, status = 200) => route.fulfill({ json, status });
    if (path === "/config")
      return send({
        user: null,
        demo_mode: true,
        services_simulation_enabled: true,
        public_base_url: publicBase,
        amap_key: "",
        areas: [
          { id: 1, name: "示例校园", latitude: 45.752, longitude: 126.632 },
        ],
        stale_minutes: 60,
      });
    if (path === "/auth/me") return send(null);
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/stalls/831") return send(state.stall);
    if (path === "/stalls") return send([state.stall]);
    if (path === "/events") {
      state.events.push(req.postDataJSON());
      return send({ ok: true }, 201);
    }
    if (path === "/feedback") {
      state.feedback.push(req.postDataJSON());
      if (state.failFeedback) return route.abort("failed");
      if (state.forbiddenFeedback)
        return send({ detail: "安全校验未通过" }, 403);
      return send({ ok: true }, 201);
    }
    return send({ detail: `Unexpected ${req.method()} ${path}` }, 500);
  });
  return state;
}

test("guest location report captures displayed context, recovers an unknown result using the same request, and never changes trading state", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/stalls/831");
  const visit = page.getByRole("region", { name: "到摊指引" });
  await expect(visit).toContainText("认准绿色棚顶");
  await expect(visit).toContainText("暂停线上接单");
  await visit.getByRole("button", { name: /没找到摊位/ }).click();
  const dialog = page.getByRole("dialog", { name: /没找到 南门煎饼/ });
  await dialog.getByLabel("位置不对", { exact: true }).check();
  await dialog.getByLabel("补充说明（选填）").fill("到南门没有找到绿色棚顶");
  await dialog.getByLabel("联系方式（选填）").fill("测试联系信息");
  const displayedAddress = state.stall.address;
  state.stall.address = "商家后续修改的地址";
  state.failFeedback = true;
  await dialog
    .getByRole("button", { name: "提交位置反馈", exact: true })
    .click();
  await expect(dialog).toContainText("提交结果尚未确认");
  await expect(dialog.getByLabel("位置不对", { exact: true })).toBeDisabled();
  state.failFeedback = false;
  state.forbiddenFeedback = true;
  await dialog
    .getByRole("button", { name: "确认原反馈结果", exact: true })
    .click();
  await expect(dialog).toContainText("提交结果尚未确认");
  await expect(dialog.getByLabel("位置不对", { exact: true })).toBeDisabled();
  state.forbiddenFeedback = false;
  await dialog
    .getByRole("button", { name: "确认原反馈结果", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "反馈已收到", exact: true }),
  ).toBeVisible();
  expect(state.feedback).toHaveLength(3);
  expect(state.feedback[1]).toEqual(state.feedback[0]);
  expect(state.feedback[2]).toEqual(state.feedback[0]);
  expect(state.feedback[0]).toMatchObject({
    stall_id: 831,
    kind: "wrong_location",
    location_snapshot: { address: displayedAddress },
  });
  expect(state.feedback[0].idempotency_key.length).toBeGreaterThan(8);
  expect(state.stall.status).toBe("open");
  expect(state.stall.accepting_orders).toBe(false);
});

test("blank optional fields are valid and copy fallback gives a selectable address on insecure browsers", async ({
  page,
}) => {
  await fixture(page);
  await page.addInitScript(() =>
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    }),
  );
  await page.goto("/stalls/831");
  const visit = page.getByRole("region", { name: "到摊指引" });
  await visit.getByRole("button", { name: "复制地址", exact: true }).click();
  await expect(visit.getByLabel("可复制的摊位地址")).toHaveValue(
    /南门外第二棵树旁.*绿色棚顶/,
  );
  await visit.getByRole("button", { name: /没找到摊位/ }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "提交位置反馈", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("反馈已收到");
});

test("public sharing uses the configured domain without session or order secrets and counts entry separately", async ({
  page,
}) => {
  const state = await fixture(page, "https://campus.example.com");
  await page.goto("/stalls/831?src=stall_qr");
  await expect
    .poll(() => state.events.filter((event) => event.type === "qr_open").length)
    .toBe(1);
  await page.getByRole("button", { name: "分享小摊", exact: true }).click();
  const share = page.getByRole("dialog", {
    name: "把好味道分享出去",
    exact: true,
  });
  await expect(share.getByLabel("公开摊位链接")).toHaveValue(
    "https://campus.example.com/stalls/831?src=share_link",
  );
  await expect(
    share.getByRole("img", { name: "南门煎饼的公开摊位二维码" }),
  ).toBeVisible();
  await expect(share.getByRole("link", { name: "保存二维码" })).toHaveAttribute(
    "download",
    "烟火地图-摊位831.png",
  );
  await expect(share).not.toContainText("仅同网络");
  expect(state.events.find((event) => event.type === "qr_open")).toEqual({
    type: "qr_open",
    stall_id: 831,
    source: "stall_qr",
  });
  expect(
    state.events.filter((event) => event.type === "share_click"),
  ).toHaveLength(0);
});

test("local QR explicitly warns about reachability and the dialog fits mobile and desktop", async ({
  page,
}, info) => {
  await fixture(page);
  await page.goto("/stalls/831");
  await page.getByRole("button", { name: "分享小摊", exact: true }).click();
  const share = page.getByRole("dialog", {
    name: "把好味道分享出去",
    exact: true,
  });
  await expect(share).toContainText("仅当前电脑可打开");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    const box = await share.boundingBox();
    expect(box!.width).toBeLessThanOrEqual(width - 20);
    await expect(share.getByRole("button", { name: "复制链接" })).toBeVisible();
    if (width === 390)
      await page.screenshot({ path: info.outputPath("share-mobile.png") });
  }
});

test("arrival guidance stays honest without coordinates or photos, includes report and phone fallback", async ({
  page,
}, info) => {
  const state = await fixture(page);
  Object.assign(state.stall, {
    latitude: null,
    longitude: null,
    status: "stale",
    last_confirmed_at: null,
    arrival_note: "",
    arrival_image: "",
  });
  await page.goto("/stalls/831");
  const visit = page.getByRole("region", { name: "到摊指引" });
  await expect(visit).toContainText("商家尚未确认位置");
  await expect(visit).toContainText("位置已过期");
  await expect(visit.getByRole("link", { name: "查看路线" })).toHaveCount(0);
  await expect(visit.locator("img")).toHaveCount(0);
  await expect(visit.getByRole("link", { name: "联系商家" })).toHaveAttribute(
    "href",
    "tel:13800138000",
  );
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    if (width === 390 || width === 1440)
      await visit.screenshot({ path: info.outputPath(`visit-${width}.png`) });
  }
});

test("new merchant application entry is discoverable without changing the student navigation", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/stalls/831");
  await expect(
    page.getByRole("link", { name: "我是商家，申请入驻" }),
  ).toHaveAttribute("href", "/merchant/apply");
  await page.goto("/login?role=merchant&returnTo=%2Fmerchant%2Fapply");
  await expect(
    page.getByRole("button", { name: "注册并申请", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "注册并申请", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "创建账号，申请开摊" }),
  ).toBeVisible();
  await expect(page.getByRole("navigation", { name: "底部导航" })).toHaveCount(
    0,
  );
});
