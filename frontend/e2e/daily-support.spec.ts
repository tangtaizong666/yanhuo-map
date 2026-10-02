import { test, expect, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";
const code = "ABCD-1234-ABCD-1234-ABCD-1234-ABCD-1234";
async function fixture(page: Page) {
  const state = {
    user: {
      id: 96,
      username: "support_student",
      display_name: "支持测试同学",
      is_merchant: false,
      is_staff: false,
    } as any,
    enabled: false,
    writes: [] as any[],
    unknown: false,
    overviewFails: false,
    stalls: [
      {
        id: 91,
        name: "晚课煎饼",
        description: "测试餐点",
        image: "/images/food-jianbing.jpg",
        category: "小吃",
        area_id: 1,
        area_name: "南门",
        address: "南门外绿色棚顶",
        status: "closed",
        usual_hours: "工作日 17:00–21:00",
        can_order: false,
        transaction_enabled: true,
        is_followed: true,
        last_confirmed_at: null,
        products: [],
        reviews: [],
      },
    ] as any[],
    unexpected: [] as string[],
  };
  await page.route("https://**/*", (r) => r.abort());
  await page.route("**/api/v1/**", async (r) => {
    const req = r.request(),
      url = new URL(req.url()),
      path = url.pathname.replace("/api/v1", "");
    const send = (json: any, status = 200) => r.fulfill({ json, status });
    if (path === "/auth/csrf") return fulfillCsrf(r);
    if (path === "/config")
      return send({
        user: state.user,
        demo_mode: true,
        brand: "烟火地图",
        amap_key: "",
        areas: [
          { id: 1, name: "南门", latitude: 31, longitude: 121 },
          { id: 2, name: "北门", latitude: 31, longitude: 121 },
        ],
      });
    if (path === "/auth/me") return send(state.user);
    if (
      path === "/orders/recent-completed" ||
      path === "/orders" ||
      path === "/follows"
    )
      return send([]);
    if (path === "/orders/active-summary")
      return send({
        user_id: state.user?.id,
        order: null,
        counts: { total: 0 },
      });
    if (path === "/events") return send({ ok: true });
    if (path === "/auth/recovery") {
      if (req.method() === "GET") return send({ enabled: state.enabled });
      state.writes.push(req.postDataJSON());
      if (state.unknown) return r.abort("failed");
      state.enabled = req.method() !== "DELETE";
      return send(
        state.enabled
          ? {
              enabled: true,
              recovery_code: code,
              detail: "仅本次显示，请保存。",
            }
          : { enabled: false },
      );
    }
    if (path === "/auth/recovery/reset") {
      state.writes.push(req.postDataJSON());
      if (state.unknown) return r.abort("failed");
      state.user = null;
      return send({
        detail: "密码已重设，其他设备需要重新登录。恢复码已用完。",
      });
    }
    if (path === "/stalls") {
      if (state.overviewFails && !url.searchParams.has("status"))
        return send({ detail: "offline" }, 503);
      return send(
        state.stalls.filter(
          (s) =>
            (!url.searchParams.get("area") ||
              s.area_id === Number(url.searchParams.get("area"))) &&
            (!url.searchParams.get("status") ||
              s.status === url.searchParams.get("status")) &&
            (!url.searchParams.get("category") ||
              s.category === url.searchParams.get("category")),
        ),
      );
    }
    state.unexpected.push(path);
    return send({ detail: path }, 500);
  });
  return state;
}

test("recovery code is shown once, can be saved then hidden, and never persists in browser storage", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto("/account-security");
  await expect(
    page.getByText("尚未设置有效恢复码", { exact: false }),
  ).toBeVisible();
  await page.getByLabel("当前密码", { exact: true }).fill("FixtureSecret!26");
  await page.getByRole("button", { name: "生成恢复码", exact: true }).click();
  await expect(page.getByLabel("新恢复码")).toHaveValue(code);
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await assertNoHorizontalOverflow(page);
    if (width === 390)
      await page.screenshot({
        path: info.outputPath("recovery-mobile.png"),
        fullPage: true,
      });
  }
  expect(
    await page.evaluate(() =>
      JSON.stringify({ ...localStorage, ...sessionStorage }),
    ),
  ).not.toContain(code);
  await page.getByRole("button", { name: "我已安全保存" }).click();
  await expect(page.getByLabel("新恢复码")).toHaveCount(0);
  expect(state.writes).toHaveLength(1);
  expect(state.unexpected).toEqual([]);
});
test("reset validates confirmation, keeps unknown result honest, and offers login after confirmed success", async ({
  page,
}) => {
  const state = await fixture(page);
  state.user = null;
  await page.goto("/recover");
  await page.getByLabel("账号", { exact: true }).fill("support_student");
  await page.getByLabel("恢复码", { exact: true }).fill(code);
  await page.getByLabel("新密码", { exact: true }).fill("NewSecret!2027");
  await page.getByLabel("再次输入新密码").fill("Different!2027");
  await page.getByRole("button", { name: "重设密码", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("不一致");
  expect(state.writes).toHaveLength(0);
  await page.getByLabel("再次输入新密码").fill("NewSecret!2027");
  state.unknown = true;
  await page.getByRole("button", { name: "重设密码", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("结果尚未确认");
  await expect(page.getByRole("link", { name: "使用新密码登录" })).toHaveCount(
    0,
  );
  state.unknown = false;
  await page.getByRole("button", { name: "重设密码", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "使用新密码登录" }),
  ).toBeVisible();
  expect(state.writes).toHaveLength(2);
});
test("merchant security page keeps merchant back link and excludes student navigation", async ({
  page,
}) => {
  const state = await fixture(page);
  state.user.is_merchant = true;
  await page.goto("/account-security");
  await expect(
    page.getByRole("link", { name: "返回", exact: true }),
  ).toHaveAttribute("href", "/merchant");
  await expect(page.getByRole("navigation", { name: "主导航" })).toHaveCount(0);
  await expect(page.getByText("正在预览学生端")).toHaveCount(0);
});
test("unknown code creation does not claim a usable code and refresh allows deliberate rotation", async ({
  page,
}) => {
  const state = await fixture(page);
  state.unknown = true;
  await page.goto("/account-security");
  await page.getByLabel("当前密码", { exact: true }).fill("FixtureSecret!26");
  await page.getByRole("button", { name: "生成恢复码", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("结果尚未确认");
  await expect(page.getByLabel("新恢复码")).toHaveCount(0);
  state.enabled = true;
  await page.getByRole("button", { name: "刷新状态" }).click();
  await expect(
    page.getByRole("button", { name: "生成新码，替换旧码" }),
  ).toBeVisible();
});
test("closed stalls remain useful with planned hours, without advertising current availability", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto("/");
  const empty = page.getByRole("region", { name: "附近出摊提示" });
  await expect(
    empty.getByRole("heading", { name: "小摊们暂时收摊了" }),
  ).toBeVisible();
  await expect(empty).toContainText("工作日 17:00–21:00 · 商家计划");
  await expect(empty).toContainText("已收摊");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 844 });
    await assertNoHorizontalOverflow(page);
    if (width === 390)
      await page.screenshot({
        path: info.outputPath("closed-home-mobile.png"),
        fullPage: true,
      });
  }
  await empty.getByRole("button", { name: "看看全部摊位" }).click();
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(1);
  expect(state.unexpected).toEqual([]);
});
test("unrecorded area differs from closed stalls and can return to all campus areas", async ({
  page,
}) => {
  await fixture(page);
  await page.addInitScript(() => localStorage.setItem("yanhuo-area", "2"));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "这个区域还没有收录摊位" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "换到全校周边" }).click();
  await expect(page.locator(".food-grid .stall-card")).toHaveCount(1);
});
test("stale positions and failed overview are not labeled as closed business", async ({
  page,
}) => {
  const state = await fixture(page);
  state.stalls[0].status = "stale";
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "出摊位置正在等待商家确认" }),
  ).toBeVisible();
  state.stalls[0].status = 'closed';
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('heading', { name: '小摊们暂时收摊了' })).toBeVisible();
  state.overviewFails = true;
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "暂时无法确认附近的出摊情况" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "小摊们暂时收摊了" }),
  ).toHaveCount(0);
});
test("filter excludes existing open stalls without declaring the campus unrecorded", async ({
  page,
}) => {
  const state = await fixture(page);
  state.stalls[0].status = "open";
  await page.goto("/");
  await page.getByRole("button", { name: "喝点什么", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "暂时没有符合筛选条件的摊位" }),
  ).toBeVisible();
});

