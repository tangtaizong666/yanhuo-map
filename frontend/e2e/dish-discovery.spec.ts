import { expect, test } from "@playwright/test";
import { assertNoHorizontalOverflow } from "./helpers";

test("dish search opens the matching item, retains its query on refresh, and handles no matches", async ({
  page,
}, testInfo) => {
  const stalls = await (await page.request.get("/api/v1/stalls")).json();
  const stall = stalls.find((item: any) => item.products.length);
  const product = stall.products[0];
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "搜索摊位或美食" })
    .fill(product.name);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  const results = page.getByRole("region", { name: "餐点搜索结果" });
  const item = results.getByRole("link", {
    name: `查看${product.name}详情，${stall.name}`,
    exact: true,
  });
  await expect(item).toBeVisible();
  await expect(item).toContainText("¥");
  await page.reload();
  await expect(
    page.getByRole("textbox", { name: "搜索摊位或美食" }),
  ).toHaveValue(product.name);
  await item.click();
  await expect(page).toHaveURL(`/stalls/${stall.id}/products/${product.id}`);
  await expect(
    page.getByRole("heading", { name: product.name, exact: true }),
  ).toBeVisible();
  await expect(page.locator('.dish-photo > img')).toHaveJSProperty('complete', true);
  await page.screenshot({ path: testInfo.outputPath('dish-detail-desktop.png'), fullPage: false });
  await page.goto("/search?type=dishes&q=不存在的餐点名称987654321");
  await expect(
    page.getByRole("heading", { name: "还没有找到这道餐点" }),
  ).toBeVisible();
  await expect(page.locator(".dish-card")).toHaveCount(0);
  await expect(page.getByRole('group', { name: '营业状态筛选' })).toBeVisible();
  await page.goto(`/search?type=dishes&q=${encodeURIComponent(product.name)}`);
  await expect(item).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('textbox', { name: '搜索摊位或美食' })).toHaveValue('不存在的餐点名称987654321');
  await expect(page.getByRole('heading', { name: '还没有找到这道餐点' })).toBeVisible();
});

test("home food links and all-meals discovery use real products at four viewport sizes", async ({
  page,
}, testInfo) => {
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    const shelf = page.getByRole("region", { name: "餐点灵感" });
    await expect(shelf.locator(".dish-card").first()).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await expect(page.locator(".stall-menu-peek a").first()).toHaveAttribute(
      "href",
      /\/stalls\/\d+\/products\/\d+$/,
    );
    await page.getByRole("link", { name: "逛逛餐点", exact: true }).click();
    await expect(page).toHaveURL("/search?type=dishes");
    const cards = page
      .getByRole("region", { name: "餐点搜索结果" })
      .locator(".dish-card");
    await expect(cards.first()).toBeVisible();
    const visibleCount = await cards.count();
    const stalls = await (await page.request.get("/api/v1/stalls")).json();
    expect(visibleCount).toBe(
      stalls.reduce((sum: number, item: any) => sum + item.products.length, 0),
    );
    await assertNoHorizontalOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`food-gallery-${width}.png`),
      fullPage: false,
    });
  }
});
