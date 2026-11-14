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
  view_reviews: true,
  manage_reviews: true,
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
  let demoOpenCount = 2;
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
  const reviewRows = [
    {
      id: 701,
      mode: "live",
      order_id: "00000000-0000-0000-0000-000000000701",
      order_number: "YH202610120701",
      order_payment_method: "offline",
      order_total_cents: 800,
      fulfillment_seconds: 25,
      stall_id: 1,
      stall_name: "阿婆葱油饼",
      user_id: 2101,
      username: "student",
      display_name: "测试同学",
      rating: 5,
      content: "好评返现加微领红包",
      created_at: "2026-10-12T11:20:00+08:00",
      merchant_reply: "",
      replied_at: null,
      is_hidden: false,
      moderation_status: "unreviewed",
      moderation_reason: "",
      moderated_at: null,
      moderated_by_username: "",
      pair_7d_count: 3,
      pair_total_count: 3,
      risk_flags: [
        {
          code: "repeat_pair",
          label: "同客同摊高频评价（7天 3 次 / 累计 3 次）",
          tone: "danger",
        },
        {
          code: "rapid_completion",
          label: "极速完单（下单至完成仅 25 秒）",
          tone: "warn",
        },
      ],
      is_flagged: true,
    },
    {
      id: 702,
      mode: "live",
      order_id: "00000000-0000-0000-0000-000000000702",
      order_number: "YH202610120702",
      order_payment_method: "wechat",
      order_total_cents: 1200,
      fulfillment_seconds: 840,
      stall_id: 1,
      stall_name: "阿婆葱油饼",
      user_id: 2199,
      username: "critic_stu",
      display_name: "林同学",
      rating: 1,
      content: "今天排队等了十四分钟，饼稍微有点凉。",
      created_at: "2026-10-12T11:25:00+08:00",
      merchant_reply: "爱吃不吃滚出夜市",
      replied_at: "2026-10-12T11:26:00+08:00",
      is_hidden: false,
      moderation_status: "unreviewed",
      moderation_reason: "",
      moderated_at: null,
      moderated_by_username: "",
      pair_7d_count: 1,
      pair_total_count: 1,
      risk_flags: [
        {
          code: "extreme_low_rating",
          label: "1 星极端差评（若属真实体验请点「复核保留」）",
          tone: "warn",
        },
      ],
      is_flagged: true,
    },
  ];

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
          stalls_open: demoOpenCount,
          stalls_stale: demoOpenCount < 3 ? 1 : 0,
          stalls_trade_eligible: 1,
          demo_stalls_visible: 3,
          demo_stalls_open: demoOpenCount,
          applications_submitted: appRow.status === "submitted" ? 1 : 0,
          applications_needs_changes: 0,
          applications_draft: 0,
          feedback_unresolved: feedbackRow.resolved ? 0 : 1,
          feedback_unreviewed_location: feedbackRow.verification === "unreviewed" ? 1 : 0,
          reviews_total: reviewRows.length,
          reviews_hidden: reviewRows.filter((r) => r.is_hidden).length,
          reviews_flagged: reviewRows.filter((r) => r.is_flagged).length,
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
    if (path === "/ops/demo-stalls/restore" && req.method() === "POST") {
      demoOpenCount = 3;
      return send({ restored_count: 3, stall_ids: [1, 2, 3] });
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
    if (path === "/ops/reviews" && req.method() === "GET") {
      const status = new URL(req.url()).searchParams.get("status") || "all";
      const filtered =
        status === "flagged"
          ? reviewRows.filter((r) => r.is_flagged)
          : status === "hidden"
            ? reviewRows.filter((r) => r.is_hidden)
            : status === "visible"
              ? reviewRows.filter((r) => !r.is_hidden)
              : reviewRows;
      return send({
        counts: {
          all: reviewRows.length,
          flagged: reviewRows.filter((r) => r.is_flagged).length,
          visible: reviewRows.filter((r) => !r.is_hidden).length,
          hidden: reviewRows.filter((r) => r.is_hidden).length,
        },
        stalls: [{ id: 1, name: "阿婆葱油饼" }],
        results: filtered,
      });
    }
    if (path.startsWith("/ops/reviews/") && req.method() === "PATCH") {
      const id = Number(path.split("/").pop());
      const body = req.postDataJSON();
      const target = reviewRows.find((r) => r.id === id)!;
      if (body.action === "hide") {
        target.is_hidden = true;
        target.moderation_status = "hidden";
        target.is_flagged = false;
      } else if (body.action === "clear") {
        target.moderation_status = "cleared";
        target.is_flagged = false;
      } else if (body.action === "clear_reply") {
        target.merchant_reply = "";
        target.replied_at = null;
      }
      target.moderation_reason = body.moderation_reason || "人工复核正常";
      target.moderated_at = "2026-10-12T12:00:00+08:00";
      target.moderated_by_username = "admin";
      return send({ review: target });
    }
    if (path === "/ops/users" && req.method() === "GET") {
      return send({ counts: { all: 2, staff: 1, merchant: 0, student: 1 }, results: [adminAccount, studentAccount] });
    }
    if (path === "/auth/password" && req.method() === "POST") {
      return send({ detail: "密码已更新，其他设备需要重新登录。" });
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

  // Guard against white-on-white invisible button regression outside .o-shell
  const submitBtn = page.getByRole("button", { name: "进入平台管理台" });
  const btnColors = await submitBtn.evaluate((el) => {
    const s = window.getComputedStyle(el);
    return { bg: s.backgroundColor, color: s.color };
  });
  expect(btnColors.bg).toBe("rgb(36, 55, 47)");
  expect(btnColors.color).toBe("rgb(255, 255, 255)");

  // Recovery link on /ops/login returns to /ops/login rather than consumer /login
  await page.getByRole("link", { name: /使用预先保存的恢复码重设/ }).click();
  await expect(page).toHaveURL(/\/recover\?.*role=ops/);
  await page.getByRole("link", { name: "返回登录" }).click();
  await expect(page).toHaveURL(/\/ops\/login/);

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

  // 1-click demo stall recovery on /ops overview
  await expect(page.getByText("2 / 4")).toBeVisible();
  await page.getByRole("button", { name: "恢复示例摊位出摊" }).click();
  await expect(page.locator(".toast")).toContainText("已恢复 3 个示例摊位出摊");
  await expect(page.getByText("3 / 4")).toBeVisible();

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

  // Review anti-brushing & malicious comment governance flow
  await page.goto("/ops/reviews");
  await expect(page.getByRole("heading", { name: "评论治理与防刷评风控" })).toBeVisible();
  await expect(page.getByText("同客同摊高频评价（7天 3 次 / 累计 3 次）")).toBeVisible();

  const brushCard = page.locator(".o-item", { hasText: "好评返现加微领红包" });
  await brushCard.getByRole("button", { name: "同客短时高频极速完单，疑似刷评控分" }).click();
  await brushCard.getByRole("button", { name: "屏蔽违规/刷评" }).click();

  const honestCard = page.locator(".o-item", { hasText: "今天排队等了十四分钟" });
  await honestCard.getByRole("button", { name: "商家回复含辱骂驱赶或暴露隐私等不当言论" }).click();
  await honestCard.getByRole("button", { name: "清理不当商家回复" }).click();
  await expect(honestCard.getByText("爱吃不吃滚出夜市")).toHaveCount(0);
  await honestCard.getByRole("button", { name: "核对订单履约属实，属真实消费体验予以保留" }).click();
  await honestCard.getByRole("button", { name: "复核正常保留" }).click();
  await expect(page.getByRole("button", { name: "待复核风险 (0)" })).toBeVisible();

  // Admin self-service password security tab & responsive check
  await page.goto("/ops/users?tab=security");
  await expect(page.getByRole("heading", { name: "修改当前管理员密码" })).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
  }

  // Session expiry on /ops/* routes to /ops/login rather than consumer /login
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.route("**/api/v1/ops/reviews**", (route) =>
    route.fulfill({
      status: 403,
      json: { detail: "身份认证信息未提供。", code: "not_authenticated" },
    }),
  );
  await page.getByRole("link", { name: "评价与风控" }).click();
  await expect(page).toHaveURL(
    /\/ops\/login\?returnTo=(?:\/|%2F)ops(?:\/|%2F)reviews$/,
  );
});
