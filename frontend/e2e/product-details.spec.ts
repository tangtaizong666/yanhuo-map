import { expect, test, type BrowserContext } from "@playwright/test";
import {
  assertNoHorizontalOverflow,
  baseURL,
  createStudent,
  login,
  mutate,
} from "./helpers";

let merchant: BrowserContext;
let stall: any;
let product: any;
let soldOut: any;

test.beforeAll(async ({ browser }) => {
  merchant = await browser.newContext({ baseURL });
  const config = await (
    await merchant.request.get(`${baseURL}/api/v1/config`)
  ).json();
  expect(
    config.demo_mode,
    "Dish fixtures may only be created in the demo environment",
  ).toBe(true);
  const signedIn = await mutate(merchant, "/auth/login", {
    username: "vendor",
    password: "demo12345",
  });
  expect(signedIn.ok(), await signedIn.text()).toBeTruthy();
  const stalls = await (
    await merchant.request.get(`${baseURL}/api/v1/merchant/stalls`)
  ).json();
  stall = stalls.find((item: any) => item.transaction_enabled);
  expect(stall).toBeTruthy();
  const opened = await mutate(merchant, `/merchant/stalls/${stall.id}/status`, {
    status: "open",
    confirm_location: true,
    closes_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
  });
  expect(opened.ok(), await opened.text()).toBeTruthy();
  for (const [name, stock] of [
    ["详情测试餐点", 3],
    ["售罄测试餐点", 0],
  ] as const) {
    const created = await mutate(
      merchant,
      `/merchant/stalls/${stall.id}/products`,
      {
        name: `${name}-${Date.now()}`,
        description: "详情页自动化验证：现点制作，具体口味请向商家确认。",
        category: "测试小食",
        price_cents: 850,
        stock,
        image: "/images/food-pancake.jpg",
        is_active: true,
      },
    );
    expect(created.status(), await created.text()).toBe(201);
    if (stock) product = await created.json();
    else soldOut = await created.json();
  }
});

test.afterAll(async () => {
  for (const item of [product, soldOut]) {
    if (!item || !merchant) continue;
    const response = await mutate(
      merchant,
      `/merchant/products/${item.id}`,
      { is_active: false },
      "PATCH",
    );
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  await merchant?.close();
});

test("menu photos and names open a refreshable detail page at mobile and desktop widths", async ({
  page,
}, testInfo) => {
  await page.goto(`/stalls/${stall.id}`);
  const row = page
    .locator("article.product-row")
    .filter({ hasText: product.name });
  // The image, description and title form one large link; quantity buttons remain separate.
  const photo = row.locator(".product-image");
  await photo.scrollIntoViewIfNeeded();
  const photoBox = await photo.boundingBox();
  expect(photoBox).not.toBeNull();
  // Click the visible photo area, including the deliberately stretched link target.
  await page.mouse.click(
    photoBox!.x + photoBox!.width / 2,
    photoBox!.y + photoBox!.height / 2,
  );
  await expect(page).toHaveURL(
    new RegExp(`/stalls/${stall.id}/products/${product.id}$`),
  );
  await expect(
    page.getByRole("heading", { name: product.name, exact: true }),
  ).toBeVisible();
  await expect(page.locator(".dish-description")).toHaveText(
    product.description,
  );
  await expect(page.locator(".dish-price-line strong")).toHaveText("¥8.5");
  await expect(page.locator(".dish-location-fact")).toContainText(stall.address);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: product.name, exact: true }),
  ).toBeVisible();
  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await assertNoHorizontalOverflow(page);
    await expect(page.getByRole("button", { name: /加入餐袋/ })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`dish-${width}.png`),
      fullPage: true,
    });
  }
  await page.getByRole("link", { name: "返回小摊", exact: true }).click();
  await page
    .getByRole("link", { name: `查看${product.name}详情`, exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: product.name, exact: true }),
  ).toBeVisible();
});

