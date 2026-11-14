import { expect, test } from "@playwright/test";
import { assertNoHorizontalOverflow, requireIsolatedApi } from "./helpers";

test.beforeEach(() => requireIsolatedApi());

test("real admin login, governance modules, audit trail and student preview isolation", async ({
  page,
}) => {
  await page.goto("/ops");
  await expect(page).toHaveURL(/\/ops\/login$/);

  // Student account rejected at dedicated admin login
  await page.getByLabel("管理员账号").fill("student");
  await page.getByLabel("登录密码").fill("demo12345");
  await page.getByRole("button", { name: "进入平台管理台" }).click();
  await expect(page.getByRole("alert")).toContainText("不是平台管理员账号");

  // Demo admin logs into /ops
  await page.getByRole("button", { name: /填入管理员账号/ }).click();
  await page.getByRole("button", { name: "进入平台管理台" }).click();
  await expect(page).toHaveURL(/\/ops$/);
  await expect(
    page.getByRole("heading", { name: "治理总览", exact: true }),
  ).toBeVisible();

  // Pure admin visiting / or /merchant stays isolated in /ops unless previewing
  await page.goto("/");
  await expect(page).toHaveURL(/\/ops$/);
  await page.goto("/merchant");
  await expect(page).toHaveURL(/\/ops$/);

  // Update global threshold and verify audit log records it
  await page
    .getByLabel("位置过期停单分钟数（1 - 1440）")
    .fill("55");
  await page.getByRole("button", { name: "保存全局配置" }).click();
  await expect(page.locator(".toast")).toContainText("已保存");

  // Visit stalls, areas, orders, users & audit log
  for (const [sec, heading] of [
    ["stalls", "商户与摊位"],
    ["areas", "区域与交接点"],
    ["orders", "订单与对账"],
    ["reviews", "评价与风控"],
    ["users", "账号与审计"],
  ] as const) {
    await page.goto(`/ops/${sec}`);
    await expect(
      page.getByRole("heading", { name: heading, exact: true }),
    ).toBeVisible();
    for (const width of [360, 768, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await assertNoHorizontalOverflow(page);
    }
  }

  await page.getByRole("button", { name: "操作审计日志" }).click();
  await expect(
    page.locator(".o-badge", { hasText: "ops_site_config_updated" }).first(),
  ).toBeVisible();

  // Restore default stale_minutes so shared demo state stays clean
  await page.goto("/ops");
  await page
    .getByLabel("位置过期停单分钟数（1 - 1440）")
    .fill("60");
  await page.getByRole("button", { name: "保存全局配置" }).click();
  await expect(page.locator(".toast")).toContainText("已保存");

  // Explicit consumer preview & return to /ops
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.getByRole("link", { name: "预览学生端" }).first().click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator(".consumer-preview-bar")).toContainText(
    "返回平台管理台",
  );
  await page.getByRole("link", { name: "返回平台管理台" }).click();
  await expect(page).toHaveURL(/\/ops$/);
});
