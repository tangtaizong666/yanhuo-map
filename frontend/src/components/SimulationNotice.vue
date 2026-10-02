<script setup lang="ts">
import { FlaskConical } from "lucide-vue-next";
defineProps<{ delivery?: boolean }>();
</script>
<template>
  <div class="simulation-notice" role="note">
    <FlaskConical :size="18" />
    <div>
      <strong>模拟体验</strong
      ><span>{{
        delivery
          ? "付款与配送仅用于演练，不会真实扣款或安排送货。"
          : "本单仅用于流程试跑，不会发生真实扣款。"
      }}</span>
    </div>
  </div>
</template>
<style scoped>
.simulation-notice {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid #e9dcbd;
  background: #fcf5e5;
  color: #88672f;
  padding: 14px 18px;
  border-radius: 13px;
  font-size: 12px;
  line-height: 1.8;
  margin-bottom: 20px;
}
.simulation-notice > svg {
  flex: none;
}
.simulation-notice strong {
  margin-right: 10px;
  font-size: 13px;
}
@media (max-width: 600px) {
  .simulation-notice {
    padding: 13px;
    align-items: flex-start;
  }
  .simulation-notice strong {
    display: block;
  }
  .simulation-notice > svg {
    margin-top: 3px;
  }
}
</style>
