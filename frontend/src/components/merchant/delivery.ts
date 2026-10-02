export const activeOrderStatuses = [
  "pending_payment",
  "pending",
  "preparing",
  "ready",
  "delivering",
  "arrived",
];
export const cancellableDeliveryStatuses = [
  "preparing",
  "ready",
  "delivering",
  "arrived",
];
export function isDelivery(order: any) {
  return order?.fulfillment_type === "delivery";
}
export function verifiedOnlinePayment(order: any) {
  return (
    order?.payment_method === "wechat" &&
    order.payment_status === "paid" &&
    order.payment?.status === "paid" &&
    !order.payment_review_required
  );
}
export function acceptReady(order: any) {
  return (
    order.status === "pending" &&
    (!isDelivery(order) || verifiedOnlinePayment(order))
  );
}
export function fulfillmentLabel(order: any) {
  return isDelivery(order) ? "商家自配送" : "到摊自取";
}
