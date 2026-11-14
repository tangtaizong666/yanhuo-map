import { expect, test, type Page } from "@playwright/test";
import { assertNoHorizontalOverflow, fulfillCsrf } from "./helpers";

const TINY_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
  "base64",
);

async function setupAssistFixture(
  page: Page,
  options: { discoveryOnly?: boolean; aiMode?: "ready" | "unconfigured" | "non_food" } = {},
) {
  const discoveryOnly = Boolean(options.discoveryOnly);
  const aiMode = options.aiMode || "ready";
  const user = {
    id: 2101,
    username: "photo_vendor",
    display_name: "南门铁板摊",
    is_merchant: true,
    is_staff: false,
  };
  const products = [
    {
      id: 2102,
      name: "招牌烤冷面",
      description: "铁板现做",
      category: "铁板小吃",
      image: "",
      price_cents: 800,
      stock: 15,
      stock_version: 1,
      is_active: true,
      sale_paused: false,
      taste_options: [],
    },
    {
      id: 2103,
      name: "酸梅汤",
      description: "冰镇解腻",
      category: "饮品",
      image: "",
      price_cents: 500,
      stock: 10,
      stock_version: 1,
      is_active: true,
      sale_paused: false,
      taste_options: [],
    },
  ];
  const stall = {
    id: 2101,
    name: "南门铁板摊",
    category: "铁板小吃",
    products,
    reviews: [],
    image: "",
    area_name: "南门",
    address: "南门夜市三号位",
    latitude: 30,
    longitude: 120,
    status: "open",
    session_status: "open",
    prep_minutes: 8,
    accepting_orders: true,
    transaction_enabled: !discoveryOnly,
    is_visible: true,
    can_order: !discoveryOnly,
    contact_phone: "",
    services: { mode: "live" },
    last_confirmed_at: new Date().toISOString(),
    closes_at: new Date(Date.now() + 7200000).toISOString(),
  };

  const state = {
    uploads: 0,
    aiCalls: [] as any[],
    created: [] as any[],
    unexpected: [] as string[],
  };

  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/media/merchants/2101/**", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: TINY_PNG }),
  );
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace("/api/v1", "");
    const send = (json: unknown, status = 200) => route.fulfill({ status, json });

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
      return send({ results: [], next: null, counts: { all: 0, active: 0, attention: 0 } });
    if (path === "/merchant/metrics")
      return send({
        today: { revenue_cents: 0, orders_created: 0, orders_completed: 0 },
        series: [],
        top_products: [],
        recent_payments: [],
      });
    if (path === "/merchant/stalls/2101/image" && req.method() === "POST") {
      state.uploads++;
      return send({ url: `/media/merchants/2101/dish${state.uploads}abcdef123456.jpg` }, 201);
    }
    if (path === "/merchant/stalls/2101/ai-product-draft" && req.method() === "POST") {
      const body = req.postDataJSON();
      state.aiCalls.push(body);
      if (aiMode === "unconfigured") {
        return send({
          ai_available: false,
          fallback_reason: "unconfigured",
          is_food: true,
          name: body.merchant_name || "",
          name_candidates: [],
          category: "铁板小吃",
          description: body.merchant_note ? `${body.merchant_note}。` : "",
          highlight_tags: ["现点现做", "趁热享用", "份量实在"],
          ask_hint: "可点选特点或补充主料",
          notice: "当前未开启 DeepSeek 自动识图，照片已保存；核对菜名与售价后即可直接上架。",
        });
      }
      if (aiMode === "non_food") {
        return send({
          ai_available: true,
          fallback_reason: "",
          is_food: false,
          name: "",
          name_candidates: [],
          category: "铁板小吃",
          description: "",
          highlight_tags: ["现点现做"],
          ask_hint: "请重拍菜品实物",
          notice: "这张图疑似二维码而非餐点实拍，收款码请到店铺收款设置中上传。",
        });
      }
      const refined = Boolean(body.merchant_note || body.selected_tags?.length);
      return send({
        ai_available: true,
        fallback_reason: "",
        is_food: true,
        name: body.merchant_name || "鸡蛋火腿肠炒面",
        name_candidates: ["铁板鸡蛋炒面", "招牌火腿炒面"],
        category: "铁板小吃",
        description: refined
          ? `铁板猛火现炒劲道面条，${body.merchant_note || "加鸡蛋火腿"}，${(body.selected_tags || []).join("、") || "锅气十足"}。`
          : "铁板现炒面条裹着金黄鸡蛋与火腿片，搭配青菜叶，色泽油亮热乎。",
        highlight_tags: ["铁板猛火", "锅气十足", "份量管饱"],
        ask_hint: "可点选特色或补一句真实主料再润色文案",
        notice: "",
      });
    }
    if (path === "/merchant/stalls/2101/products" && req.method() === "POST") {
      const body = req.postDataJSON();
      state.created.push(body);
      const item = {
        id: 2200 + state.created.length,
        sale_paused: false,
        stock_version: 1,
        stock: body.stock ?? 0,
        ...body,
      };
      products.push(item);
      return send(item, 201);
    }

    state.unexpected.push(`${req.method()} ${path}`);
    return send({ detail: "Unexpected isolated request" }, 500);
  });

  return state;
}

test("photo assist guides vendor through name, price, optional highlight refinement and lists dish across viewports", async ({
  page,
}, info) => {
  const state = await setupAssistFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/products");

  const trigger = page.getByRole("button", { name: "拍照上架", exact: true });
  await expect(trigger).toBeVisible();
  expect((await trigger.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await trigger.click();

  const dialog = page.getByRole("dialog", { name: "拍菜识图上架", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.locator('input[aria-label="拍照识菜"]').setInputFiles({
    name: "fresh-noodles.png",
    mimeType: "image/png",
    buffer: TINY_PNG,
  });

  await expect(dialog.getByText("已根据实拍生成初稿")).toBeVisible();
  const nameBox = dialog.getByRole("textbox", { name: "这道菜叫什么名字" });
  await expect(nameBox).toHaveValue("鸡蛋火腿肠炒面");

  // Candidate name chip updates dish name in one tap.
  await dialog.getByRole("button", { name: "铁板鸡蛋炒面", exact: true }).click();
  await expect(nameBox).toHaveValue("铁板鸡蛋炒面");

  // Quick price & stock chips avoid tedious typing.
  await dialog.getByRole("button", { name: "¥10", exact: true }).click();
  await dialog.getByRole("button", { name: "20 份", exact: true }).click();

  // Optional highlight tag + short real note refines the DeepSeek copywriting.
  await dialog.getByRole("button", { name: "锅气十足", exact: true }).click();
  await dialog.getByRole("textbox", { name: "补充真实主料或做法" }).fill("配脆爽包菜丝");
  await dialog.getByRole("button", { name: "润色文案", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: /商品文案/ })).toHaveValue(
    /配脆爽包菜丝.*锅气十足/,
  );

  for (const width of [360, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: width <= 390 ? 844 : 900 });
    await assertNoHorizontalOverflow(page);
    const submitBox = await dialog
      .getByRole("button", { name: "确认上架", exact: true })
      .boundingBox();
    expect(submitBox!.height).toBeGreaterThanOrEqual(44);
    await page.screenshot({
      path: info.outputPath(`merchant-photo-assist-${width}.png`),
    });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await dialog.getByRole("button", { name: "确认上架", exact: true }).click();
  await expect(dialog).toHaveCount(0);

  expect(state.uploads).toBe(1);
  expect(state.aiCalls).toHaveLength(2);
  expect(state.aiCalls[1]).toMatchObject({
    merchant_name: "铁板鸡蛋炒面",
    merchant_note: "配脆爽包菜丝",
    selected_tags: ["锅气十足"],
  });
  expect(state.created).toHaveLength(1);
  expect(state.created[0]).toMatchObject({
    name: "铁板鸡蛋炒面",
    category: "铁板小吃",
    price_cents: 1000,
    stock: 20,
    is_active: true,
    image: "/media/merchants/2101/dish1abcdef123456.jpg",
  });
  expect(state.created[0].idempotency_key).toMatch(/^product/);
  await expect(
    page.locator(".merchant-product").filter({ hasText: "铁板鸡蛋炒面" }),
  ).toContainText("¥10");
  expect(state.unexpected).toEqual([]);
});

test("discovery-only vendors, non-food warnings and handoff to full taste editor work cleanly", async ({
  page,
}) => {
  const state = await setupAssistFixture(page, {
    discoveryOnly: true,
    aiMode: "non_food",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/products");
  await page.getByRole("button", { name: "拍照上架", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "拍菜识图上架", exact: true });
  await dialog.locator('input[aria-label="从相册选图"]').setInputFiles({
    name: "qr-mistake.png",
    mimeType: "image/png",
    buffer: TINY_PNG,
  });

  await expect(dialog.getByRole("alert")).toContainText("疑似二维码");
  await expect(dialog.getByRole("spinbutton", { name: "线上剩余可卖份数" })).toHaveCount(0);
  await dialog.getByRole("group", { name: "今日供应" }).getByRole("button", { name: "今天有" }).click();
  await dialog.getByRole("textbox", { name: "这道菜叫什么名字" }).fill("手工冰粉");
  await dialog.getByRole("spinbutton", { name: "单价（元）" }).fill("6");

  // Unverified non-food image blocks accidental publish until explicitly confirmed or retaken.
  await dialog.getByRole("button", { name: "确认上架", exact: true }).click();
  await expect(dialog.getByText(/勾选上方核实确认后再上架/)).toBeVisible();
  expect(state.created).toHaveLength(0);

  await dialog.getByRole("checkbox", { name: /我已核实这是本店真实菜品照片/ }).check();

  // Handoff carries draft into full editor so vendor can configure free taste options.
  await dialog.getByRole("button", { name: "口味与更多", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const fullEditor = page.getByRole("dialog", { name: "添加新商品", exact: true });
  await expect(fullEditor).toBeVisible();
  await expect(fullEditor.getByRole("textbox", { name: "商品名称" })).toHaveValue("手工冰粉");
  await expect(fullEditor.getByRole("spinbutton", { name: "单价（元）" })).toHaveValue("6");
  expect(state.unexpected).toEqual([]);
});

test("skipping AI wait and retaking photo ignores late stale AI response", async ({
  page,
}) => {
  const state = await setupAssistFixture(page);
  let releaseFirstCall: (() => void) | null = null;
  const firstCallGate = new Promise<void>((resolve) => {
    releaseFirstCall = resolve;
  });
  let draftCallIndex = 0;

  await page.route("**/api/v1/merchant/stalls/2101/ai-product-draft", async (route) => {
    draftCallIndex += 1;
    const myIndex = draftCallIndex;
    const body = route.request().postDataJSON();
    state.aiCalls.push(body);
    if (myIndex === 1) {
      await firstCallGate;
      await route.fulfill({
        status: 200,
        json: {
          ai_available: true,
          fallback_reason: "",
          is_food: true,
          name: "迟到的旧识别结果",
          name_candidates: [],
          category: "旧分类",
          description: "这段旧文案不应覆盖新照片。",
          highlight_tags: ["趁热享用"],
          ask_hint: "",
          notice: "",
        },
      });
      return;
    }
    await route.fulfill({
      status: 200,
      json: {
        ai_available: true,
        fallback_reason: "",
        is_food: true,
        name: "现炸脆皮臭豆腐",
        name_candidates: ["灌汤臭豆腐"],
        category: "铁板小吃",
        description: "金黄酥脆臭豆腐浇满汤汁，撒葱花香菜。",
        highlight_tags: ["外酥里嫩", "汤汁浓郁"],
        ask_hint: "核对售价即可上架",
        notice: "",
      },
    });
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/merchant/products");
  await page.getByRole("button", { name: "拍照上架", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "拍菜识图上架", exact: true });
  await dialog.locator('input[aria-label="拍照识菜"]').setInputFiles({
    name: "first-slow.png",
    mimeType: "image/png",
    buffer: TINY_PNG,
  });

  const skipBtn = dialog.getByRole("button", { name: "跳过等待，直接手填", exact: true });
  await expect(skipBtn).toBeVisible();
  await skipBtn.click();
  await expect(dialog.getByText(/已跳过智能识图等待/)).toBeVisible();

  // Immediately retake a second photo while first request's server work finishes late.
  await dialog.locator('input[aria-label="重拍或更换商品照片"]').setInputFiles({
    name: "second-fresh.png",
    mimeType: "image/png",
    buffer: TINY_PNG,
  });

  await expect(dialog.getByRole("textbox", { name: "这道菜叫什么名字" })).toHaveValue(
    "现炸脆皮臭豆腐",
  );
  releaseFirstCall?.();
  await page.waitForTimeout(150);

  await expect(dialog.getByRole("textbox", { name: "这道菜叫什么名字" })).toHaveValue(
    "现炸脆皮臭豆腐",
  );
  await expect(dialog.getByRole("textbox", { name: /商品文案/ })).toHaveValue(
    "金黄酥脆臭豆腐浇满汤汁，撒葱花香菜。",
  );

  await dialog.getByRole("button", { name: "¥8", exact: true }).click();
  await dialog.getByRole("button", { name: "20 份", exact: true }).click();
  await dialog.getByRole("button", { name: "确认上架", exact: true }).click();
  await expect(dialog).toHaveCount(0);

  expect(state.created).toHaveLength(1);
  expect(state.created[0]).toMatchObject({
    name: "现炸脆皮臭豆腐",
    image: "/media/merchants/2101/dish2abcdef123456.jpg",
  });
  expect(state.unexpected).toEqual([]);
});
