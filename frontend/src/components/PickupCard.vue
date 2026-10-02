<script setup lang="ts">
import { computed, ref } from "vue";
import {
  CheckCircle2,
  Copy,
  MapPin,
  Navigation,
  Phone,
  Wallet,
} from "lucide-vue-next";
import type { Order } from "../lib/types";
import { copyText } from "../lib/engagement";
import { money } from "../lib/api";
import OrderContactHelp from "./OrderContactHelp.vue";
const props = defineProps<{ order: Order; syncError?: boolean }>();
const copied = ref("");
const fallback = ref(false);
const phone = computed(() =>
  (props.order.merchant_contact_phone || "").replace(/[^\d+]/g, ""),
);
const navigation = computed(() =>
  Number.isFinite(props.order.pickup_latitude) &&
  Number.isFinite(props.order.pickup_longitude)
    ? `https://uri.amap.com/navigation?to=${props.order.pickup_longitude},${props.order.pickup_latitude},${encodeURIComponent(props.order.pickup_address)}&mode=walk&coordinate=gaode&callnative=0`
    : "",
);
async function copy() {
  fallback.value = !(await copyText(props.order.pickup_address));
  copied.value = fallback.value ? "可长按下方地址复制。" : "取餐地址已复制";
}
</script>
<template>
  <section class="card pickup-card" aria-label="到摊取餐凭证">
    <div class="pickup-card-heading">
      <span><CheckCircle2 :size="19" />餐点已做好</span
      ><small>{{
        order.mode === "simulation" ? "模拟取餐 · 不发生实际交付" : "到摊自取"
      }}</small>
    </div>
    <h2>{{ order.stall_name }}</h2>
    <p v-if="syncError" class="pickup-sync-error" role="status">
      订单暂未同步，以下为上次读取的信息，请刷新后与商家核对。
    </p>
    <div class="pickup-code">
      <span>向商家出示取餐码</span><strong>{{ order.pickup_code }}</strong
      ><small>请勿向无关人员分享 · 商家确认收款后核销</small>
    </div>
    <p
      class="pickup-payment"
      :class="{ paid: order.payment_status === 'paid' }"
    >
      <Wallet :size="17" />{{
        order.payment_status === "paid"
          ? "款项已确认，可凭码取餐"
          : `应付 ¥${money(order.total_cents)} · 请在下方支付或到摊付款`
      }}
    </p>
    <div class="pickup-card-address">
      <MapPin :size="18" />
      <div>
        <small>下单时的取餐位置</small
        ><strong>{{ order.pickup_address }}</strong>
      </div>
    </div>
    <p v-if="order.location_changed" class="pickup-location-warning">
      商家当前位置有变化。本单仍保留以上位置，请先联系商家确认。
    </p>
    <div class="pickup-card-actions">
      <a
        v-if="navigation"
        :href="navigation"
        target="_blank"
        rel="noopener noreferrer"
        ><Navigation :size="16" />路线</a
      ><button type="button" @click="copy"><Copy :size="16" />复制地址</button
      ><a v-if="phone" :href="`tel:${phone}`"><Phone :size="16" />联系商家</a>
    </div>
    <p v-if="copied" class="pickup-copy-message" role="status">{{ copied }}</p>
    <input
      v-if="fallback"
      class="pickup-copy-input"
      :value="order.pickup_address"
      readonly
      aria-label="可复制的订单取餐地址"
    />
    <template v-if="!phone"
      ><p class="pickup-phone-missing">商家暂未提供联系电话。</p>
      <OrderContactHelp :order="order"
    /></template>
  </section>
</template>
<style scoped>
.pickup-card {
  padding: 24px 28px;
  background: linear-gradient(150deg, #fffef8, #fff4e3);
  border-color: #e8d0ae;
}
.pickup-card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #507447;
}
.pickup-card-heading span {
  display: flex;
  align-items: center;
  gap: 7px;
  font-weight: 600;
  font-size: 13px;
}
.pickup-card-heading small {
  font-size: 10px;
  color: #907154;
  text-align: right;
}
h2 {
  margin: 12px 0 16px;
  font-size: 22px;
  overflow-wrap: anywhere;
}
.pickup-code {
  text-align: center;
  border-top: 1px dashed #dec9a9;
  border-bottom: 1px dashed #dec9a9;
  padding: 16px 0;
}
.pickup-code span {
  display: block;
  color: #826242;
  font-size: 12px;
}
.pickup-code strong {
  display: block;
  font-size: 42px;
  letter-spacing: 5px;
  line-height: 1.4;
  font-variant-numeric: tabular-nums;
  color: #9c471a;
}
.pickup-code small {
  display: block;
  color: #927958;
  font-size: 10px;
}
.pickup-payment {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #a35728;
  font-size: 12px;
  line-height: 1.6;
  margin: 15px 0;
}
.pickup-payment.paid {
  color: #476d3d;
}
.pickup-card-address {
  display: flex;
  align-items: flex-start;
  gap: 9px;
}
.pickup-card-address svg {
  color: #b67640;
  flex-shrink: 0;
  margin-top: 3px;
}
.pickup-card-address small {
  display: block;
  color: #8b755b;
  font-size: 10px;
}
.pickup-card-address strong {
  font-size: 14px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.pickup-card-actions {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  margin-top: 12px;
}
.pickup-card-actions > * {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  flex: 1;
  padding: 8px;
  border: 1px solid #e5cdb0;
  border-radius: 10px;
  color: #96501f;
  font-size: 12px;
  white-space: nowrap;
}
.pickup-location-warning,
.pickup-sync-error {
  padding: 10px;
  background: #fff1df;
  color: #915422;
  font-size: 12px;
  line-height: 1.7;
  border-radius: 9px;
}
.pickup-copy-message,
.pickup-phone-missing {
  font-size: 12px;
  color: #86694b;
  margin: 10px 0 0;
}
.pickup-copy-input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  padding: 10px;
  border: 1px solid #dfc4a0;
  border-radius: 8px;
  min-height: 44px;
  margin-top: 8px;
  font: inherit;
}
.pickup-card :deep(.order-help-open) {
  width: 100%;
  justify-content: flex-start;
}
@media (max-width: 600px) {
  .pickup-card {
    padding: 18px;
  }
  h2 {
    font-size: 20px;
    margin: 9px 0 13px;
  }
  .pickup-code {
    padding: 12px 0;
  }
  .pickup-code strong {
    font-size: 36px;
    letter-spacing: 4px;
  }
  .pickup-payment {
    margin: 12px 0;
  }
  .pickup-card-heading small {
    max-width: 145px;
  }
}
@media (max-width: 360px) {
  .pickup-code strong {
    font-size: 32px;
    letter-spacing: 3px;
  }
}
</style>
