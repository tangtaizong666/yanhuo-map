import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

const fullPerms = {
  manage_config: true,
  view_applications: true,
  review_applications: true,
  create_assisted_application: true,
  view_merchants: true,
  manage_merchants: true,
  manage_stalls: true,
  view_areas: true,
  add_areas: true,
  manage_areas: true,
  add_delivery_points: true,
  manage_delivery_points: true,
  view_orders: true,
  resolve_payments: true,
  export_reconciliation: true,
  view_feedback: true,
  manage_feedback: true,
  view_users: true,
  manage_users: true,
  view_audit: true,
};

async function mountOpsFixture(page: Page) {
  let currentUser: any = null;
  const studentAccount = {
    id: 2101,
    username: "student",
    display_name: "测试同学",
    is_merchant: false,
    is_staff: false,
  };
  const adminAccount = {
    id: 2102,
    username: "admin",
    display_name: "校园治理员",
    is_merchant: false,
    is_staff: true,
    is_superuser: true,
    ops_permissions: fullPerms,
  };
  const appRow = {
    id: 91,
    status: "submitted",
    source: "self",
    username: "vendor_new",
    display_name: "王姐",
    business_name: "王姐煎饼档",
    stall_name: "王姐杂粮煎饼",
    contact_phone: "13800138999",
    area_id: 1,
    area_name: "南苑生活区",
    category: "小吃",
    address_note: "南苑东侧餐车位",
    description: "现摊杂粮煎饼",
    review_note: "",
    approved_stall_id: null,
    updated_at: "2026-10-12T10:00:00+08:00",
  };
  const feedbackRow = {
    id: 501,
    kind: "wrong_location",
    stall_id: 1,
    stall_name: "阿婆葱油饼",
    current_stall_address: "南苑旧点位",
    order_id: null,
    order_number: "",
    username: "student",
    content: "现场挪到了树荫旁",
    contact: "13900001111",
    location_snapshot: { address: "南苑旧点位" },
    verification: "unreviewed",
    verification_note: "",
    resolved: false,
    created_at: "2026-10-12T11:00:00+08:00",
  };

  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    const send = (json: any, status = 200) => route.fulfill({ status, json });

    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config") {
      return send({
        user: currentUser,
        areas: [{ id: 1, name: "南苑生活区", subtitle: "夜市街区", latitude: 31.23, longitude: 121.47 }],
        demo_mode: true,
        services_simulation_enabled: true,
        brand: "烟火地图",
        stale_minutes: 60,
        amap_key: "",
        amap_proxy: "",
      });
    }
    if (path === "/auth/me") return send(currentUser);
    if (path === "/ops/auth/login" && req.method() === "POST") {
      const body = req.postDataJSON();
      if (body.username === "admin") {
        currentUser = adminAccount;
        return send(currentUser);
      }
      return send(
        {
          detail: "该账号不是平台管理员账号。学生与商家请前往普通登录入口。",
          code: "forbidden",
        },
        403,
      );
    }
    if (path === "/auth/login" && req.method() === "POST") {
      const body = req.postDataJSON();
      if (body.username === "admin") {
        currentUser = adminAccount;
        return send(currentUser);
      }
      currentUser = studentAccount;
      return send(currentUser);
    }
    if (path === "/auth/logout" && req.method() === "POST") {
      currentUser = null;
      return send({ ok: true });
    }
    if (path === "/stalls") return send({ results: [], next: null });
    if (path === "/products") return send({ results: [], next: null });
    if (path === "/events") return send({});

    if (path === "/ops/overview") {
      return send({
        permissions: fullPerms,
        config: { stale_minutes: 60, pending_minutes: 15 },
        runtime: {
          status: "ok",
          workers: [{ name: "expire_orders", healthy: true, seconds_since_success: 8, processed_last_batch: 0, failure_count: 0 }],
          order_expiry: { overdue_count: 0 },
          payments: { reviews_required: 0 },
          notifications: { conflicts: 0, dead: 0 },
        },
        summary: {
          stalls_total: 4,
          stalls_visible: 3,
          stalls_open: 2,
          stalls_stale: 0,
          stalls_trade_eligible: 1,
          applications_submitted: appRow.status === "submitted" ? 1 : 0,
          applications_needs_changes: 0,
          applications_draft: 0,
          feedback_unresolved: feedbackRow.resolved ? 0 : 1,
          feedback_unreviewed_location: feedbackRow.verification === "unreviewed" ? 1 : 0,
          orders_live_active: 1,
          orders_live_attention: 0,
          orders_simulation_active: 0,
          orders_simulation_attention: 0,
          merchants_total: 4,
          merchants_verified: 2,
          users_total: 12,
          staff_total: 1,
        },
        pilot: {
          mode: "live",
          days: 7,
          active_merchant_days: 5,
          completed_orders: 18,
          paying_customers: 11,
          accept_p50_seconds: 95,
          location_reports_received: 1,
          location_reports_confirmed: feedbackRow.verification === "confirmed" ? 1 : 0,
        },
      });
    }
    if (path === "/ops/applications" && req.method() === "GET") {
      return send({
        counts: { all: 1, submitted: appRow.status === "submitted" ? 1 : 0, needs_changes: 0, draft: 0, approved: appRow.status === "approved" ? 1 : 0, rejected: 0 },
        results: [appRow],
      });
    }
    if (path === "/ops/applications/91/review" && req.method() === "POST") {
      const body = req.postDataJSON();
      appRow.status = body.decision;
      appRow.review_note = body.review_note || "";
      appRow.approved_stall_id = 88;
      return send({ application: appRow });
    }
    if (path === "/ops/feedback" && req.method() === "GET") {
      return send({
        counts: { all: 1, unresolved: feedbackRow.resolved ? 0 : 1, unreviewed: feedbackRow.verification === "unreviewed" ? 1 : 0 },
        results: [feedbackRow],
      });
    }
    if (path === "/ops/feedback/501" && req.method() === "PATCH") {
      const body = req.postDataJSON();
      Object.assign(feedbackRow, body);
      return send({ feedback: feedbackRow });
    }
    return send({ detail: `Unhandled ${path}` }, 404);
  });
}

