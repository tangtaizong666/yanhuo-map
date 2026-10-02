import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { assertNoHorizontalOverflow, baseURL, login, mutate } from "./helpers";

async function openMerchant(
  page: Page,
  context: BrowserContext,
  section: string,
) {
  const configResponse = await context.request.get(`${baseURL}/api/v1/config`);
  expect(configResponse.ok(), await configResponse.text()).toBeTruthy();
  expect(
    (await configResponse.json()).demo_mode,
    "Settings tests only mutate labelled demo data",
  ).toBe(true);
  await page.goto(
    `/login?returnTo=${encodeURIComponent(`/merchant/${section}`)}`,
  );
  await login(page, "vendor", "demo12345");
  await expect(page.locator(".m-stall-bar")).toBeVisible();
  const response = await context.request.get(
    `${baseURL}/api/v1/merchant/stalls`,
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const stalls = await response.json();
  expect(stalls.length).toBeGreaterThan(0);
  return stalls;
}

async function stallSnapshot(context: BrowserContext, id: number) {
  const response = await context.request.get(
    `${baseURL}/api/v1/merchant/stalls`,
  );
  expect(response.ok(), await response.text()).toBeTruthy();
  const stall = (await response.json()).find((entry: any) => entry.id === id);
  expect(stall).toBeTruthy();
  return stall;
}

test("merchant settings preserve a local draft and save only edited fields after another device changes the stall", async ({
  page,
  context,
  browser,
}, testInfo) => {
  await page.setViewportSize({ width: 360, height: 844 });
  const stalls = await openMerchant(page, context, "store");
  const original =
    stalls.find((stall: any) => stall.transaction_enabled) || stalls[0];
  await page.getByLabel("选择管理的摊位").selectOption(String(original.id));
  const otherDevice = await browser.newContext({ baseURL });
  const profilePath = `/merchant/stalls/${original.id}/profile`;
  const statusPath = `/merchant/stalls/${original.id}/status`;
  const draft = `手作好味，认真出摊。跨设备草稿测试 ${Date.now()}`;
  const changedPhone =
    original.contact_phone === "13800000006" ? "13800000007" : "13800000006";
  try {
    const signedIn = await mutate(otherDevice, "/auth/login", {
      username: "vendor",
      password: "demo12345",
    });
    expect(signedIn.ok(), await signedIn.text()).toBeTruthy();
    const opened = await mutate(otherDevice, statusPath, {
      status: "open",
      confirm_location: true,
      closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
    });
    expect(opened.ok(), await opened.text()).toBeTruthy();
    await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
    await expect(page.locator(".m-stall-bar .m-status")).toHaveText("出摊中");
    const before = await stallSnapshot(context, original.id);
    await page
      .locator(".store-details > summary")
      .filter({ hasText: "店铺资料" })
      .click();
    await page
      .locator(".store-details > summary")
      .filter({ hasText: "位置与经营资料" })
      .click();
    const description = page.getByLabel("店铺简介", { exact: true });
    const phone = page.getByLabel("公开联系电话", { exact: true });
    await expect(description).toHaveValue(original.description);
    await description.fill(draft);
    const changed = await mutate(
      otherDevice,
      profilePath,
      { contact_phone: changedPhone },
      "PATCH",
    );
    expect(changed.ok(), await changed.text()).toBeTruthy();
    await page.getByRole("button", { name: "刷新工作台", exact: true }).click();
    await expect(phone).toHaveValue(changedPhone);
    await expect(description).toHaveValue(draft);
    await expect(page.getByLabel("摊位名称", { exact: true })).toHaveValue(
      original.name,
    );
    const saveProfile = page.getByRole("button", {
      name: "保存店铺信息",
      exact: true,
    });
    await expect(saveProfile).toBeEnabled();
    expect((await saveProfile.boundingBox())!.height).toBeGreaterThanOrEqual(
      44,
    );
    await assertNoHorizontalOverflow(page);
    const profileSaved = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().endsWith(profilePath),
      { timeout: 15_000 },
    );
    await saveProfile.click();
    const profileResponse = await profileSaved;
    expect(profileResponse.ok(), await profileResponse.text()).toBeTruthy();
    expect(profileResponse.request().postDataJSON()).toEqual({
      description: draft,
    });
    const afterProfile = await stallSnapshot(context, original.id);
    expect(afterProfile).toMatchObject({
      description: draft,
      contact_phone: changedPhone,
      name: original.name,
      image: original.image,
      prep_minutes: original.prep_minutes,
      address: before.address,
      latitude: before.latitude,
      longitude: before.longitude,
      transaction_enabled: before.transaction_enabled,
    });

    // Only the closing time changes on the same form; position and permission must stay intact.
    const nextClosingTime = await page.evaluate(
      (timestamp) => {
        const date = new Date(timestamp);
        const pad = (value: number) => String(value).padStart(2, "0");
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
      },
      Date.now() + 3 * 60 * 60 * 1000,
    );
    const expectedClosingTime = await page.evaluate(
      (value) => new Date(value).toISOString(),
      nextClosingTime,
    );
    await page
      .getByLabel("预计收摊时间", { exact: true })
      .fill(nextClosingTime);
    const saveLocation = page.getByRole("button", {
      name: "确认并保存位置与时间",
      exact: true,
    });
    await saveLocation.scrollIntoViewIfNeeded();
    await expect(saveLocation).toBeEnabled();
    expect((await saveLocation.boundingBox())!.height).toBeGreaterThanOrEqual(
      44,
    );
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath("merchant-closing-time-360.png"),
    });
    const statusSaved = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(statusPath),
      { timeout: 15_000 },
    );
    await saveLocation.click();
    const statusResponse = await statusSaved;
    expect(statusResponse.ok(), await statusResponse.text()).toBeTruthy();
    expect(statusResponse.request().postDataJSON()).toEqual({
      status: "open",
      confirm_location: true,
      closes_at: expectedClosingTime,
    });
    const afterClosing = await stallSnapshot(context, original.id);
    expect(afterClosing).toMatchObject({
      status: "open",
      address: before.address,
      latitude: before.latitude,
      longitude: before.longitude,
      transaction_enabled: before.transaction_enabled,
      description: draft,
      contact_phone: changedPhone,
    });
    expect(new Date(afterClosing.closes_at).toISOString()).toBe(
      expectedClosingTime,
    );
    await page.reload();
    await expect(description).toHaveValue(draft);
    await expect(phone).toHaveValue(changedPhone);
    await expect(page.getByLabel("预计收摊时间", { exact: true })).toHaveValue(
      nextClosingTime,
    );
  } finally {
    const restoredProfile = await mutate(
      context,
      profilePath,
      {
        description: original.description,
        contact_phone: original.contact_phone,
      },
      "PATCH",
    );
    expect(restoredProfile.ok(), await restoredProfile.text()).toBeTruthy();
    // An elapsed original closing time represents a closed stall even if its session had been open.
    const expiredClosing =
      original.closes_at &&
      new Date(original.closes_at).getTime() <= Date.now();
    const sessionStatus =
      original.session_status ||
      (original.status === "stale" ? "paused" : original.status);
    const restoredStatus = await mutate(context, statusPath, {
      status:
        original.status === "closed" ||
        (sessionStatus === "open" && expiredClosing)
          ? "closed"
          : sessionStatus,
      confirm_location: false,
      closes_at: original.closes_at,
    });
    expect(restoredStatus.ok(), await restoredStatus.text()).toBeTruthy();
    await otherDevice.close();
  }
});

