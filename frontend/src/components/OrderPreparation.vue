<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import { ChefHat, Clock3, ShoppingBag } from "lucide-vue-next";
import type { Order } from "../lib/types";
import { needsFinancialFollowUp } from "../lib/orderFollowUp";
const props = defineProps<{ order: Order; syncError?: boolean }>();
const now = ref(Date.now());
const timer = setInterval(() => {
  now.value = Date.now();
}, 15000);
onUnmounted(() => clearInterval(timer));
const estimated = computed(() =>
  props.order.estimated_ready_at
    ? new Date(props.order.estimated_ready_at).getTime()
    : NaN,
);
const hasEstimate = computed(() => Number.isFinite(estimated.value));
const overdue = computed(
  () => hasEstimate.value && estimated.value < now.value,
);
const time = computed(() =>
  hasEstimate.value
    ? new Date(estimated.value).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "",
);
const delivery = computed(() => props.order.fulfillment_type === "delivery");
const collectionBlocked = computed(
  () =>
    needsFinancialFollowUp(props.order) ||
    ["refunding", "refunded"].includes(props.order.payment_status),
);
</script>
<template>
  <div
    v-if="order.status === 'preparing'"
    class="prep-card"
    :class="{ delayed: overdue }"
    role="status"
  >
    <Clock3 v-if="overdue" :size="23" /><ChefHat v-else :size="23" />
    <div>
      <strong>{{
        hasEstimate ? `商家预计 ${time} 出餐` : "商家正在备餐"
      }}</strong>
      <p v-if="syncError">订单暂未同步，请刷新确认最新进度。</p>
      <p v-else-if="overdue">
        已超过预计时间，可能有所延迟；尚未收到商家出餐确认。
      </p>
      <p v-else>
        {{
          hasEstimate
            ? "这是备餐预估，出餐后会更新状态。"
            : "商家尚未提供预计出餐时间，请留意状态更新。"
        }}
      </p>
      <p v-if="order.prep_delay_reason" class="prep-reason">
        商家说明：{{ order.prep_delay_reason }}
      </p>
      <small v-if="delivery">出餐时间不等于送达时间。</small>
    </div>
  </div>
  <div
    v-else-if="
      order.status === 'ready' && !order.cancel_requested && !collectionBlocked
    "
    class="prep-card ready"
    role="status"
  >
    <ShoppingBag :size="24" />
    <div>
      <strong>{{
        delivery ? "餐点已做好，等待商家配送" : "餐点已做好，可以来取餐了"
      }}</strong>
      <p v-if="syncError">订单暂未同步，请刷新确认最新进度。</p>
      <p v-else>
        {{
          delivery
            ? "发出后会更新配送状态，请留意交接点信息。"
            : "请核对下方取餐地点和付款状态，到摊出示取餐码。"
        }}
      </p>
    </div>
  </div>
</template>
<style scoped>
.prep-card {
  display: flex;
  gap: 13px;
  align-items: flex-start;
  padding: 18px;
  margin-bottom: 0;
  border-radius: 15px;
  background: #fff2df;
  color: #8a4b25;
  border: 1px solid #f0dbc1;
}
.prep-card > svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.prep-card > div {
  min-width: 0;
}
.prep-card strong {
  font-size: 17px;
  line-height: 1.5;
}
.prep-card p {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.prep-card small {
  display: block;
  font-size: 11px;
  margin-top: 6px;
}
.prep-card.delayed {
  background: #fff0e7;
  color: #974524;
}
.prep-card.ready {
  background: #eef5e8;
  border-color: #dce8cc;
  color: #46633b;
}
.prep-reason {
  font-weight: 600;
}
@media (max-width: 400px) {
  .prep-card {
    padding: 14px;
    gap: 9px;
  }
  .prep-card strong {
    font-size: 15px;
  }
}
</style>
