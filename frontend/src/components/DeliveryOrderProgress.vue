<script setup lang="ts">
import { computed, ref } from "vue";
import {
  Bike,
  Check,
  MapPin,
  Navigation,
  PackageCheck,
  Clock3,
  AlertCircle,
} from "lucide-vue-next";
import type { Order } from "../lib/types";
import { formatTime, routeUrl } from "../lib/api";
import { needsFinancialFollowUp } from "../lib/orderFollowUp";
import { allows } from "../lib/orderActions";
const props = defineProps<{ order: Order; busy: boolean }>();
const emit = defineEmits<{ confirm: [] }>();
const confirming = ref(false);
const states = [
  "pending_payment",
  "pending",
  "preparing",
  "ready",
  "delivering",
  "arrived",
  "completed",
];
const labels = [
  "待付款",
  "待接单",
  "制作中",
  "待配送",
  "配送中",
  "到交接点",
  "已收餐",
];
const stage = computed(() => states.indexOf(props.order.status));
const nav = computed(() =>
  props.order.mode !== "simulation" &&
  props.order.delivery_point_latitude != null &&
  props.order.delivery_point_longitude != null
    ? routeUrl(
        props.order.delivery_point_latitude,
        props.order.delivery_point_longitude,
        props.order.delivery_point_name || "校园交接点",
      )
    : "",
);
const canReceive = computed(
  () =>
    allows(props.order, "confirm_receipt", true) &&
    props.order.status === "arrived" &&
    props.order.payment_status === "paid" &&
    !needsFinancialFollowUp(props.order) &&
    !props.order.cancel_requested &&
    !props.order.delivery_issue,
);
const description = computed(
  () =>
    (
      ({
        pending_payment: "微信付款成功后，订单才会交给商家处理。",
        pending: "已收到付款，等待商家确认。超时未接单会取消并发起退款。",
        preparing: "商家正在准备餐点，做好后安排配送。",
        ready: "餐点已备好，等待商家出发送餐。",
        delivering: "商家已出发送餐，到交接点后会更新状态。",
        arrived: "餐点已到所选交接点，请按下方位置收餐。",
        completed: "已确认收到餐点，感谢你的光顾。",
      }) as Record<string, string>
    )[props.order.status] || props.order.cancel_reason,
);
</script>

