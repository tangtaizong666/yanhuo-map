// Public ordering policy is independent of merchant inventory; checkout revalidates stock.
export const ORDER_QUANTITY_LIMIT = 10;
export function remainingOrderQuantity(
  rows: readonly { quantity: number }[],
): number {
  return Math.max(
    0,
    ORDER_QUANTITY_LIMIT - rows.reduce((sum, row) => sum + row.quantity, 0),
  );
}
export function mergeDiscoveryRows<T extends { id: number }>(
  previous: T[],
  incoming: T[],
): T[] {
  const rows = new Map(previous.map((row) => [row.id, row]));
  for (const row of incoming) rows.set(row.id, row);
  return [...rows.values()];
}