test("merchant analytics exports the selected service mode and keeps simulation receipts out of live totals", async ({
  page,
  context,
}, testInfo) => {
  const stalls = await openMerchant(page, context, "analytics");
  const stall = stalls[0];
  const mode = stall.services.mode;
  expect(["simulation", "live"]).toContain(mode);
  await page.getByLabel("选择管理的摊位").selectOption(String(stall.id));
  const selectedPeriod = page.getByRole("button", {
    name: "近 30 天",
    exact: true,
  });
  await expect(selectedPeriod).toBeEnabled();
  const metricsLoaded = page.waitForResponse(
    (response) => {
      const url = new URL(response.url());
      return (
        response.request().method() === "GET" &&
        url.pathname.endsWith("/merchant/metrics") &&
        url.searchParams.get("stall") === String(stall.id) &&
        url.searchParams.get("days") === "30" &&
        url.searchParams.get("mode") === mode
      );
    },
    { timeout: 15_000 },
  );
  await selectedPeriod.click();
  const response = await metricsLoaded;
  expect(response.ok(), await response.text()).toBeTruthy();
  const metrics = await response.json();
  expect(metrics.mode).toBe(mode);
  expect(metrics.series).toHaveLength(30);
  const otherMode = mode === "simulation" ? "live" : "simulation";
  const [otherResponse, ordersResponse] = await Promise.all([
    context.request.get(
      `${baseURL}/api/v1/merchant/metrics?stall=${stall.id}&days=30&mode=${otherMode}`,
    ),
    context.request.get(`${baseURL}/api/v1/merchant/orders?stall=${stall.id}`),
  ]);
  expect(otherResponse.ok(), await otherResponse.text()).toBeTruthy();
  expect(ordersResponse.ok(), await ordersResponse.text()).toBeTruthy();
  const otherMetrics = await otherResponse.json(),
    orders = await ordersResponse.json();
  expect(otherMetrics.mode).toBe(otherMode);
  const dayKey = (value: string) =>
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(value));
  for (const report of [metrics, otherMetrics]) {
    const dates = new Set(report.series.map((day: any) => day.date));
    const sameMode = orders.filter((order: any) => order.mode === report.mode);
    const received = sameMode.filter(
      (order: any) =>
        ["paid", "refunding", "refunded"].includes(order.payment_status) &&
        order.paid_at &&
        dates.has(dayKey(order.paid_at)),
    );
    expect(report.revenue_cents).toBe(
      received.reduce((sum: number, order: any) => sum + order.total_cents, 0),
    );
    expect(report.online_revenue_cents).toBe(
      received
        .filter((order: any) => order.payment_method === "wechat")
        .reduce((sum: number, order: any) => sum + order.total_cents, 0),
    );
    for (const day of report.series) {
      expect(day.revenue_cents).toBe(
        received
          .filter((order: any) => dayKey(order.paid_at) === day.date)
          .reduce((sum: number, order: any) => sum + order.total_cents, 0),
      );
      expect(day.orders_created).toBe(
        sameMode.filter((order: any) => dayKey(order.created_at) === day.date)
          .length,
      );
      expect(day.orders_completed).toBe(
        sameMode.filter(
          (order: any) =>
            order.status === "completed" &&
            order.completed_at &&
            dayKey(order.completed_at) === day.date,
        ).length,
      );
    }
    for (const payment of report.recent_payments) {
      expect(orders.find((order: any) => order.id === payment.id)?.mode).toBe(
        report.mode,
      );
    }
  }
  const selectedReceiptIds = new Set(
    metrics.recent_payments.map((payment: any) => payment.id),
  );
  expect(
    otherMetrics.recent_payments.every(
      (payment: any) => !selectedReceiptIds.has(payment.id),
    ),
  ).toBe(true);
  await expect(selectedPeriod).toHaveClass(/active/);
  const downloadButton = page.getByRole("button", {
    name: "导出日报",
    exact: true,
  });
  await expect(downloadButton).toBeEnabled();
  const downloading = page.waitForEvent("download");
  await downloadButton.click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe(
    `烟火地图-${mode === "simulation" ? "模拟数据-" : ""}近30天经营日报.csv`,
  );
  const output = testInfo.outputPath("merchant-30-day-report.csv");
  await download.saveAs(output);
  const csv = await readFile(output, "utf8");
  expect(csv.charCodeAt(0)).toBe(0xfeff);
  const rows = csv
    .replace(/^\uFEFF/, "")
    .split(/\r?\n/)
    .map((row) => row.split(","));
  expect(rows[0]).toEqual([
    "日期",
    "下单量",
    "完成量",
    mode === "simulation"
      ? "模拟收款总额（元，无真实资金）"
      : "收款总额（元，退款另计）",
  ]);
  expect(rows.slice(1)).toEqual(
    metrics.series.map((day: any) => [
      day.date,
      String(day.orders_created),
      String(day.orders_completed),
      (day.revenue_cents / 100).toFixed(2),
    ]),
  );
  expect(
    rows
      .slice(1)
      .reduce((total, row) => total + Math.round(Number(row[3]) * 100), 0),
  ).toBe(metrics.revenue_cents);
  await page.getByText("查看每日明细", { exact: true }).click();
  await expect(page.locator(".m-data-table tbody tr")).toHaveCount(30);
  for (const width of [360, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await assertNoHorizontalOverflow(page);
    await expect(downloadButton).toBeEnabled();
  }
});
