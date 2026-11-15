<script setup lang="ts">
import { ChevronDown, FlaskConical } from "lucide-vue-next";
defineProps<{ delivery?: boolean }>();
</script>
<template>
  <details class="simulation-notice" role="note">
    <summary>
      <FlaskConical :size="16" />
      <strong>模拟体验</strong>
      <span>{{
        delivery
          ? "不会真实扣款或安排送货"
          : "本单仅作演练，不会发生真实扣款"
      }}</span>
      <ChevronDown :size="14" class="sim-chevron" />
    </summary>
    <p class="simulation-detail">
      {{
        delivery
          ? "付款与配送仅用于校园演示试跑，可在订单页模拟付款并在商家端推进节点，不会真实扣款或安排送货。"
          : "提交后可在商家工作台体验接单、出餐与取餐码核销全流程，不会发生真实扣款。"
      }}
    </p>
  </details>
</template>
<style scoped>
.simulation-notice {
  border: 1px solid #e9dcbd;
  background: #fcf5e5;
  color: #88672f;
  padding: 0 14px;
  border-radius: 13px;
  font-size: 12px;
  line-height: 1.65;
  margin-bottom: 16px;
}
.simulation-notice summary {
  list-style: none;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  cursor: pointer;
}
.simulation-notice summary::-webkit-details-marker {
  display: none;
}
.simulation-notice summary > svg:first-child {
  flex: none;
}
.simulation-notice strong {
  font-size: 13px;
  flex-shrink: 0;
}
.simulation-notice summary > span {
  flex: 1;
  min-width: 0;
  color: #93733b;
}
.sim-chevron {
  flex: none;
  transition: transform 0.16s ease;
}
.simulation-notice[open] .sim-chevron {
  transform: rotate(180deg);
}
.simulation-detail {
  margin: 0;
  padding: 0 0 12px 24px;
  color: #7d6130;
  font-size: 12px;
}
</style>
