import { expect, test } from "@playwright/test";

for (const width of [360, 390, 768, 1440]) {
  test(`discovery is readable and stays inside a ${width}px viewport`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /附近的烟火气/ }),
    ).toBeVisible();
    await expect(page.locator(".stall-card").first()).toBeVisible();
    await expect(page.getByLabel("选择校园")).toHaveValue(/\d+/);
    await expect(page.locator(".stall-photo img").first()).toHaveJSProperty(
      "complete",
      true,
    );
    expect(
      await page
        .locator(".stall-photo img")
        .first()
        .evaluate((img: HTMLImageElement) => img.naturalWidth),
    ).toBeGreaterThan(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      ),
    ).toBeLessThanOrEqual(1);
    if (width < 768)
      await expect(
        page.getByRole("navigation", { name: "底部导航" }),
      ).toBeVisible();
    else
      await expect(
        page.getByRole("navigation", { name: "主导航", exact: true }),
      ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`home-${width}.png`),
      fullPage: true,
    });
    await page.screenshot({
      path: testInfo.outputPath(`home-${width}-viewport.png`),
      fullPage: false,
    });
    expect(pageErrors).toEqual([]);
  });
}

test("direct stall navigation and product search resolve against live data", async ({
  page,
}) => {
  const response = await page.request.get("/api/v1/stalls");
  expect(response.ok()).toBeTruthy();
  const stalls = (await response.json()).results;
  const stall = stalls.find((candidate: any) => candidate.products.length > 0);
  expect(stall).toBeTruthy();
  await page.goto(`/stalls/${stall.id}`);
  await expect(
    page.getByRole("heading", { name: stall.name, exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText(stall.products[0].name, { exact: true }).first(),
  ).toBeVisible();
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "搜索摊位或美食" })
    .fill(stall.products[0].name);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page).toHaveURL(/\/search/);
  await expect(
    page.locator(".stall-card").filter({ hasText: stall.name }),
  ).toBeVisible();
  await expect(page.locator(".stall-card").first()).not.toContainText(
    /\b\d+m\b/,
  );
});

test("unconfigured map has an honest, usable fallback", async ({ page }) => {
  const config = await (await page.request.get("/api/v1/config")).json();
  test.skip(
    Boolean(config.amap_key),
    "This scenario specifically checks the no-key development environment.",
  );
  await page.goto("/map");
  await expect(page.locator(".stall-card").first()).toBeVisible();
  await expect(
    page.getByText(/未配置|未开放|尚未配置|地图服务尚未/).first(),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "定位我的位置" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看位置与路线" }).first()).toBeVisible();
  const options = await page
    .getByLabel("选择校园")
    .locator("option")
    .allTextContents();
  expect(options.length).toBeGreaterThan(1);
  await page.getByLabel("选择校园").selectOption({ label: options[1]! });
  await expect(page.locator(".map-result-count")).toContainText(options[1]!);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    ),
  ).toBeLessThanOrEqual(1);
});

test("a denied geolocation provider leaves manual campus selection usable", async ({
  page,
}) => {
  // Only the third-party failure is injected; campus/stall data stays on the
  // real API. A live browser permission dialog also requires an actual map key.
  const config = await (await page.request.get("/api/v1/config")).json();
  test.skip(
    Boolean(config.amap_key),
    "This deterministic denial harness is for the development no-key environment.",
  );
  await page.addInitScript(() => {
    (window as any).AMap = {
      Geolocation: class {
        getCurrentPosition(
          callback: (status: string, result: unknown) => void,
        ) {
          callback("error", {
            info: "PERMISSION_DENIED",
            message: "User denied location access.",
          });
        }
      },
    };
  });
  await page.goto("/map");
  await expect(page.locator(".stall-card").first()).toBeVisible();
  await page.locator(".map-location").click();
  await expect(
    page
      .locator(".toast")
      .filter({
        hasText: "未能获取位置，可在上方手动选择校园，继续浏览附近摊位。",
      }),
  ).toBeVisible();
  await expect(page.getByLabel("选择校园")).toBeEnabled();
  await expect(page.locator(".stall-card").first()).toBeVisible();
  await expect(page.locator(".distance")).toHaveCount(0);
});
