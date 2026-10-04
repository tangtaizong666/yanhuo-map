import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import path from "node:path";
import {
  assertNoHorizontalOverflow,
  baseURL,
  createStudent,
  login,
  mutate,
} from "./helpers";

async function merchantLogin(
  page: Page,
  context: BrowserContext,
  section = "",
) {
  const configResponse = await context.request.get(`${baseURL}/api/v1/config`);
  expect(configResponse.ok(), await configResponse.text()).toBeTruthy();
  expect(
    (await configResponse.json()).demo_mode,
    "Merchant browser tests only mutate explicitly labelled demo data",
  ).toBe(true);
  await page.goto(
    `/login?returnTo=${encodeURIComponent(section ? `/merchant/${section}` : "/merchant")}`,
  );
  await login(page, "vendor", "demo12345");
  await expect(page.locator(".m-stall-bar")).toBeVisible();
  const stallsResponse = await context.request.get(
    `${baseURL}/api/v1/merchant/stalls`,
  );
  expect(stallsResponse.ok(), await stallsResponse.text()).toBeTruthy();
  const stalls = await stallsResponse.json();
  expect(
    stalls.length,
    "The real vendor needs at least one seeded stall",
  ).toBeGreaterThan(0);
  return stalls;
}

async function publicStall(context: BrowserContext, id: number) {
  const response = await context.request.get(`${baseURL}/api/v1/stalls/${id}`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

async function completedReviewFixture(
  browser: Browser,
  merchant: BrowserContext,
  stall: any,
) {
  // This integration fixture is deliberately restricted to the disposable API server.
  expect(
    new URL(baseURL).origin,
    "Review fixtures must never write to the shared 5183 demo",
  ).toBe("http://127.0.0.1:5193");
  const student = await browser.newContext({ baseURL });
  const content = `独立评价验证：取餐顺利，餐点热乎。${Date.now()}`;
  let productId: number | undefined, orderId: string | undefined;
  async function checked(
    context: BrowserContext,
    endpoint: string,
    body: any,
    method = "POST",
  ) {
    const response = await mutate(context, endpoint, body, method);
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  async function cleanup() {
    try {
      if (orderId) {
        const order = await (
          await student.request.get(`${baseURL}/api/v1/orders/${orderId}`)
        ).json();
        if (order.status === "ready" && order.payment_status === "paid")
          await checked(merchant, `/merchant/orders/${orderId}/action`, {
            action: "complete",
            pickup_code: order.pickup_code,
          });
        else if (["pending", "preparing", "ready"].includes(order.status)) {
          await checked(student, `/orders/${orderId}/cancel`, {
            reason: "独立评价测试结束，清理本测试订单",
          });
          if (order.status !== "pending")
            await checked(merchant, `/merchant/orders/${orderId}/action`, {
              action: "approve_cancel",
            });
        }
      }
      if (productId)
        await checked(
          merchant,
          `/merchant/products/${productId}`,
          { is_active: false },
          "PATCH",
        );
      await checked(
        merchant,
        `/merchant/stalls/${stall.id}/profile`,
        {
          prep_capacity: stall.prep_capacity ?? null,
          accepting_orders: stall.accepting_orders !== false,
        },
        "PATCH",
      );
      await checked(merchant, `/merchant/stalls/${stall.id}/status`, {
        status: stall.session_status || "closed",
        confirm_location: false,
        closes_at: stall.closes_at,
        stop_orders_at: stall.stop_orders_at ?? null,
      });
    } finally {
      await student.close();
    }
  }
  try {
    const credentials = await createStudent(student);
    await checked(student, "/auth/login", credentials);
    await checked(
      merchant,
      `/merchant/stalls/${stall.id}/profile`,
      { accepting_orders: true, prep_capacity: null },
      "PATCH",
    );
    await checked(merchant, `/merchant/stalls/${stall.id}/status`, {
      status: "open",
      confirm_location: true,
      closes_at: new Date(Date.now() + 7200000).toISOString(),
      stop_orders_at: null,
    });
    const product = await checked(
      merchant,
      `/merchant/stalls/${stall.id}/products`,
      {
        name: `评价验证餐点-${Date.now()}`,
        price_cents: 800,
        stock: 1,
        category: "测试餐点",
        description: "仅隔离测试使用，结束后下架。",
      },
    );
    productId = product.id;
    const order = await checked(student, "/orders", {
      stall_id: stall.id,
      fulfillment_type: "pickup",
      idempotency_key: crypto.randomUUID(),
      items: [
        {
          product_id: product.id,
          quantity: 1,
          expected_price_cents: product.price_cents,
        },
      ],
    });
    orderId = order.id;
    expect(order.mode).toBe("simulation");
    for (const action of ["accept", "ready", "confirm_payment"])
      await checked(merchant, `/merchant/orders/${order.id}/action`, {
        action,
      });
    const ready = await (
      await student.request.get(`${baseURL}/api/v1/orders/${order.id}`)
    ).json();
    expect(ready.pickup_code).toMatch(/^\d{8}$/);
    await checked(merchant, `/merchant/orders/${order.id}/action`, {
      action: "complete",
      pickup_code: ready.pickup_code,
    });
    const reviewed = await checked(student, `/orders/${order.id}/review`, {
      rating: 5,
      content,
    });
    expect(reviewed.status).toBe("completed");
    const review = (await publicStall(merchant, stall.id)).reviews.find(
      (item: any) => item.content === content,
    );
    expect(review).toBeTruthy();
    return { review, cleanup };
  } catch (error) {
    await cleanup();
    throw error;
  }
}

test("merchant creates and uploads a menu item, edits it and controls public availability", async ({
  page,
  context,
}, testInfo) => {
  const stalls = await merchantLogin(page, context, "products");
  const stall = stalls[0];
  await page.getByLabel("选择管理的摊位").selectOption(String(stall.id));
  const uniqueName = `测试手作小食-${Date.now()}`;
  const editedName = `${uniqueName}-改`;
  let productId: number | undefined;
  try {
    await page.getByRole("button", { name: "添加商品", exact: true }).click();
    const editor = page.getByRole("dialog", { name: "添加新商品" });
    await expect(editor).toBeVisible();
    await editor.getByLabel("商品名称", { exact: false }).fill(uniqueName);
    await editor
      .locator("summary")
      .filter({ hasText: "照片、分类与介绍（选填）" })
      .click();
    await editor.getByLabel("商品分类", { exact: true }).fill("测试小食");
    await editor.getByLabel("单价（元）", { exact: false }).fill("9.80");
    await editor.getByLabel("线上剩余可卖份数", { exact: true }).fill("7");
    await editor
      .getByLabel("商品描述", { exact: false })
      .fill("自动化测试商品，真实上传及菜单管理验证。");
    const imageUploaded = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/merchant/stalls/${stall.id}/image`),
    );
    await editor
      .locator("input[type=file]")
      .setInputFiles(path.resolve("public/images/food-pancake.jpg"));
    const uploadResponse = await imageUploaded;
    expect(uploadResponse.status(), await uploadResponse.text()).toBe(201);
    const image = await uploadResponse.json();
    expect(image.url).toMatch(/^\/media\/merchants\//);
    await expect(editor.getByAltText("商品图片预览")).toHaveAttribute(
      "src",
      image.url,
    );
    const imageResponse = await context.request.get(`${baseURL}${image.url}`);
    expect(
      imageResponse.ok(),
      "Uploaded product image must be served through the real media proxy",
    ).toBeTruthy();
    expect(imageResponse.headers()["content-type"]).toMatch(/^image\/jpeg/);
    for (const width of [360, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await assertNoHorizontalOverflow(page);
      await expect(
        editor.getByRole("button", { name: "添加商品", exact: true }),
      ).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath(`product-editor-${width}.png`),
      });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    const created = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/merchant/stalls/${stall.id}/products`),
    );
    await editor.getByRole("button", { name: "添加商品", exact: true }).click();
    const createdResponse = await created;
    expect(createdResponse.status(), await createdResponse.text()).toBe(201);
    const product = await createdResponse.json();
    productId = product.id;
    expect(product).toMatchObject({
      name: uniqueName,
      category: "测试小食",
      price_cents: 980,
      stock: 7,
      is_active: true,
      image: image.url,
    });
    await expect(editor).toHaveCount(0);
    await page.reload();
    const card = page
      .locator(".merchant-product")
      .filter({ hasText: uniqueName });
    await expect(card).toBeVisible();
    await expect(
      card.getByAltText(uniqueName, { exact: true }),
    ).toHaveAttribute("src", image.url);
    await card.getByRole("button", { name: /^编辑商品：/ }).click();
    const editDialog = page.getByRole("dialog", { name: "编辑商品" });
    await editDialog.getByLabel("商品名称", { exact: false }).fill(editedName);
    await editDialog
      .locator("summary")
      .filter({ hasText: "照片、分类与介绍（选填）" })
      .click();
    await editDialog.getByLabel("商品分类", { exact: true }).fill("暖心小食");
    await editDialog.getByLabel("单价（元）", { exact: false }).fill("10.50");
    await editDialog
      .getByLabel("商品描述", { exact: false })
      .fill("修改口味与份量说明；库存保持原值。");
    const updated = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().endsWith(`/merchant/products/${productId}`),
    );
    await editDialog
      .getByRole("button", { name: "保存修改", exact: true })
      .click();
    const updateResponse = await updated;
    expect(updateResponse.status(), await updateResponse.text()).toBe(200);
    expect(updateResponse.request().postDataJSON()).toEqual({
      name: editedName,
      category: "暖心小食",
      price_cents: 1050,
      description: "修改口味与份量说明；库存保持原值。",
    });
    await expect(editDialog).toHaveCount(0);
    await page.getByLabel("搜索商品名称").fill(editedName);
    const edited = page
      .locator(".merchant-product")
      .filter({ hasText: editedName });
    await expect(edited).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("new-product-persisted.png"),
      fullPage: true,
    });
    const unpublished = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().endsWith(`/merchant/products/${productId}`),
    );
    await edited.getByRole("button", { name: "下架", exact: true }).click();
    expect((await unpublished).ok()).toBeTruthy();
    await expect(edited.locator(".product-status")).toHaveText("已下架");
    expect(
      (await publicStall(context, stall.id)).products.some(
        (item: any) => item.id === productId,
      ),
    ).toBe(false);
    const merchantData = await (
      await context.request.get(`${baseURL}/api/v1/merchant/stalls`)
    ).json();
    expect(
      merchantData
        .find((item: any) => item.id === stall.id)
        .products.find((item: any) => item.id === productId),
    ).toMatchObject({ name: editedName, is_active: false, stock: 7 });
    const republished = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" &&
        response.url().endsWith(`/merchant/products/${productId}`),
    );
    await edited.getByRole("button", { name: "上架", exact: true }).click();
    expect((await republished).ok()).toBeTruthy();
    await expect(edited.locator(".product-status")).toHaveText("销售中");
    expect(
      (await publicStall(context, stall.id)).products.find(
        (item: any) => item.id === productId,
      ),
    ).toMatchObject({
      name: editedName,
      category: "暖心小食",
      price_cents: 1050,
      stock: 7,
      image: image.url,
    });
  } finally {
    if (productId != null) {
      const cleanup = await mutate(
        context,
        `/merchant/products/${productId}`,
        { is_active: false },
        "PATCH",
      );
      expect(cleanup.ok(), await cleanup.text()).toBeTruthy();
    }
  }
});