async function notificationHarness(
  page: Page,
  mode: "granted" | "denied" | "unsupported" = "granted",
) {
  await page.addInitScript((mode) => {
    (window as any).sent = [];
    (window as any).closedNotices = [];
    if (mode === "unsupported") {
      Object.defineProperty(window, "Notification", {
        value: undefined,
        configurable: true,
      });
      return;
    }
    Object.defineProperty(window, "Notification", {
      value: {
        permission: mode === "denied" ? "denied" : "default",
        requestPermission: async () => {
          (window as any).Notification.permission = "granted";
          return "granted";
        },
      },
      configurable: true,
    });
    Object.defineProperty(navigator, "serviceWorker", {
      value: {
        register: async () => ({
          active: { state: "activated" },
          showNotification: async (title: string, options: any) =>
            (window as any).sent.push({ title, options }),
          getNotifications: async () =>
            (window as any).sent.map((n: any) => ({
              tag: n.options.tag,
              close: () => (window as any).closedNotices.push(n.options.tag),
            })),
        }),
      },
      configurable: true,
    });
  }, mode);
  await page.route("**/notification-harness", (r) =>
    r.fulfill({
      contentType: "text/html",
      body: '<div id="app"></div><script type="module">import {createApp,h,ref} from "/node_modules/.vite/deps/vue.js";import Panel from "/src/components/SessionNotifications.vue";const events=ref([{id:"old",title:"历史订单",body:"",url:"/orders/old"}]),user=ref(1);window.updateEvents=(value)=>events.value=value;window.changeUser=()=>user.value=2;createApp({setup:()=>()=>h(Panel,{userId:user.value,events:events.value,ready:true})}).mount("#app");</script>',
    }),
  );
  await page.goto("/notification-harness");
  await page.locator("summary").click();
}
test("system reminders require opt in, ignore initial history, dedupe updates and close on account change", async ({
  page,
}) => {
  await notificationHarness(page);
  expect(await page.evaluate(() => (window as any).sent)).toEqual([]);
  await page.getByRole("button", { name: "开启并发送试提醒" }).click();
  await expect(page.getByText("已开启本次会话系统提醒")).toBeVisible();
  await page.evaluate(() =>
    (window as any).updateEvents([
      { id: "new", title: "新订单", body: "摊位", url: "/orders/new" },
    ]),
  );
  await expect
    .poll(() => page.evaluate(() => (window as any).sent.length))
    .toBe(2);
  await page.evaluate(() =>
    (window as any).updateEvents([
      { id: "new", title: "新订单", body: "摊位", url: "/orders/new" },
    ]),
  );
  expect(await page.evaluate(() => (window as any).sent.length)).toBe(2);
  await page.evaluate(() => (window as any).changeUser());
  await expect
    .poll(() => page.evaluate(() => (window as any).closedNotices.length))
    .toBe(2);
  await expect(
    page.getByRole("button", { name: "开启并发送试提醒" }),
  ).toBeVisible();
});
for (const mode of ["denied", "unsupported"] as const)
  test(`system reminders explain ${mode} and never claim background delivery`, async ({
    page,
  }) => {
    await notificationHarness(page, mode);
    await expect(
      page.getByRole("button", { name: "开启并发送试提醒" }),
    ).toBeDisabled();
    await expect(
      page.getByText("关闭网页、切到后台或锁屏后不保证送达", { exact: false }),
    ).toBeVisible();
    expect(await page.evaluate(() => (window as any).sent)).toEqual([]);
  });
