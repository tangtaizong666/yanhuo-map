import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { notices, notify } from "../src/lib/notify";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("window", { setTimeout });
  notices.value = [];
});
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("rapid order confirmations show the latest success without swallowing errors", () => {
  notify("已接单", "success");
  notify("另一笔订单同步失败", "error");
  notify("请核对顾客要求", "info");
  notify("已通知取餐", "success");
  expect(notices.value.map((notice) => notice.message)).toEqual([
    "另一笔订单同步失败", "请核对顾客要求", "已通知取餐",
  ]);
});

test("an older confirmation timer cannot dismiss its newer replacement", () => {
  notify("已接单", "success");
  vi.advanceTimersByTime(3000);
  notify("已通知取餐", "success");
  vi.advanceTimersByTime(1500);
  expect(notices.value.map((notice) => notice.message)).toEqual(["已通知取餐"]);
  vi.advanceTimersByTime(3000);
  expect(notices.value).toEqual([]);
});