<template>
  <section class="delivery-progress" aria-label="配送进度">
    <header>
      <span class="delivery-icon"><Bike :size="24" /></span>
      <div>
        <small>{{
          order.mode === "simulation" ? "商家配送 · 模拟演练" : "商家自配送"
        }}</small>
        <h2>{{ labels[stage] || "配送已结束" }}</h2>
      </div>
    </header>
    <p class="delivery-description">
      {{
        order.mode === "simulation"
          ? "当前为模拟配送，请在商家工作台操作接单、出餐、出发和送达，体验完整流程。不会安排实际送货。"
          : description
      }}
    </p>
    <p
      v-if="order.status === 'pending_payment' && order.expires_at"
      class="node-time"
    >
      付款截止：{{
        formatTime(order.expires_at)
      }}。支付结果未确认时请勿重复付款。
    </p>
    <ol v-if="stage >= 0" class="delivery-timeline">
      <li
        v-for="(label, index) in labels"
        :key="label"
        :class="{ done: index < stage, current: index === stage }"
        :aria-current="index === stage ? 'step' : undefined"
      >
        <span
          ><Check v-if="index < stage" :size="13" /><template v-else>{{
            index + 1
          }}</template></span
        ><b>{{ label }}</b>
      </li>
    </ol>
    <div v-if="order.delivery_issue" class="delivery-alert" role="status">
      <AlertCircle :size="19" />
      <div>
        <strong>配送遇到情况</strong>
        <p>{{ order.delivery_issue }}</p>
        <p>请联系商家处理，订单不会自动完成。</p>
      </div>
    </div>
    <div
      v-if="order.delivery_eta_min_at && order.delivery_eta_max_at"
      class="eta-note"
    >
      <Clock3 :size="17" /><span
        >下单时预计 {{ formatTime(order.delivery_eta_min_at) }}—{{
          formatTime(order.delivery_eta_max_at)
        }}
        送达<br /><small>{{
          order.mode === "simulation"
            ? "模拟时段仅用于演练，进度由商家工作台操作推进。"
            : "预计时段供参考，当前页面显示真实操作节点，无实时配送轨迹。"
        }}</small></span
      >
    </div>
    <div class="delivery-destination">
      <MapPin :size="22" />
      <div>
        <small>本单校园交接点</small>
        <h3>{{ order.delivery_point_name }}</h3>
        <p>{{ order.delivery_point_address }}</p>
        <p v-if="order.recipient_name">
          {{ order.recipient_name }} · {{ order.contact_phone }}
        </p>
      </div>
      <a
        v-if="nav"
        :href="nav"
        target="_blank"
        rel="noopener noreferrer"
        class="btn btn-secondary"
        aria-label="前往交接点的路线"
        ><Navigation :size="15" />路线</a
      >
    </div>
    <p v-if="order.dispatched_at" class="node-time">
      商家出发 {{ formatTime(order.dispatched_at)
      }}<template v-if="order.arrived_at">
        · 到交接点 {{ formatTime(order.arrived_at) }}</template
      >
    </p>
    <div v-if="canReceive" class="receipt-confirm">
      <PackageCheck :size="28" />
      <div>
        <h3>
          {{
            order.mode === "simulation"
              ? "最后一步：模拟收餐"
              : "收到餐点后，再确认"
          }}
        </h3>
        <p>
          {{
            order.mode === "simulation"
              ? "商家已模拟送达，你可以确认收餐来完成这次演练。"
              : "当面向配送人员出示收餐码，或亲自确认收餐。不要提前分享。"
          }}
        </p>
        <strong class="receipt-code" aria-label="收餐码">{{
          order.pickup_code
        }}</strong>
      </div>
      <button
        v-if="!confirming"
        class="btn btn-primary"
        :disabled="busy"
        @click="confirming = true"
      >
        {{ order.mode === "simulation" ? "模拟收到餐点" : "我已收到餐点" }}
      </button>
      <div
        v-else
        class="receipt-confirmation"
        role="group"
        aria-label="确认实际收餐"
      >
        <p>
          {{
            order.mode === "simulation"
              ? "确认后，本次模拟配送订单将完成。"
              : "请确认你已实际拿到这份餐点。"
          }}
        </p>
        <button
          class="btn btn-primary"
          :disabled="busy"
          @click="emit('confirm')"
        >
          {{ busy ? "正在确认…" : "确认已收餐" }}</button
        ><button
          class="btn btn-ghost"
          :disabled="busy"
          @click="confirming = false"
        >
          还没收到
        </button>
      </div>
    </div>
    <p v-else-if="order.status === 'arrived'" class="delivery-description">
      {{
        needsFinancialFollowUp(order)
          ? "本单款项仍待处理，请先联系商家核对，暂时不要重复付款或分享收餐码。"
          : "当前订单有待处理事项，请联系商家确认后再收餐。"
      }}
    </p>
  </section>
</template>

