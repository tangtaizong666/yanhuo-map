import { test, expect, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

// Every API is intercepted: these layout contracts never touch a business DB.
async function fixture(page: Page) {
  const user = {
    id: 981,
    username: "menu_merchant",
    display_name: "校园饭摊",
    is_merchant: true,
    is_staff: false,
  };
  const product = (id: number, name: string, extra: any = {}) => ({
    id,
    name,
    description: "商家真实填写的菜品介绍",
    category: "主食",
    image: "",
    price_cents: 1200,
    stock: 8,
    stock_version: 3,
    is_active: true,
    sale_paused: false,
    taste_options: [],
    ...extra,
  });
  const products = [
    product(982, "香菇鸡肉饭"),
    product(983, "红豆汤", { stock: 0 }),
    product(984, "招牌蛋炒饭", { sale_paused: true }),
  ];
  const stall = {
    id: 981,
    name: "校园饭摊",
    products,
    reviews: [],
    image: "",
    area_name: "南门",
    address: "南门橙色棚",
    latitude: 30,
    longitude: 120,
    status: "open",
    session_status: "open",
    prep_minutes: 10,
    accepting_orders: true,
    transaction_enabled: true,
    is_visible: true,
    can_order: true,
    contact_phone: "",
    services: { mode: "live" },
    last_confirmed_at: new Date().toISOString(),
    closes_at: new Date(Date.now() + 7200000).toISOString(),
  };
  const state = {
    products,
    patches: [] as any[],
    corrections: [] as any[],
    restocks: [] as any[],
    unexpected: [] as string[],
  };
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname.replace("/api/v1", "");
    const send = (json: any) => route.fulfill({ json });
    if (path === "/auth/csrf") return fulfillCsrf(route);
    if (path === "/config")
      return send({
        user,
        demo_mode: false,
        areas: [],
        amap_key: "",
        stale_minutes: 60,
      });
    if (path === "/auth/me") return send(user);
    if (path === "/events") return send({});
    if (path === "/merchant/stalls") return send([stall]);
    if (path === "/merchant/orders")
      return send({
        results: [],
        next: null,
        counts: { all: 0, active: 0, attention: 0 },
      });
    if (path === "/merchant/metrics")
      return send({
        today: { revenue_cents: 0, orders_created: 0, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    const correction = path.match(
      /^\/merchant\/products\/(\d+)\/stock-correction$/,
    );
    if (correction) {
      const body = request.postDataJSON(),
        row = products.find((p) => p.id === Number(correction[1]))!;
      state.corrections.push(body);
      return send({ product: row, replayed: true });
    }
    if (path === "/merchant/stalls/981/restock") {
      state.restocks.push(request.postDataJSON());
      return send({ replayed: true });
    }
    const patch = path.match(/^\/merchant\/products\/(\d+)$/);
    if (patch && request.method() === "PATCH") {
      const body = request.postDataJSON(),
        row = products.find((p) => p.id === Number(patch[1]))!;
      state.patches.push({ id: row.id, body });
      Object.assign(row, body);
      return send(row);
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({
      status: 500,
      json: { detail: "Unexpected isolated request" },
    });
  });
  return state;
}

function card(page: Page, name: string) {
  return page
    .locator(".merchant-product")
    .filter({ has: page.getByRole("heading", { name, exact: true }) });
}

test("menu cards show readable price, stock and one supply action without inline editing", async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto("/merchant/products");
  const chicken = card(page, "香菇鸡肉饭");
  await expect(chicken).toContainText("¥12");
  await expect(chicken).toContainText("线上可卖 8 份");
  await expect(chicken.locator(".product-status")).toHaveText("销售中");
  await expect(card(page, "红豆汤").locator(".product-status")).toHaveText(
    "已售罄",
  );
  await expect(card(page, "招牌蛋炒饭").locator(".product-status")).toHaveText(
    "暂停供应",
  );
  await expect(chicken.getByRole("spinbutton")).toHaveCount(0);
  await expect(chicken.getByRole("button")).toHaveCount(2);
  await expect(
    chicken.getByRole("button", { name: "编辑商品：香菇鸡肉饭", exact: true }),
  ).toBeVisible();
  await expect(
    chicken.getByRole("button", { name: "暂停供应", exact: true }),
  ).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
    await assertNoHorizontalOverflow(page);
    const box = await chicken
      .getByRole("button", { name: "暂停供应", exact: true })
      .boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    if (width <= 390) {
      const first = await chicken.boundingBox();
      const second = await card(page, "红豆汤").boundingBox();
      const navigation = await page
        .getByRole("navigation", { name: "商家底部导航" })
        .boundingBox();
      expect(first!.height).toBeLessThanOrEqual(165);
      expect(second!.y + second!.height).toBeLessThan(navigation!.y);
      const tabs = await page
        .getByRole("group", { name: "商品状态筛选" })
        .getByRole("button")
        .all();
      const boxes = await Promise.all(tabs.map((tab) => tab.boundingBox()));
      expect(new Set(boxes.map((b) => Math.round(b!.y))).size).toBe(1);
      expect(boxes.every((b) => b!.height >= 44)).toBe(true);
    }
    await page.screenshot({
      path: info.outputPath(`menu-${width}.png`),
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 360, height: 844 });
  const filters = page.getByRole("group", { name: "商品状态筛选" });
  const offShelf = filters.getByRole("button", { name: /^已下架/ });
  await offShelf.click();
  await expect(offShelf).toHaveAttribute("aria-pressed", "true");
  await expect
    .poll(async () => {
      const container = await filters.boundingBox(),
        selected = await offShelf.boundingBox();
      return (
        selected!.x >= container!.x &&
        selected!.x + selected!.width <= container!.x + container!.width + 1
      );
    })
    .toBe(true);
  expect(state.unexpected).toEqual([]);
});

test("editing price and menu visibility preserves current stock and manual supply pause", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant/products");
  const rice = card(page, "招牌蛋炒饭");
  await rice.getByRole("button", { name: /^编辑商品：/ }).click();
  const dialog = page.getByRole("dialog", { name: "编辑商品", exact: true });
  await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("13.50");
  // A concurrent reservation changes stock while the editor is open.
  state.products[2].stock = 6;
  state.products[2].stock_version++;
  await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.patches[0]).toEqual({ id: 984, body: { price_cents: 1350 } });
  await expect(rice).toContainText("线上可卖 6 份");
  await expect(rice.locator(".product-status")).toHaveText("暂停供应");
  await rice.getByRole("button", { name: /^编辑商品：/ }).click();
  await dialog.getByRole("switch", { name: "上架销售", exact: true }).uncheck();
  await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(rice.locator(".product-status")).toHaveText("已下架");
  expect(state.patches[1]).toEqual({ id: 984, body: { is_active: false } });
  expect(state.products[2].stock).toBe(6);
  expect(state.products[2].sale_paused).toBe(true);
  expect(state.unexpected).toEqual([]);
});

