<script setup lang="ts">
import {
  CreditCard,
  FlaskConical,
  ChevronDown,
  CheckCircle2,
} from "lucide-vue-next";
import { money } from "../lib/api";
defineProps<{
  amount: number;
  busy: boolean;
  allowed: boolean;
  pending: boolean;
}>();
defineEmits<{ result: [outcome: "success" | "failure" | "pending"] }>();
</script>

<template>
  <div class="simulation-cashier" aria-label="模拟收银台">
    <div class="cashier-title">
      <span><CreditCard :size="26" /></span>
      <div>
        <h3>微信支付 · 模拟收银台</h3>
        <p>无需打开微信，不会扣取真实费用。</p>
      </div>
    </div>
    <div class="cashier-amount">
      <span>本次模拟金额</span><strong>¥{{ money(amount) }}</strong>
    </div>
    <p v-if="pending" class="sim-pending" role="status">
      正在模拟“付款结果待确认”。订单会保留待付款状态，你可以在下面选择确认成功或失败。
    </p>
    <button
      class="btn btn-primary simulation-pay"
      :disabled="busy || !allowed"
      @click="$emit('result', 'success')"
    >
      <CheckCircle2 :size="19" />{{ busy ? "正在处理…" : "模拟支付成功" }}
    </button>
    <p v-if="!allowed" class="cashier-note">
      当前不能继续模拟付款，请刷新订单确认状态。
    </p>
    <details class="simulation-scenarios">
      <summary>
        <FlaskConical :size="16" />试试其他付款情况<ChevronDown :size="15" />
      </summary>
      <p>用于检查失败、等待及重试时的页面表现。</p>
      <div>
        <button
          class="btn btn-secondary"
          :disabled="busy || !allowed"
          @click="$emit('result', 'failure')"
        >
          模拟支付失败</button
        ><button
          class="btn btn-ghost"
          :disabled="busy || !allowed"
          @click="$emit('result', 'pending')"
        >
          模拟结果待确认
        </button>
      </div>
    </details>
  </div>
</template>

<style scoped>
.simulation-cashier {
  border: 1px solid #d8e4d2;
  border-radius: 16px;
  background: #f5f9f0;
  padding: 24px;
  margin: 20px 0 12px;
}
.cashier-title {
  display: flex;
  align-items: center;
  gap: 12px;
}
.cashier-title > span {
  display: grid;
  place-items: center;
  background: #e4eddc;
  color: #52754a;
  border-radius: 13px;
  width: 48px;
  height: 48px;
  flex: none;
}
h3 {
  font-size: 16px;
  margin: 0;
}
.cashier-title p {
  font-size: 12px;
  color: #65705d;
  margin: 6px 0 0;
  line-height: 1.8;
}
.cashier-amount {
  text-align: center;
  padding: 27px 0 23px;
}
.cashier-amount span {
  display: block;
  font-size: 12px;
  color: #77846b;
}
.cashier-amount strong {
  display: block;
  font-size: 37px;
  color: #345c3a;
  margin-top: 7px;
  letter-spacing: -1px;
}
.simulation-pay {
  width: 100%;
  justify-content: center;
}
.cashier-note,
.sim-pending {
  font-size: 12px;
  line-height: 1.8;
  color: #776345;
}
.sim-pending {
  padding: 12px;
  background: #fff4dd;
  border-radius: 9px;
}
.simulation-scenarios {
  border-top: 1px solid #dae3d3;
  margin-top: 20px;
  padding-top: 8px;
}
.simulation-scenarios summary {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  cursor: pointer;
  color: #68795e;
  font-size: 12px;
  list-style: none;
}
.simulation-scenarios summary::-webkit-details-marker {
  display: none;
}
.simulation-scenarios summary > svg:last-child {
  margin-left: auto;
}
.simulation-scenarios p {
  font-size: 12px;
  color: #77846b;
  line-height: 1.8;
}
.simulation-scenarios > div {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.simulation-scenarios button {
  font-size: 12px;
}
@media (max-width: 600px) {
  .simulation-cashier {
    padding: 20px 15px;
  }
  .cashier-title {
    gap: 10px;
  }
  h3 {
    font-size: 14px;
  }
  .cashier-title p {
    font-size: 11px;
  }
  .cashier-amount strong {
    font-size: 33px;
  }
}
</style>
