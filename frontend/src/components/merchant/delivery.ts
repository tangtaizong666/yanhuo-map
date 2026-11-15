import { allows } from "../../lib/orderActions";
export const activeOrderStatuses = [
  "pending_payment",
  "pending",
  "preparing",
  "ready",
  "delivering",
  "arrived",
];
export const cancellableKitchenStatuses = [
  "preparing",
  "ready",
  "delivering",
  "arrived",
];
export const cancellableDeliveryStatuses = cancellableKitchenStatuses;
export function isDelivery(order: any) {
  return order?.fulfillment_type === "delivery";
}
export function verifiedOnlinePayment(order: any) {
  return (
    order?.payment_method === "wechat" &&
    order.payment_status === "paid" &&
    (!order.payment || order.payment.status === "paid") &&
    !order.payment_review_required
  );
}
export function acceptReady(order: any) {
  if (Array.isArray(order?.allowed_actions)) return allows(order, "accept");
  return order.status === "pending" && verifiedOnlinePayment(order);
}
export function fulfillmentLabel(order: any) {
  return isDelivery(order) ? "商家自配送" : "到摊自取";
}