test("all editor close controls protect unsaved changes and keep keyboard focus inside the choice", async ({ page }, info) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/products");
  const edit = card(page, "香菇鸡肉饭").getByRole("button", { name: /^编辑商品：/ });
  const dialog = page.getByRole("dialog", { name: "编辑商品", exact: true });
  const warning = page.getByRole("alertdialog", { name: "放弃未保存的修改？", exact: true });
  await edit.click();
  await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("15.50");
  for (const close of [
    () => dialog.getByRole("button", { name: "关闭商品编辑", exact: true }).click(),
    () => dialog.getByRole("button", { name: "取消", exact: true }).click(),
    () => page.keyboard.press("Escape"),
    () => page.mouse.click(10, 10),
  ]) {
    await close();
    await expect(warning).toBeVisible();
    await expect(warning.getByRole("button", { name: "继续编辑" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(warning.getByRole("button", { name: "放弃修改" })).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(warning.getByRole("button", { name: "继续编辑" })).toBeFocused();
    await warning.getByRole("button", { name: "继续编辑", exact: true }).click();
    await expect(warning).toHaveCount(0);
    await expect(dialog.getByRole("spinbutton", { name: "单价（元）" })).toHaveValue("15.5");
  }
  await page.keyboard.press("Escape");
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
    await assertNoHorizontalOverflow(page);
    for (const button of await warning.getByRole("button").all()) {
      expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await expect(button).toBeInViewport({ ratio: 1 });
    }
    await page.screenshot({ path: info.outputPath(`merchant-discard-editor-${width}.png`), fullPage: true });
  }
  await page.keyboard.press("Escape");
  await expect(warning).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await warning.getByRole("button", { name: "放弃修改", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(edit).toBeFocused();
  await edit.click();
  await expect(dialog.getByRole("spinbutton", { name: "单价（元）" })).toHaveValue("12.00");
  await dialog.getByRole("button", { name: "关闭商品编辑", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(warning).toHaveCount(0);
  expect(state.patches).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("saving locks closing and a successful save does not ask to discard", async ({ page }) => {
  const state = await fixture(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let writing = false;
  await page.route("**/api/v1/merchant/products/982", async (route) => {
    writing = true;
    const body = route.request().postDataJSON();
    await gate;
    Object.assign(state.products[0], body);
    state.patches.push({ id: 982, body });
    await route.fulfill({ json: state.products[0] });
  });
  await page.goto("/merchant/products");
  await card(page, "香菇鸡肉饭").getByRole("button", { name: /^编辑商品：/ }).click();
  const dialog = page.getByRole("dialog", { name: "编辑商品", exact: true });
  await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("15.50");
  await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect.poll(() => writing).toBe(true);
  await expect(dialog.getByRole("button", { name: "关闭商品编辑", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "取消", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.mouse.click(10, 10);
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  release();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  expect(state.patches).toEqual([{ id: 982, body: { price_cents: 1550 } }]);
  expect(state.unexpected).toEqual([]);
});

for (const lostResult of ["abort", "5xx"] as const) {
  test(`an applied edit with a lost ${lostResult} response closes with uncertainty and rereads the actual price`, async ({ page }) => {
    const state = await fixture(page);
    let attempts = 0;
    await page.route("**/api/v1/merchant/products/982", async (route) => {
      attempts++;
      if (attempts > 1) return route.fulfill({ status: 403, json: { detail: "本次请求未提交", submitted: false } });
      const body = route.request().postDataJSON();
      Object.assign(state.products[0], body);
      state.patches.push({ id: 982, body });
      if (lostResult === "abort") return route.abort("failed");
      return route.fulfill({ status: 503, json: { detail: "暂时不能核对保存结果" } });
    });
    await page.goto("/merchant/products");
    const chicken = card(page, "香菇鸡肉饭");
    await chicken.getByRole("button", { name: /^编辑商品：/ }).click();
    const dialog = page.getByRole("dialog", { name: "编辑商品", exact: true });
    await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("15.50");
    await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
    await expect(dialog.getByRole("alert")).toContainText("服务器可能已经保存");
    // A definite rejection on a later attempt says nothing about the first write.
    if (lostResult === "abort") {
      await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
      await expect.poll(() => attempts).toBe(2);
      await expect(dialog.getByRole("alert")).toContainText("服务器可能已经保存");
    }
    if (lostResult === "5xx") {
      // Reverting to the old local value does not undo the possibly saved PATCH.
      await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("12");
      await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
    } else await dialog.getByRole("button", { name: "取消", exact: true }).click();
    const warning = page.getByRole("alertdialog", { name: "保存结果尚未确认", exact: true });
    await expect(warning).toContainText("服务器可能已经保存");
    await expect(warning).toContainText("关闭只放弃本地输入");
    await expect(warning).not.toContainText("还没有保存");
    await expect(warning.getByRole("button", { name: "放弃修改", exact: true })).toHaveCount(0);
    const reread = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/merchant/stalls" && response.request().method() === "GET");
    await warning.getByRole("button", { name: "关闭并刷新", exact: true }).click();
    await reread;
    await expect(dialog).toHaveCount(0);
    await expect(chicken.locator(".product-meta > strong")).toHaveText("¥15.5");
    expect(state.patches).toEqual([{ id: 982, body: { price_cents: 1550 } }]);
    expect(attempts).toBe(lostResult === "abort" ? 2 : 1);
    expect(state.unexpected).toEqual([]);
  });
}

test("a conclusively rejected edit still asks only about unsaved local changes", async ({ page }) => {
  const state = await fixture(page);
  await page.route("**/api/v1/merchant/products/982", (route) => route.fulfill({
    status: 400, json: { detail: "本次价格未保存", submitted: false },
  }));
  await page.goto("/merchant/products");
  const chicken = card(page, "香菇鸡肉饭");
  await chicken.getByRole("button", { name: /^编辑商品：/ }).click();
  const dialog = page.getByRole("dialog", { name: "编辑商品", exact: true });
  await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("15.50");
  await dialog.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("本次价格未保存");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  const warning = page.getByRole("alertdialog", { name: "放弃未保存的修改？", exact: true });
  await expect(warning).toContainText("这次填写的内容还没有保存");
  await warning.getByRole("button", { name: "放弃修改", exact: true }).click();
  await expect(chicken).toContainText("¥12");
  expect(state.products[0].price_cents).toBe(1200);
  expect(state.patches).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("new product drafts are protected but closing an unknown creation preserves the original request", async ({ page }) => {
  const state = await fixture(page);
  const bodies: any[] = [];
  await page.route("**/api/v1/merchant/stalls/981/products", async (route) => {
    const body = route.request().postDataJSON();
    bodies.push(body);
    if (bodies.length === 1) return route.abort("failed");
    await route.fulfill({ json: { id: 990, ...body } });
  });
  await page.goto("/merchant/products");
  await page.getByRole("button", { name: "添加商品", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "添加新商品", exact: true });
  await dialog.getByRole("textbox", { name: "商品名称" }).fill("新菜单草稿");
  await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("8");
  await dialog.getByRole("spinbutton", { name: "线上剩余可卖份数" }).fill("4");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  const warning = page.getByRole("alertdialog", { name: "放弃未保存的修改？", exact: true });
  await expect(warning).toBeVisible();
  await warning.getByRole("button", { name: "继续编辑" }).click();
  await dialog.getByRole("button", { name: "添加商品", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("新增结果尚未确认");
  await dialog.getByRole("button", { name: "关闭商品编辑", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(warning).toHaveCount(0);
  const saved = await page.evaluate(() => sessionStorage.getItem("merchant-product-create:981:981"));
  expect(JSON.parse(saved!).body).toEqual(bodies[0]);
  await page.getByRole("button", { name: "确认上一笔新增", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: "商品名称" })).toHaveValue("新菜单草稿");
  await expect(dialog.getByRole("textbox", { name: "商品名称" })).toBeDisabled();
  await dialog.getByRole("button", { name: "确认原新增结果", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
  expect(await page.evaluate(() => sessionStorage.getItem("merchant-product-create:981:981"))).toBeNull();
  expect(state.unexpected).toEqual([]);
});

test("photo uploading locks all closing controls and its unsaved result is protected", async ({ page }) => {
  const state = await fixture(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let uploading = false;
  await page.route("**/api/v1/merchant/stalls/981/image", async (route) => {
    uploading = true;
    await gate;
    await route.fulfill({ json: { url: "/media/merchant-test/photo.png" } });
  });
  await page.goto("/merchant/products");
  await card(page, "香菇鸡肉饭").getByRole("button", { name: /^编辑商品：/ }).click();
  const dialog = page.getByRole("dialog", { name: "编辑商品", exact: true });
  await dialog.locator("summary").filter({ hasText: "照片、分类与介绍" }).click();
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "fixture.png", mimeType: "image/png",
    buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=", "base64"),
  });
  await expect.poll(() => uploading).toBe(true);
  await expect(dialog.getByRole("button", { name: "关闭商品编辑", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "取消", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.mouse.click(10, 10);
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  release();
  await expect(dialog.getByRole("button", { name: "关闭商品编辑", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  const warning = page.getByRole("alertdialog", { name: "放弃未保存的修改？", exact: true });
  await expect(warning).toBeVisible();
  await warning.getByRole("button", { name: "放弃修改" }).click();
  expect(state.products[0].image).toBe("");
  expect(state.patches).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("supply pause is independent of sold-out quantity and never changes stock", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/merchant/products");
  const chicken = card(page, "香菇鸡肉饭"),
    soup = card(page, "红豆汤");
  await chicken.getByRole("button", { name: "暂停供应", exact: true }).click();
  await expect(chicken.locator(".product-status")).toHaveText("暂停供应");
  expect(state.products[0].stock).toBe(8);
  await chicken.getByRole("button", { name: "恢复供应", exact: true }).click();
  await expect(chicken.locator(".product-status")).toHaveText("销售中");
  await soup.getByRole("button", { name: "暂停供应", exact: true }).click();
  await expect(soup.locator(".product-status")).toHaveText("暂停供应");
  await soup.getByRole("button", { name: "恢复供应", exact: true }).click();
  await expect(soup.locator(".product-status")).toHaveText("已售罄");
  expect(state.products[1].stock).toBe(0);
  expect(state.patches.map((p) => p.body)).toEqual([
    { sale_paused: true },
    { sale_paused: false },
    { sale_paused: true },
    { sale_paused: false },
  ]);
  expect(state.unexpected).toEqual([]);
});

test("unconfirmed correction and restock remain automatically visible and reuse original requests", async ({
  page,
}) => {
  const state = await fixture(page);
  const correction = {
    stock: 8,
    expected_stock_version: 2,
    reason: "盘点复核",
    idempotency_key: "menu-correction-original",
  };
  const restock = {
    items: [{ product_id: 984, quantity: 4 }],
    idempotency_key: "menu-restock-original",
  };
  await page.addInitScript(
    ({ correction, restock }) => {
      sessionStorage.setItem(
        "merchant-stock-correction:981:981:982",
        JSON.stringify(correction),
      );
      sessionStorage.setItem(
        "merchant-restock:981:981",
        JSON.stringify(restock),
      );
    },
    { correction, restock },
  );
  await page.goto("/merchant/products");
  const chicken = card(page, "香菇鸡肉饭");
  await expect(
    chicken.getByRole("button", { name: "确认原更正结果", exact: true }),
  ).toBeVisible();
  await expect(chicken.locator(".inventory-correction")).toHaveAttribute(
    "open",
    "",
  );
  await expect(page.locator(".restock")).toHaveAttribute("open", "");
  await chicken
    .getByRole("button", { name: "确认原更正结果", exact: true })
    .click();
  await expect.poll(() => state.corrections.length).toBe(1);
  expect(state.corrections[0]).toEqual(correction);
  await page
    .getByRole("button", { name: "重试确认这批补货", exact: true })
    .click();
  await expect.poll(() => state.restocks.length).toBe(1);
  expect(state.restocks[0]).toEqual(restock);
  expect(state.products[2].sale_paused).toBe(true);
  expect(state.unexpected).toEqual([]);
});
