<script lang="ts">
import { needsFinancialFollowUp } from "../../lib/orderFollowUp";
export function canConfirmOfflinePayment(order: any) {
  return (
    order.fulfillment_type !== "delivery" &&
    (order.payment_method || "offline") === "offline" &&
    order.payment_status === "unpaid" &&
    !order.payment_review_required &&
    (order.payment == null || order.payment.status === "closed")
  );
}

export function merchantPaymentLabel(order: any) {
  const label = paymentLabel(order);
  return order.mode === "simulation" ? `模拟 · ${label}` : label;
}
function paymentLabel(order: any) {
  if (order.payment_review_required || order.payment?.status === "review")
    return "付款需人工核对";
  if (order.refund?.status === "closed") return "退款未完成 · 申请已关闭";
  if (order.refund?.status === "abnormal") return "退款异常 · 待跟进";
  if (order.refund?.status === "reconcile") return "退款结果待确认";
  if (order.refund && order.refund.status !== "success") return "退款处理中";
  if (order.payment?.status === "reconcile") return "付款核对中";
  if (order.payment_status === "refunded") return "已原路退款";
  if (order.payment_status === "refunding") return "退款处理中";
  if (order.payment_status === "paid")
    return order.payment_method === "wechat" ? "微信已付款" : "到摊已收款";
  if (order.payment?.status === "creating") return "正在发起微信支付";
  if (order.payment?.status === "pending") return "等待微信付款";
  if (
    order.fulfillment_type === "delivery" &&
    order.payment_status === "unpaid"
  )
    return "待顾客微信付款";
  return order.payment_method === "wechat" ? "等待微信确认" : "到摊付款";
}

export function merchantPaymentAmountLabel(order: any) {
  if (order.payment_review_required || order.payment?.status === "review")
    return "订单";
  if (["closed", "abnormal"].includes(order.refund?.status)) return "待退";
  if (order.payment_status === "refunded") return "已退";
  if (order.payment_status === "refunding") return "退款中";
  return order.payment_status === "paid" ? "已收" : "应收";
}
</script>

<script setup lang="ts">
import { computed } from "vue";
import { CircleCheck, Clock3, ShieldCheck, Wallet } from "lucide-vue-next";
import { formatTime, money } from "../../lib/api";

const props = withDefaults(defineProps<{ order: any; compact?: boolean }>(), {
  compact: false,
});
const label = computed(() => merchantPaymentLabel(props.order));
const isPaid = computed(
  () =>
    props.order.payment_status === "paid" &&
    !needsFinancialFollowUp(props.order),
);
const hasOnlinePayment = computed(
  () => props.order.payment_method === "wechat" || !!props.order.payment,
);
const description = computed(() => {
  const order = props.order;
  if (order.payment_review_required || order.payment?.status === "review")
    return order.mode === "simulation"
      ? "模拟付款出现待核对情况，请联系运营核对，并提供订单号。商家不能手动标记核对完成；此订单不涉及真实资金。"
      : "付款信息存在异常，请联系运营核对，并提供订单号。核对完成前不能线下收款、退款或核销。";
  if (order.refund?.status === "closed")
    return order.mode === "simulation"
      ? "模拟退款申请已关闭，尚未完成。可查询原退款进度或在更多操作中继续模拟结果；没有真实资金变动。"
      : "退款申请已关闭，款项尚未退还。请查询原退款结果并联系运营核对，不要另行线下退付；订单不会因此恢复履约。";
  if (order.refund?.status === "abnormal")
    return order.mode === "simulation"
      ? "模拟退款出现异常，尚未完成。可在更多操作中继续模拟原退款的结果；没有真实资金变动。"
      : "微信退款出现异常，尚未完成。请查询原退款进度并联系运营或支付服务方处理，不要另行线下退付或核销取餐。";
  if (order.refund?.status === "reconcile")
    return order.mode === "simulation"
      ? "模拟退款结果待确认，可查询原退款进度或继续模拟结果；没有真实资金变动。"
      : "原退款请求结果待确认，可查询原退款进度。请勿重新发起另一笔退款或线下退付。";
  if (order.payment?.status === "reconcile")
    return order.mode === "simulation"
      ? "模拟付款结果待确认，请让学生在原订单付款区继续确认。商家不能手动标记已付款；不会真实扣款。"
      : "系统正在核对微信付款结果。可刷新订单，或请顾客在原订单中查询付款进度；结果明确前不要另行收款。";
  if (order.mode === "simulation") {
    if (order.payment_status === "refunded")
      return "模拟退款已完成，没有真实资金退回。";
    if (order.payment_status === "refunding")
      return "模拟退款尚未完成，可在更多操作中继续模拟退款结果；没有真实资金变动。";
    if (order.payment_status === "paid")
      return order.fulfillment_type === "delivery"
        ? "已记录模拟付款。练习送达交接点后，仍需学生确认收餐或核验收餐码。没有真实收款或配送。"
        : "已记录模拟付款。交餐时继续练习核验取餐码，没有真实收款。";
    return "这是模拟订单。请让学生在订单详情完成模拟付款，不会真实扣款。";
  }
  if (order.payment_status === "refunded")
    return "微信已确认原路退款，请勿再次收款。";
  if (order.payment_status === "refunding")
    return "退款请求正在核对，请等待微信结果，不要重复退付或核销取餐。";
  if (order.payment_status === "paid")
    return order.payment_method === "wechat"
      ? order.fulfillment_type === "delivery"
        ? "微信系统已确认付款。到达交接点后，由顾客确认收餐，或当面核验 8 位收餐码完成交付。"
        : "微信系统已确认付款，无需手工确认收款。交餐时仍需核验 8 位取餐码。"
      : "已记录到摊收款，交餐时仍需核验 8 位取餐码。";
  if (["creating", "pending"].includes(order.payment?.status))
    return "顾客正在通过微信付款。由系统确认结果，请勿重复收取线下款项。";
  if (order.fulfillment_type === "delivery")
    return "配送订单需先微信付款，系统确认后才能接单。请勿线下补收或在未付款时制作配送。";
  if (order.payment_method === "wechat")
    return "等待微信支付状态同步，暂不能手工确认收款。";
  if (order.payment?.status === "closed")
    return "微信支付已关闭，可在顾客到摊实际付款后确认收款。";
  return "请在顾客到摊实际付款后确认收款。";
});
</script>

