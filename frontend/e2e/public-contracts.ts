// Fixture builders deliberately start from merchant inventory, then serialize the
// current public contract. Production code has no legacy-response fallback.
export function publicProduct(value: any) {
  const { stock, stock_version, ...item } = value;
  const availability =
    item.is_active === false
      ? "unavailable"
      : item.sale_paused
        ? "paused"
        : stock === 0
          ? "sold_out"
          : item.availability || "available";
  return {
    ...item,
    availability,
    max_order_quantity: availability === "available" ? 10 : 0,
  };
}
export function publicStall(
  value: any,
  mode: "detail" | "summary" | "map" = "detail",
) {
  if (!value) return value;
  const {
    prep_capacity,
    prep_active_orders,
    receiving_seen_at,
    receiving_age_seconds,
    location_draft_address,
    services,
    ...item
  } = structuredClone(value);
  item.receiving_valid_for_seconds =
    item.receiving_status === "recent" ? 30 : 0;
  if (item.products) item.products = item.products.map(publicProduct);
  if (item.delivery) delete item.delivery.capacity;
  if (mode !== "detail") {
    for (const key of [
      "wechat_payment",
      "delivery",
      "reviews",
      "contact_phone",
      "merchant_name",
      "qualification_note",
      "order_count",
    ])
      delete item[key];
    item.products = (item.products || [])
      .filter((p: any) => p.availability === "available")
      .slice(0, 2);
  }
  if (mode === "map")
    for (const key of [
      "products",
      "description",
      "rating",
      "review_count",
      "distance_m",
      "usual_hours",
    ])
      delete item[key];
  return item;
}
export function publicResponse(path: string, value: any) {
  if (path === "/stalls" || path === "/follows")
    return {
      results: value.map((s: any) => publicStall(s, "summary")),
      next: null,
    };
  if (path === "/stalls/map")
    return {
      results: value.map((s: any) => publicStall(s, "map")),
      truncated: false,
      limit: 200,
    };
  if (/^\/stalls\/\d+$/.test(path)) return publicStall(value);
  if (/^\/stalls\/\d+\/follow$/.test(path))
    return { id: value.id, is_followed: value.is_followed };
  return value;
}
export function mealResponse(stalls: any[], params: URLSearchParams) {
  const term = (params.get("q") || "").toLocaleLowerCase(),
    budget = Number(params.get("budget") || 0);
  const rows = stalls
    .filter(
      (s) => s.status === "open" && (!params.get("follow") || s.is_followed),
    )
    .flatMap((stall) =>
      (stall.products || []).map((product: any) => ({
        product: publicProduct(product),
        stall: publicStall(stall, "map"),
      })),
    )
    .filter(
      (row: any) =>
        row.product.availability === "available" &&
        (!budget || row.product.price_cents <= budget) &&
        (!term ||
          `${row.product.name} ${row.product.description} ${row.stall.name}`
            .toLocaleLowerCase()
            .includes(term)),
    );
  if (params.get("meal_sort") === "price")
    rows.sort(
      (a: any, b: any) =>
        a.product.price_cents - b.product.price_cents ||
        a.product.id - b.product.id,
    );
  return { results: rows, next: null };
}
