import type { Product } from "./types";

// Product supply and stall admission are independent. The server rechecks both.
export function productAvailable(product?: Product | null): boolean {
  return (
    !!product &&
    product.is_active !== false &&
    !product.sale_paused &&
    product.availability === "available"
  );
}

export function productUnavailableReason(product?: Product | null): string {
  if (!product || product.is_active === false) return "这道餐点已下架";
  if (product.sale_paused || product.availability === "paused")
    return "商家已暂停供应这道餐点";
  if (product.availability === "sold_out")
    return product.display_only ? "这道餐点今天已卖完" : "线上份数已售罄";
  if (product.availability !== "available")
    return "暂时无法确认可售状态，请刷新";
  return "";
}

export function preparationLabel(minutes?: number | null): string {
  return Number.isFinite(minutes) && Number(minutes) > 0
    ? `通常备餐约 ${minutes} 分钟`
    : "备餐时间待商家确认";
}