test("dish quantity respects the per-order limit, persists and survives guest checkout login", async ({
  page,
  context,
}) => {
  const student = await createStudent(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/stalls/${stall.id}`);
  await page
    .getByRole("button", { name: `添加${product.name}`, exact: true })
    .click();
  // The plus button must not activate the surrounding detail link.
  await expect(page).toHaveURL(new RegExp(`/stalls/${stall.id}$`));
  await page
    .getByRole("link", { name: `查看${product.name}详情`, exact: true })
    .click();
  for (let index = 0; index < 8; index++)
    await page.getByRole("button", { name: "增加份数", exact: true }).click();
  await expect(page.locator(".dish-quantity output")).toHaveText("9");
  await expect(
    page.getByRole("button", { name: "增加份数", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".dish-add-button strong")).toHaveText("¥76.5");
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  const checkout = page.getByRole("link", { name: "去结算", exact: true });
  await expect(checkout).toBeVisible();
  await expect(checkout).toHaveClass(/btn-primary/);
  await expect(checkout).toHaveAttribute("href", `/checkout/${stall.id}`);
  await expect(page.locator(".dish-added-total small")).toHaveText("本摊餐袋 10 份");
  await expect(page.locator(".dish-added-total strong")).toHaveText("¥85");
  await page.locator(".dish-purchase-panel").evaluate((form) =>
    (form as HTMLFormElement).requestSubmit(),
  );
  await expect(page.locator(".dish-added-total small")).toHaveText("本摊餐袋 10 份");
  await expect(page.locator(".dish-added-total strong")).toHaveText("¥85");
  await page.reload();
  await expect(
    page.getByRole("button", { name: /每单合计最多 10 份/ }),
  ).toBeDisabled();
  await expect(page.locator(".dish-cart-line")).toContainText(
    "本摊餐袋 10 份 · ¥85",
  );
  await page.getByRole("link", { name: "去结算", exact: true }).click();
  await expect(page).toHaveURL(/\/login\?returnTo=/);
  await login(page, student.username, student.password);
  await expect(page).toHaveURL(new RegExp(`/checkout/${stall.id}$`));
  await page.goto('/cart');
  await page.getByRole('button', { name: '加入当前账号餐袋', exact: true }).click();
  await page.goto(`/checkout/${stall.id}`);
  await expect(
    page.getByRole("link", { name: `查看${product.name}详情`, exact: true }),
  ).toBeVisible();
  const user = await (await context.request.get(`${baseURL}/api/v1/auth/me`)).json();
  const draft = await page.evaluate(
    ({ id, key }) => JSON.parse(localStorage.getItem(key) || "{}")[String(id)],
    { id: stall.id, key: `yanhuo-cart-v2:user:${user.id}` },
  );
  expect(draft).toEqual([
    expect.objectContaining({
      quantity: 10,
      product: expect.objectContaining({ id: product.id, price_cents: 850 }),
    }),
  ]);
  await assertNoHorizontalOverflow(page);
});

test("sold-out food remains readable and an unpublished food is safely unavailable", async ({
  page,
}) => {
  await page.goto(`/stalls/${stall.id}`);
  await page
    .getByRole("link", { name: `查看${soldOut.name}详情`, exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: soldOut.name, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "暂时售罄", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".dish-unavailable")).toContainText(
    "线上份数已售罄",
  );
  const inactive = await mutate(
    merchant,
    `/merchant/products/${soldOut.id}`,
    { is_active: false },
    "PATCH",
  );
  expect(inactive.ok(), await inactive.text()).toBeTruthy();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "这道餐点已下架或不存在", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /加入餐袋/ })).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "查看小摊菜单", exact: true }),
  ).toBeVisible();
});

test("checkout food links retain optional fields in memory and logout clears them", async ({
  page,
  context,
}) => {
  const student = await createStudent(context);
  await page.goto(`/stalls/${stall.id}/products/${product.id}`);
  await page.getByRole("button", { name: /加入餐袋/ }).click();
  await page.getByRole("link", { name: "去结算", exact: true }).click();
  await login(page, student.username, student.password);
  await page.goto('/cart');
  await page.getByRole('button', { name: '加入当前账号餐袋', exact: true }).click();
  await page.goto(`/checkout/${stall.id}`);
  const note = page.getByPlaceholder(
    "例如：餐具按需提供（每份口味请在上方分别填写）",
  );
  const phone = page.getByPlaceholder("如遇缺货，方便商家联系你");
  await page.locator(".checkout-contact-card > summary").click();
  await note.fill("详情往返测试：少辣，谢谢");
  await phone.fill("13800138000");
  await page
    .getByRole("link", { name: `查看${product.name}图片与详情`, exact: true })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/stalls/${stall.id}/products/${product.id}$`),
  );
  await page.getByRole("link", { name: "去结算", exact: true }).click();
  await page.locator(".checkout-contact-card > summary").click();
  await expect(note).toBeVisible();
  await expect(phone).toBeVisible();
  await expect(note).toHaveValue("详情往返测试：少辣，谢谢");
  await expect(phone).toHaveValue("13800138000");
  // Titles are also independent detail links, and the desktop bag preview opens the same dish.
  await page
    .getByRole("link", { name: `查看${product.name}详情`, exact: true })
    .click();
  await page.getByRole("link", { name: "返回小摊", exact: true }).click();
  await page
    .getByRole("link", { name: `查看餐袋中${product.name}详情`, exact: true })
    .click();
  await page.getByRole("link", { name: "去结算", exact: true }).click();
  await page.locator(".checkout-contact-card > summary").click();
  await expect(note).toBeVisible();
  await expect(phone).toBeVisible();
  await expect(note).toHaveValue("详情往返测试：少辣，谢谢");
  await expect(phone).toHaveValue("13800138000");
  expect(
    await page.evaluate(() =>
      [...Object.values(localStorage), ...Object.values(sessionStorage)].some(
        (value) => String(value).includes("详情往返测试"),
      ),
    ),
  ).toBe(false);
  // Stay inside the running SPA throughout logout/login, so this verifies identity cleanup,
  // not the unrelated fact that a full document reload discards in-memory stores.
  await page.locator(".header-user").click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await page.locator(".header-user").click();
  await login(page, student.username, student.password);
  await page.locator(".brand").click();
  await page
    .locator(`.stall-card a.stall-photo[href="/stalls/${stall.id}"]`)
    .first()
    .click();
  await page
    .locator(".pickup-card")
    .getByRole("link", { name: "去结算", exact: true })
    .click();
  await page.locator(".checkout-contact-card > summary").click();
  await expect(note).toBeVisible();
  await expect(phone).toBeVisible();
  await expect(note).toHaveValue("");
  await expect(phone).toHaveValue("");
});

test("offline-only stall food has details without an online ordering action", async ({
  page,
  context,
}) => {
  const { results: all } = await (
    await context.request.get(`${baseURL}/api/v1/stalls`)
  ).json();
  const offline = all.find(
    (item: any) => !item.transaction_enabled && item.products.length,
  );
  expect(
    offline,
    "Seed data must retain an information-only stall",
  ).toBeTruthy();
  await page.goto(`/stalls/${offline.id}/products/${offline.products[0].id}`);
  await expect(
    page.getByRole("heading", { name: offline.products[0].name, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "仅限线下到访", exact: true }),
  ).toBeDisabled();
  await expect(page.locator(".dish-unavailable")).toContainText(
    "仅支持线下到访",
  );
  await expect(
    page.getByRole("link", { name: "路线", exact: true }),
  ).toBeVisible();
});