test("merchant review reply persists and is visible on the public stall", async ({
  page,
  context,
  browser,
}) => {
  const stalls = await merchantLogin(page, context, "reviews");
  const stall = stalls.find(
    (item: any) =>
      item.transaction_enabled && item.services?.mode === "simulation",
  );
  expect(stall, "Requires an eligible seeded demo stall").toBeTruthy();
  const fixture = await completedReviewFixture(browser, context, stall);
  const review = fixture.review;
  const originalReply = review.merchant_reply || "";
  const reply = `感谢你的反馈，我们会继续认真做好每一餐。测试回复 ${Date.now()}`;
  let changed = false;
  let publicPage: Page | undefined;
  try {
    await page.reload();
    await page.getByLabel("选择管理的摊位").selectOption(String(stall.id));
    const card = page
      .locator(".review-card")
      .filter({ has: page.getByText(review.content, { exact: true }) })
      .first();
    await expect(card).toBeVisible();
    await card
      .getByRole("button", {
        name: originalReply ? "修改回复" : "回复评价",
        exact: true,
      })
      .click();
    await card.getByLabel("回复顾客", { exact: true }).fill(reply);
    const posted = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/merchant/reviews/${review.id}/reply`),
    );
    await card.getByRole("button", { name: "发布回复", exact: true }).click();
    const result = await posted;
    expect(result.status(), await result.text()).toBe(200);
    changed = true;
    await expect(card.locator(".merchant-reply p")).toHaveText(reply);
    await page.reload();
    await expect(
      page
        .locator(".review-card")
        .filter({ hasText: reply })
        .locator(".merchant-reply p"),
    ).toHaveText(reply);
    const publicReview = (await publicStall(context, stall.id)).reviews.find(
      (item: any) => item.id === review.id,
    );
    expect(publicReview.merchant_reply).toBe(reply);
    expect(publicReview.replied_at).toBeTruthy();
    publicPage = await context.newPage();
    await publicPage.goto(`/stalls/${stall.id}`);
    await publicPage.getByRole("button", { name: /大家的评价/ }).click();
    await expect(
      publicPage
        .locator(".merchant-public-reply")
        .getByText(reply, { exact: true }),
    ).toBeVisible();
    const publishedCard = page
      .locator(".review-card")
      .filter({ hasText: reply });
    await publishedCard
      .getByRole("button", { name: "撤回回复", exact: true })
      .click();
    await expect(
      publishedCard.getByRole("group", { name: "确认撤回商家回复" }),
    ).toBeVisible();
    const withdrawn = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().endsWith(`/merchant/reviews/${review.id}/reply`),
    );
    await publishedCard
      .getByRole("button", { name: "确认撤回", exact: true })
      .click();
    expect((await withdrawn).ok()).toBeTruthy();
    const afterWithdrawal = (await publicStall(context, stall.id)).reviews.find(
      (item: any) => item.id === review.id,
    );
    expect(afterWithdrawal).toMatchObject({
      merchant_reply: "",
      replied_at: null,
      content: review.content,
      rating: review.rating,
    });
    await publicPage.reload();
    await publicPage.getByRole("button", { name: /大家的评价/ }).click();
    await expect(publicPage.getByText(reply, { exact: true })).toHaveCount(0);
    changed = originalReply !== "";
  } finally {
    await publicPage?.close();
    if (changed) {
      const restored = await mutate(
        context,
        `/merchant/reviews/${review.id}/reply`,
        { content: originalReply },
      );
      expect(
        restored.ok(),
        "Restore the original public merchant reply after checking persistence",
      ).toBeTruthy();
    }
    await fixture.cleanup();
  }
});

const merchantPages = [
  { path: "/merchant", title: "经营首页", image: "dashboard" },
  { path: "/merchant/orders", title: "订单处理", image: "orders" },
  { path: "/merchant/products", title: "商品管理", image: "products" },
  { path: "/merchant/analytics", title: "经营数据", image: "analytics" },
  { path: "/merchant/reviews", title: "顾客评价", image: "reviews" },
  { path: "/merchant/store", title: "店铺设置", image: "store" },
];

for (const width of [360, 390, 768, 1440]) {
  test(`all six merchant pages work inside a ${width}px viewport`, async ({
    page,
    context,
  }, testInfo) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: width >= 768 ? 1000 : 844 });
    await merchantLogin(page, context);
    for (const entry of merchantPages) {
      await page.goto(entry.path);
      await expect(page.locator(".m-page-heading h1")).toContainText(
        entry.title,
      );
      await expect(page.locator(".m-stall-bar")).toBeVisible();
      await expect(
        page.getByText("正在准备商家工作台…", { exact: true }),
      ).toHaveCount(0);
      if (entry.image === "analytics")
        await expect(
          page.getByText("正在更新经营数据…", { exact: true }),
        ).toHaveCount(0);
      if (entry.image === "reviews")
        await expect(
          page.getByText("正在读取顾客评价…", { exact: true }),
        ).toHaveCount(0);
      await assertNoHorizontalOverflow(page);
      await page.screenshot({
        path: testInfo.outputPath(`${entry.image}-${width}.png`),
        fullPage: true,
      });
      if (entry.image === "dashboard")
        await page.screenshot({
          path: testInfo.outputPath(`${entry.image}-${width}-viewport.png`),
        });
    }
  });
}
