import { describe, expect, it } from "vitest";
import { ApiError } from "../src/lib/api";
import {
  isMerchantPath,
  loginDestination,
  safeReturnTo,
} from "../src/lib/identity";
import { isDefinitiveIdempotentRejection } from "../src/lib/merchantIdempotency";
import {
  invalidPortions,
  normalizePortions,
  portionText,
  specifiedPortions,
} from "../src/lib/portions";
import type { Product, User } from "../src/lib/types";

const studentUser: User = {
  id: 10,
  username: "student1",
  display_name: "张同学",
  is_merchant: false,
  is_staff: false,
};

const merchantUser: User = {
  id: 20,
  username: "merchant1",
  display_name: "李摊主",
  is_merchant: true,
  is_staff: false,
};

describe("merchant idempotent write error classification", () => {
  it("treats fresh 400/403/404/422 or submitted=false errors as definitive rejections", () => {
    expect(
      isDefinitiveIdempotentRejection(new ApiError("参数错误", 400), false),
    ).toBe(true);
    expect(
      isDefinitiveIdempotentRejection(new ApiError("CSRF 失效", 403), false),
    ).toBe(true);
    expect(
      isDefinitiveIdempotentRejection(new ApiError("未找到商品", 404), false),
    ).toBe(true);
    expect(
      isDefinitiveIdempotentRejection(new ApiError("校验未通过", 422), false),
    ).toBe(true);
    expect(
      isDefinitiveIdempotentRejection(
        new ApiError("请求未发出", 0, { submitted: false }),
        false,
      ),
    ).toBe(true);
  });

  it("keeps network/5xx errors and excluded conflict codes non-definitive", () => {
    expect(
      isDefinitiveIdempotentRejection(new ApiError("网关超时", 502), false),
    ).toBe(false);
    expect(
      isDefinitiveIdempotentRejection(new TypeError("Failed to fetch"), false),
    ).toBe(false);
    expect(
      isDefinitiveIdempotentRejection(
        new ApiError("版本冲突", 409, { code: "stock_version_conflict" }),
        false,
      ),
    ).toBe(false);
    expect(
      isDefinitiveIdempotentRejection(
        new ApiError("版本冲突", 400, { code: "stock_version_conflict" }),
        false,
        ["stock_version_conflict"],
      ),
    ).toBe(false);
  });

  it("never releases an uncertain batch even when a later retry hits 403 or 400", () => {
    expect(
      isDefinitiveIdempotentRejection(new ApiError("CSRF 失效", 403), true),
    ).toBe(false);
    expect(
      isDefinitiveIdempotentRejection(
        new ApiError("请求未发出", 0, { submitted: false }),
        true,
      ),
    ).toBe(false);
  });
});

describe("identity returnTo sanitization and role isolation", () => {
  it("rejects open redirects, protocol-relative URLs, backslashes, and login loops", () => {
    expect(safeReturnTo("https://evil.example/phish")).toBe("/");
    expect(safeReturnTo("//evil.example/phish")).toBe("/");
    expect(safeReturnTo("/\\evil.example")).toBe("/");
    expect(safeReturnTo("/login")).toBe("/");
    expect(safeReturnTo("/login?returnTo=/me")).toBe("/");
    expect(safeReturnTo("/stalls/5?tab=reviews")).toBe("/stalls/5?tab=reviews");
  });

  it("routes merchants to merchant workspace and prevents student returnTo into /merchant", () => {
    expect(isMerchantPath("/merchant")).toBe(true);
    expect(isMerchantPath("/merchant/orders?status=ready")).toBe(true);
    expect(isMerchantPath("/merchant-info")).toBe(false);

    expect(loginDestination(merchantUser, "/stalls/3")).toBe("/merchant");
    expect(loginDestination(merchantUser, "/merchant/apply")).toBe("/merchant");
    expect(loginDestination(merchantUser, "/merchant?section=store")).toBe(
      "/merchant?section=store",
    );

    expect(loginDestination(studentUser, "/merchant")).toBe("/");
    expect(loginDestination(studentUser, "/merchant/apply")).toBe(
      "/merchant/apply",
    );
    expect(loginDestination(studentUser, "/orders/12")).toBe("/orders/12");
  });
});

describe("portion normalization and taste validation", () => {
  const noodle: Product = {
    id: 1,
    name: "招牌炒面",
    description: "",
    image: "",
    price_cents: 1200,
    availability: "available",
    max_order_quantity: 10,
    taste_options: [{ name: "辣度", choices: ["不辣", "微辣", "特辣"] }],
  };

  it("clamps note length, strips empty options, and flags tampered taste choices", () => {
    const longNote = "多加香菜".repeat(40);
    const normalized = normalizePortions(
      [{ options: { 辣度: "微辣", 空项: "" }, note: longNote }],
      2,
    );
    expect(normalized).toHaveLength(2);
    expect(normalized[0].options).toEqual({ 辣度: "微辣" });
    expect(normalized[0].note.length).toBe(100);
    expect(normalized[1]).toEqual({ options: {}, note: "" });

    expect(specifiedPortions([{ options: {}, note: "   " }], 1)).toBeUndefined();
    expect(invalidPortions(noodle, [{ options: { 辣度: "微辣" }, note: "" }], 1)).toBe(false);
    expect(invalidPortions(noodle, [{ options: { 辣度: "变态辣" }, note: "" }], 1)).toBe(true);
    expect(portionText({ options: { 辣度: "微辣" }, note: "不要葱 " })).toBe(
      "辣度：微辣 · 不要葱",
    );
  });
});
