<script setup lang="ts">
import { CreditCard, Wallet, Clock3, ChevronDown } from "lucide-vue-next";
import type { PaymentReadiness } from "../lib/types";

defineProps<{ readiness?: PaymentReadiness; delivery?: boolean }>();
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
          delivery
            ? "配送先付款，付款成功后商家才会接单。"
            : "先下单，商家出餐后再付款。"
        }}
      </p>
    </header>
    <div class="method-row wechat-method">
      <span class="method-icon"><CreditCard :size="22" /></span>
      <div class="method-copy">
        <h3>
          {{ readiness?.mode === "simulation" ? "模拟微信支付" : "微信支付" }}
          <span>线上支付</span>
        </h3>
        <p>
          {{
            readiness?.mode === "simulation"
              ? readiness.available
                ? "模拟付款，不会真实扣款。"
                : "模拟线上支付暂未开启。"
              : readiness?.available
                ? delivery
                  ? "提交后在订单页主动付款。"
                  : "出餐后在订单页付款。"
                : delivery
                  ? "微信支付尚未开通，暂时无法提交配送订单。"
                  : "暂不能在线扣款，到摊扫摊主收款码付款。"
          }}
        </p>
      </div>
      <span class="method-state" :class="{ available: readiness?.available }">{{
        readiness?.available
          ? delivery
            ? "下单后支付"
            : "出餐后可支付"
          : "尚未开通"
      }}</span>
    </div>
    <div v-if="!delivery" class="method-row offline-method">
      <span class="method-icon"><Wallet :size="22" /></span>
      <div class="method-copy">
        <h3>到摊付款</h3>
        <p>
          {{
            readiness?.mode === "simulation"
              ? "可演练商家确认模拟收款后取餐。"
              : "取餐时扫摊主本人的收款码付款，平台不经手款项。"
          }}
        </p>
      </div>
      <span class="method-state available">可使用</span>
    </div>
    <details v-if="!readiness?.available" class="payment-explanation">
      <summary>
        {{
          readiness?.mode === "simulation"
            ? "怎样开启模拟支付？"
            : "为什么微信支付尚未开通？"
        }}<ChevronDown :size="16" />
      </summary>
      <p>
        {{ readiness?.reason || "该商家暂未开通微信支付，到摊扫摊主收款码付款。" }}
      </p>
      <p>
        {{
          readiness?.mode === "simulation"
            ? "商家打开“店铺 → 经营服务 → 线上支付”即可试跑。"
            : "微信支付开通后会在这里显示可用状态，提交订单本身不会扣款。"
        }}
      </p>
    </details>
    <p v-else class="payment-timing">
      <Clock3 :size="15" />{{
        delivery
          ? "餐费与配送费一起支付；提交后仍需主动完成付款。"
          : "提交订单不会扣款，无需现在付款。"
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