<template>
  <div
    class="merchant-payment-info"
    :class="{
      compact,
      paid: isPaid,
      online: hasOnlinePayment,
      refunded: order.payment_status === 'refunded',
    }"
  >
    <component
      :is="
        isPaid
          ? CircleCheck
          : order.payment_status === 'refunded'
            ? ShieldCheck
            : hasOnlinePayment
              ? Clock3
              : Wallet
      "
      :size="17"
      aria-hidden="true"
    />
    <div>
      <strong>{{ label }}</strong>
      <p>{{ description }}</p>
      <small v-if="!compact && order.paid_at">
        付款确认：{{ formatTime(order.paid_at) }}
      </small>
      <template v-if="!compact && order.refund">
        <small v-if="order.refund.created_at"
          >退款申请：{{ formatTime(order.refund.created_at) }}</small
        >
        <small>退款金额：¥{{ money(order.refund.amount_cents) }}</small>
        <small v-if="order.refund.reason"
          >退款原因：{{ order.refund.reason }}</small
        >
        <small v-if="order.refund.completed_at">
          退款完成：{{ formatTime(order.refund.completed_at) }}
        </small>
      </template>
      <p v-if="order.refund?.error_message" class="refund-alert">
        退款说明：{{ order.refund.error_message }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.merchant-payment-info {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border: 1px solid #ecdfcb;
  border-radius: 14px;
  padding: 14px;
  background: #fbf5e9;
  color: #755227;
  margin: 12px 0;
}
.merchant-payment-info > svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.merchant-payment-info > div {
  min-width: 0;
}
.merchant-payment-info strong {
  display: block;
  font-size: 13px;
}
.merchant-payment-info p {
  font-size: 12px;
  line-height: 1.65;
  margin: 5px 0 0;
  overflow-wrap: anywhere;
}
.merchant-payment-info small {
  display: block;
  margin-top: 7px;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.merchant-payment-info.paid {
  background: #f0f7f1;
  border-color: #d9e7dc;
  color: #345b40;
}
.merchant-payment-info.refunded {
  background: #f5f3f0;
  border-color: #e6e0d8;
  color: #5d564e;
}
.merchant-payment-info.compact {
  padding: 10px 12px;
}
.merchant-payment-info .refund-alert {
  color: #9a391e;
}
</style>
