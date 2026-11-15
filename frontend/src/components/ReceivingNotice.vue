<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { ChevronDown, Clock3 } from "lucide-vue-next";
import type { Stall } from "../lib/types";
const props = defineProps<{
  stall: Pick<
    Stall,
    "id" | "can_order" | "receiving_status" | "receiving_valid_for_seconds"
  >;
  compact?: boolean;
}>();
const now = ref(performance.now()),
  receivedAt = ref(now.value);
watch(
  () => [
    props.stall.id,
    props.stall.receiving_status,
    props.stall.receiving_valid_for_seconds,
  ],
  () => {
    now.value = receivedAt.value = performance.now();
  },
  { immediate: true },
);
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  timer = setInterval(() => {
    now.value = performance.now();
  }, 5000);
});
onUnmounted(() => clearInterval(timer));
const state = computed(() => {
  if (props.stall.receiving_status !== "recent")
    return props.stall.receiving_status || "unknown";
  const ttl = Math.max(
    0,
    Math.min(30, props.stall.receiving_valid_for_seconds || 0),
  );
  return (now.value - receivedAt.value) / 1000 >= ttl ? "stale" : "recent";
});
</script>
<template>
  <p
    v-if="stall.can_order && state !== 'recent' && compact"
    class="receiving-notice is-compact"
    role="status"
  >
    <Clock3 :size="14" />
    <span>{{
      state === "stale"
        ? "商家接单页面近期未更新，可能回复较慢"
        : "商家可能没在看接单页，回复或许较慢"
    }}</span>
  </p>
  <details
    v-else-if="stall.can_order && state !== 'recent'"
    class="receiving-notice receiving-disclosure"
    role="status"
  >
    <summary>
      <Clock3 :size="14" />
      <span>{{
        state === "stale"
          ? "商家接单页面近期未更新，可能回复较慢"
          : "商家可能没在看接单页，回复或许较慢"
      }}</span>
      <ChevronDown :size="14" class="receiving-chevron" />
    </summary>
    <small>仍可下单；以商家接单结果为准，超时未接单会自动取消。</small>
  </details>
</template>
<style scoped>
.receiving-notice {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 10px 13px;
  margin: 12px 0;
  color: #86511f;
  background: #fff5e6;
  border: 1px solid #ecd9be;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.receiving-disclosure {
  display: block;
  padding: 0 13px;
}
.receiving-disclosure summary {
  list-style: none;
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  cursor: pointer;
}
.receiving-disclosure summary::-webkit-details-marker {
  display: none;
}
.receiving-disclosure summary span {
  flex: 1;
  min-width: 0;
}
.receiving-chevron {
  flex-shrink: 0;
  color: #9e7346;
  transition: transform 0.16s ease;
}
.receiving-disclosure[open] .receiving-chevron {
  transform: rotate(180deg);
}
.receiving-disclosure small {
  display: block;
  padding: 0 0 10px 21px;
  color: #735d47;
  font-size: 12px;
}
.receiving-notice svg {
  flex-shrink: 0;
  margin-top: 4px;
}
.receiving-notice small {
  display: block;
  color: #735d47;
  font-size: 12px;
}
.receiving-notice.is-compact {
  align-items: center;
  font-size: 12px;
  line-height: 1.5;
  padding: 6px 9px;
  margin: 0;
  border-radius: 9px;
  text-align: left;
}
.receiving-notice.is-compact svg {
  margin-top: 0;
}
.receiving-notice.is-compact span {
  flex: 1;
  min-width: 0;
}
</style>