<style scoped>
header {
  display: flex;
  align-items: center;
  gap: 13px;
}
.delivery-icon {
  width: 50px;
  height: 50px;
  background: #fff0df;
  color: #c15f2d;
  border-radius: 15px;
  display: grid;
  place-items: center;
}
header small {
  color: #84745e;
  font-size: 12px;
}
h2 {
  font-size: 23px;
  margin: 4px 0 0;
}
.delivery-description {
  font-size: 13px;
  line-height: 1.8;
  color: #766953;
}
.delivery-timeline {
  display: flex;
  gap: 0;
  padding: 16px 0 20px;
  list-style: none;
  margin: 0;
}
.delivery-timeline li {
  flex: 1;
  position: relative;
  text-align: center;
  color: #897f70;
  min-width: 0;
}
.delivery-timeline li::before {
  content: "";
  position: absolute;
  top: 12px;
  left: -50%;
  width: 100%;
  height: 2px;
  background: #e6e0d6;
}
.delivery-timeline li:first-child::before {
  display: none;
}
.delivery-timeline li.done::before,
.delivery-timeline li.current::before {
  background: #de8a4c;
}
.delivery-timeline span {
  display: grid;
  place-items: center;
  position: relative;
  margin: 0 auto 9px;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #eee9e0;
  font-size: 11px;
}
.delivery-timeline b {
  font-size: 11px;
  font-weight: 500;
}
.delivery-timeline .current,
.delivery-timeline .done {
  color: #b65524;
}
.delivery-timeline .current span,
.delivery-timeline .done span {
  background: #d77337;
  color: white;
}
.eta-note {
  display: flex;
  gap: 9px;
  color: #6e775b;
  background: #f2f5eb;
  border-radius: 12px;
  padding: 14px;
  font-size: 12px;
  line-height: 1.8;
}
.eta-note svg {
  flex: none;
  margin-top: 3px;
}
.eta-note small {
  color: #7b7d70;
  font-size: 11px;
}
.delivery-destination {
  display: flex;
  align-items: center;
  gap: 13px;
  margin: 24px 0 14px;
}
.delivery-destination > svg {
  flex: none;
  color: #c76c35;
}
.delivery-destination > div {
  flex: 1;
  min-width: 0;
}
.delivery-destination small {
  font-size: 11px;
  color: #84745e;
}
.delivery-destination h3 {
  font-size: 17px;
  margin: 5px 0;
}
.delivery-destination p {
  margin: 4px 0;
  font-size: 12px;
  line-height: 1.7;
  color: #736650;
  overflow-wrap: anywhere;
}
.delivery-destination .btn {
  flex: none;
}
.node-time {
  font-size: 11px;
  color: #82745e;
  line-height: 1.8;
}
.receipt-confirm {
  display: flex;
  gap: 13px;
  flex-wrap: wrap;
  padding: 20px;
  background: #fff1e0;
  border: 1px solid #efd8bb;
  border-radius: 16px;
  margin-top: 22px;
}
.receipt-confirm > svg {
  flex: none;
  color: #bb642e;
}
.receipt-confirm > div {
  flex: 1;
  min-width: 0;
}
.receipt-confirm h3 {
  font-size: 16px;
  margin: 0;
}
.receipt-confirm p {
  font-size: 12px;
  line-height: 1.8;
  color: #846b4e;
  margin: 8px 0;
}
.receipt-code {
  display: block;
  font-size: 28px;
  letter-spacing: 4px;
  color: #a95624;
  margin: 10px 0;
}
.receipt-confirm > button,
.receipt-confirm .receipt-confirmation {
  flex-basis: 100%;
}
.receipt-confirmation .btn {
  margin: 3px 7px 0 0;
}
.delivery-alert {
  display: flex;
  gap: 10px;
  padding: 15px;
  border-radius: 12px;
  background: #fff0e5;
  color: #a74d26;
  margin-bottom: 18px;
}
.delivery-alert svg {
  flex: none;
}
.delivery-alert strong {
  font-size: 13px;
}
.delivery-alert p {
  font-size: 12px;
  line-height: 1.8;
  margin: 5px 0 0;
}
@media (max-width: 600px) {
  .delivery-timeline {
    flex-wrap: wrap;
    gap: 13px 0;
  }
  .delivery-timeline li {
    flex: 0 0 25%;
  }
  .delivery-timeline li:nth-child(5)::before {
    display: none;
  }
  .delivery-destination {
    flex-wrap: wrap;
  }
  .delivery-destination .btn {
    margin-left: 35px;
  }
  .receipt-confirm {
    padding: 17px 13px;
  }
  .receipt-code {
    font-size: 25px;
    letter-spacing: 3px;
  }
}
</style>
