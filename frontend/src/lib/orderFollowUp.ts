import type { Order } from "./types";

type FollowUpOrder = Partial<
  Pick<
    Order,
    | "status"
    | "fulfillment_type"
    | "payment_status"
    | "payment_review_required"
    | "payment"
    | "refund"
    | "delivery_issue"
  >
>;

export type FollowUpReason = {
  kind: "refund" | "payment" | "delivery";
  label: string;
};

const refundLabels: Record<string, string> = {
  creating: "退款申请待确认",
  processing: "退款处理中",
  reconcile: "退款结果待确认",
  abnormal: "退款异常",
  closed: "退款关闭，尚未退回",
};

// Fulfilment can end while money is still unresolved. In particular, a closed
// refund restores payment_status=paid and must remain discoverable.
export function refundNeedsFollowUp(order: FollowUpOrder) {
  return order.refund
    ? Object.hasOwn(refundLabels, order.refund.status)
    : order.payment_status === "refunding";
}

export function paymentNeedsFollowUp(order: FollowUpOrder) {
  return (
    !!order.payment_review_required ||
    ["review", "reconcile"].includes(order.payment?.status || "")
  );
}

export function deliveryNeedsFollowUp(order: FollowUpOrder) {
  return (
    order.fulfillment_type === "delivery" &&
    !!order.delivery_issue?.trim() &&
    ["preparing", "ready", "delivering", "arrived"].includes(order.status || "")
  );
}

export function needsFinancialFollowUp(order: FollowUpOrder) {
  return refundNeedsFollowUp(order) || paymentNeedsFollowUp(order);
}

export function needsMerchantFollowUp(order: FollowUpOrder) {
  return needsFinancialFollowUp(order) || deliveryNeedsFollowUp(order);
}

export function followUpReasons(order: FollowUpOrder): FollowUpReason[] {
  const reasons: FollowUpReason[] = [];
  if (paymentNeedsFollowUp(order))
    reasons.push({
      kind: "payment",
      label:
        order.payment_review_required || order.payment?.status === "review"
          ? "付款需人工核对"
          : "付款结果待确认",
    });
  if (refundNeedsFollowUp(order))
    reasons.push({
      kind: "refund",
      label: refundLabels[order.refund?.status || ""] || "退款状态待确认",
    });
  if (deliveryNeedsFollowUp(order))
    reasons.push({ kind: "delivery", label: "配送异常待处理" });
  return reasons;
}

export function followUpPriority(order: FollowUpOrder) {
  if (
    order.payment_review_required ||
    order.payment?.status === "review" ||
    ["closed", "abnormal"].includes(order.refund?.status || "")
  )
    return 0;
  if (deliveryNeedsFollowUp(order)) return 1;
  return needsFinancialFollowUp(order) ? 2 : 3;
}
