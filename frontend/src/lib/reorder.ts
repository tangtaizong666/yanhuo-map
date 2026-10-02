import type { CartItem, Order, Product, Stall, Portion } from "./types";
import { invalidPortions, normalizePortions } from "./portions";
import { productAvailable, productUnavailableReason } from "./availability";

export interface ReorderRow {
  previous: Order["items"][number];
  product?: Product;
  existing: number;
  add: number;
  reason: string;
  priceChanged: boolean;
  draftPriceChanged: boolean;
  portions: Portion[];
  tastesChanged: boolean;
}

// Reordering only prepares a local draft. The server still validates it at checkout.
export function reorderRows(
  order: Order,
  stall: Stall,
  draft: CartItem[],
): ReorderRow[] {
  return order.items.map((previous) => {
    const product = stall.products.find(
      (item) => item.id === previous.product_id,
    );
    const saved = draft.find((item) => item.product.id === previous.product_id);
    const existing = saved?.quantity || 0;
    const capacity =
      product && productAvailable(product)
        ? Math.max(0, Math.min(product.stock, 99) - existing)
        : 0;
    const add = Math.min(previous.quantity, capacity);
    let reason = "";
    if (!product) reason = "已下架或暂时不可售，本次跳过";
    else if (!productAvailable(product))
      reason = `${productUnavailableReason(product)}，本次跳过`;
    else if (!add) reason = "餐袋中已有数量达到当前可售上限，本次不再添加";
    else if (add < previous.quantity)
      reason = `受当前库存与每种最多 99 份限制，本次可加 ${add} 份`;
    return {
      previous,
      product,
      existing,
      add,
      reason,
      priceChanged:
        !!product && product.price_cents !== previous.unit_price_cents,
      draftPriceChanged:
        !!product &&
        !!saved &&
        saved.product.price_cents !== product.price_cents,
      portions: normalizePortions(previous.portions, add),
      tastesChanged:
        !!product && invalidPortions(product, previous.portions, add),
    };
  });
}

export function reorderSignature(stall: Stall, rows: ReorderRow[]) {
  return JSON.stringify({
    canOrder: stall.can_order,
    status: stall.status,
    address: stall.address,
    latitude: stall.latitude,
    longitude: stall.longitude,
    rows: rows.map((row) => [
      row.previous.product_id,
      row.product?.name,
      row.product?.price_cents,
      row.existing,
      row.add,
      row.reason,
      row.draftPriceChanged,
      row.product?.taste_options,
      row.portions,
      row.tastesChanged,
    ]),
  });
}

export function reorderUnavailableReason(stall: Stall) {
  if (stall.order_unavailable_reason) return stall.order_unavailable_reason;
  if (!stall.transaction_enabled)
    return "这个摊位目前仅支持线下到访，暂不开放在线点单。";
  if (stall.status === "stale") return "商家尚未重新确认位置，暂时不能点单。";
  if (stall.status === "paused") return "商家正在暂歇，恢复出摊后再来看看。";
  if (stall.status === "closed") return "商家已经收摊，重新出摊后再来看看。";
  return "这个摊位暂时无法接单，请稍后重试。";
}
