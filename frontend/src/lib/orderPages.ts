import { api } from "./api";
import { needsFinancialFollowUp, needsMerchantFollowUp } from "./orderFollowUp";
import type { Order } from "./types";

export type OrderCounts = Record<string, number>;
export interface OrderPage {
  results: Order[];
  next: string | null;
  counts: OrderCounts;
  legacy?: boolean;
}
export const activeStatuses = [
  "pending_payment",
  "pending",
  "preparing",
  "ready",
  "delivering",
  "arrived",
];
export function matchesOrderFilter(
  order: Order,
  filter: string,
  merchant = false,
): boolean {
  const active = activeStatuses.includes(order.status);
  const followup = merchant
    ? needsMerchantFollowUp(order)
    : needsFinancialFollowUp(order);
  if (filter === "all") return true;
  if (filter === "active") return active;
  if (filter === "followup") return followup;
  if (filter === "attention") return active || followup;
  if (filter === "cancelled")
    return ["cancelled", "rejected"].includes(order.status);
  return order.status === filter;
}
export function countOrders(rows: Order[], merchant = false): OrderCounts {
  return {
    reviewed: rows.filter((row) => !!row.review).length,
    ...Object.fromEntries(
      [
        "all",
        "active",
        "followup",
        "attention",
        "completed",
        "cancelled",
        ...activeStatuses,
      ].map((filter) => [
        filter,
        rows.filter((row) => matchesOrderFilter(row, filter, merchant)).length,
      ]),
    ),
  };
}
export function mergeOrders(...groups: Order[][]): Order[] {
  const map = new Map<string, Order>();
  for (const rows of groups) for (const row of rows) map.set(row.id, row);
  return [...map.values()].sort(
    (a, b) =>
      b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id),
  );
}
export async function orderPage(
  path: string,
  filter: string,
  cursor?: string | null,
  signal?: AbortSignal,
  pageSize = 30,
): Promise<OrderPage> {
  const [base, search] = path.split("?");
  const query = new URLSearchParams(search);
  query.set("pagination", "cursor");
  query.set("filter", filter);
  query.set("page_size", String(pageSize));
  if (cursor) query.set("cursor", cursor);
  const data = await api<OrderPage | Order[]>(`${base}?${query}`, { signal });
  const merchant = path.startsWith("/merchant");
  if (Array.isArray(data))
    return {
      results: data,
      next: null,
      counts: countOrders(data, merchant),
      legacy: true,
    };
  if (
    !data ||
    !Array.isArray(data.results) ||
    (data.next !== null && typeof data.next !== "string") ||
    !data.counts
  )
    throw new Error("订单列表未能读取，请重新同步。");
  return data;
}
export async function attentionOrders(
  path: string,
  signal?: AbortSignal,
): Promise<OrderPage> {
  let result = await orderPage(path, "attention", null, signal);
  const cursors = new Set<string>();
  while (result.next) {
    if (cursors.has(result.next))
      throw new Error("订单分页未完成，请重新同步。");
    cursors.add(result.next);
    const page = await orderPage(path, "attention", result.next, signal);
    result = { ...page, results: mergeOrders(result.results, page.results) };
  }
  return result;
}
