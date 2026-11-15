<script setup lang="ts">
import { CreditCard, ShieldCheck, ChevronDown } from "lucide-vue-next";
import { computed } from "vue";
import type { PaymentReadiness } from "../lib/types";

const props = defineProps<{
  readiness?: PaymentReadiness;
  delivery?: boolean;
}>();
const onlineSupported = computed(
  () =>
    props.readiness?.mode === "simulation" ||
    props.readiness?.supported !== false,
);
</script>

<template>
  <section
    class="card checkout-payment-methods"
    aria-labelledby="checkout-payment-heading"
  >
    <header>
      <h2 id="checkout-payment-heading">支付方式</h2>
      <p>
        {{
          !onlineSupported
            ? "该商户仅提供找摊与菜品展示，暂不接受线上订单。"
            : delivery
              ? "在线预付餐费与配送费，付款确认后立即进入备餐与配送。"
              : "在线预付锁单，付款确认后立即备餐，凭 4 位取餐码到摊即拿。"
        }}
      </p>
    </header>
    <div v-if="onlineSupported" class="method-row wechat-method">
      <span class="method-icon"><CreditCard :size="22" /></span>
      <div class="method-copy">
        <h3>
          {{ readiness?.mode === "simulation" ? "模拟微信支付" : "微信支付" }}
          <span>线上担保预付</span>
        </h3>
        <p>
          {{
            readiness?.mode === "simulation"
              ? readiness.available
                ? "提交后自动拉起模拟收银台，不会真实扣款。"
                : "模拟线上支付暂未开启。"
              : readiness?.available
                ? "提交后直接发起微信支付，商家无需手动确认收款。"
                : "微信支付尚未开通，暂时无法提交线上订单。"
          }}
        </p>
      </div>
      <span class="method-state" :class="{ available: readiness?.available }">{{
        readiness?.available ? "下单即付" : "尚未开通"
      }}</span>
    </div>
    <p v-if="!onlineSupported" class="payment-timing">
      找到摊位后，请现场向摊主选购餐点。
    </p>
    <details v-else-if="!readiness?.available" class="payment-explanation">
      <summary>
        {{
          readiness?.mode === "simulation"
            ? "怎样开启模拟支付？"
            : "为什么微信支付尚未开通？"
        }}<ChevronDown :size="16" />
      </summary>
      <p>
        {{
          readiness?.reason || "该商家暂未完成微信特约商户收款配置，暂时无法接收线上预付订单。"
        }}
      </p>
      <p>
        {{
          readiness?.mode === "simulation"
            ? "商家打开“店铺 → 经营服务 → 线上支付”即可恢复演练点单。"
            : "平台统一采用线上预付与自动分账结算，未开通线上支付时暂不开放线上点单。"
        }}
      </p>
    </details>
    <p v-else class="payment-timing">
      <ShieldCheck :size="15" />{{
        delivery
          ? "付款后商家自动/快速接单；开始制作前可秒退，下锅制作后需协商取消。"
          : "付款后即刻备餐防跑单；接单制作前可随时秒退，出餐后凭 4 位码直接取走。"
      }}
    </p>
  </section>
</template>

<style scoped>
.checkout-payment-methods {
  padding: 22px 25px;
  background: linear-gradient(150deg, #fffefb, #fff);
}
header {
  margin-bottom: 14px;
}
.eyebrow {
  color: #74866e;
  font-size: 10px;
  letter-spacing: 1.7px;
}
h2 {
  font-size: 19px;
  margin: 0 0 7px;
}
header p {
  color: #786f62;
  font-size: 12px;
  margin: 0;
  line-height: 1.7;
}
.method-row {
  display: flex;
  align-items: center;
  gap: 13px;
  padding: 11px 13px;
  border: 1px solid #e7e1d7;
  border-radius: 14px;
}
.method-row + .method-row {
  margin-top: 11px;
}
.single-method {
  padding: 0;
  border: 0;
}
.wechat-method {
  background: #f4f8f0;
  border-color: #dce5d3;
}
.method-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 12px;
  flex: none;
  color: #35634b;
  background: #e6eee1;
}
.offline-method .method-icon {
  background: #faefe0;
  color: #aa7040;
}
.method-copy {
  min-width: 0;
  flex: 1;
}
.method-copy h3 {
  font-size: 14px;
  line-height: 1.7;
  margin: 0;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
}
.method-copy h3 span {
  font-size: 10px;
  color: #627c63;
  font-weight: 400;
}
.method-copy p {
  font-size: 12px;
  line-height: 1.8;
  color: #6f7669;
  margin: 4px 0 0;
}
.method-state {
  flex: none;
  color: #6c705f;
  background: #e7eadf;
  border-radius: 6px;
  padding: 5px 8px;
  white-space: nowrap;
  font-size: 11px;
}
.method-state.available {
  color: #426449;
  background: #e9f0e1;
}
.offline-method .method-copy p {
  color: #80745f;
}
.payment-explanation {
  border-top: 1px solid #eee5d7;
  margin-top: 10px;
  padding-top: 3px;
}
.payment-explanation summary {
  min-height: 44px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  font-size: 12px;
  color: #746a5a;
  list-style: none;
}
.payment-explanation summary::-webkit-details-marker {
  display: none;
}
.payment-explanation[open] summary svg {
  transform: rotate(180deg);
}
.payment-explanation p {
  font-size: 12px;
  line-height: 1.8;
  color: #817461;
  margin: 2px 0 10px;
}
.payment-explanation summary:focus-visible {
  outline: 2px solid #4b7451;
  outline-offset: 3px;
  border-radius: 6px;
}
.payment-timing {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #74806c;
  font-size: 11px;
  margin: 11px 0 0;
}
@media (max-width: 600px) {
  .checkout-payment-methods {
    padding: 18px;
  }
  .method-row {
    padding: 10px;
    gap: 8px;
  }
  .method-icon {
    width: 29px;
    height: 29px;
    border-radius: 10px;
  }
  .method-copy {
    flex-basis: auto;
  }
  .method-state {
    margin-left: 0;
    padding: 4px;
    font-size: 10px;
  }
  .method-copy h3 {
    font-size: 14px;
  }
  .method-copy p {
    font-size: 11px;
  }
  .method-row h3 span {
    font-size: 10px;
  }
  h2 {
    font-size: 18px;
  }
}
</style>