test("dedicated /ops/login rejects non-admin credentials, logs in admin, and fits mobile/desktop viewports", async ({
  page,
}) => {
  await mountOpsFixture(page);

  await page.goto("/ops");
  await expect(page).toHaveURL(/\/ops\/login$/);
  await expect(page.getByRole("heading", { name: "管理员登录", exact: true })).toBeVisible();
  await expect(page.locator(".site-header")).toHaveCount(0);

  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }

  // Non-admin rejected cleanly on /ops/login
  await page.getByLabel("管理员账号").fill("student");
  await page.getByLabel("登录密码").fill("demo12345");
  await page.getByRole("button", { name: "进入平台管理台" }).click();
  await expect(page.getByRole("alert")).toContainText("不是平台管理员账号");
  await expect(page).toHaveURL(/\/ops\/login$/);

  // Admin demo fill and login
  await page.getByRole("button", { name: /填入管理员账号/ }).click();
  await page.getByRole("button", { name: "进入平台管理台" }).click();
  await expect(page).toHaveURL(/\/ops$/);
  await expect(page.getByRole("heading", { name: "治理总览", exact: true })).toBeVisible();

  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }

  // Onboarding review flow
  await page.getByRole("button", { name: "前往入驻审核" }).click();
  await expect(page).toHaveURL(/\/ops\/applications$/);
  await expect(page.getByText("王姐杂粮煎饼")).toBeVisible();
  await page.getByRole("button", { name: "通过初审并建档" }).click();
  await expect(page.getByText("初审已通过")).toBeVisible();

  // Feedback verification flow
  await page.goto("/ops/feedback");
  await page.getByLabel("核实结论").selectOption("confirmed");
  await page.getByLabel(/核实方式与结论依据/).fill("巡查员现场核对照片属实");
  await page.getByLabel("标记该工单已处理完结").check();
  await page.getByRole("button", { name: "保存核实结果" }).click();
  await expect(page.locator(".o-badge", { hasText: "已核实属实" })).toBeVisible();
  await expect(page.locator(".o-badge", { hasText: "已结案" })).toBeVisible();

  // Student preview & return bar
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.getByRole("link", { name: "预览学生端" }).first().click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator(".consumer-preview-bar")).toContainText("正在预览学生端");
  await page.getByRole("link", { name: "返回平台管理台" }).click();
  await expect(page).toHaveURL(/\/ops$/);
});
