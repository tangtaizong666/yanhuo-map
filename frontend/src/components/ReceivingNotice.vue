<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { Clock3 } from "lucide-vue-next";
import type { Stall } from "../lib/types";
const props = defineProps<{ stall: Stall; compact?: boolean }>();
const now = ref(performance.now());
const receivedAt = ref(now.value);
const initialAge = ref<number | null>(null);
watch(
  () =>
    [
      props.stall.id,
      props.stall.receiving_seen_at,
      props.stall.receiving_status,
      props.stall.receiving_age_seconds,
    ].join(":"),
  () => {
    now.value = receivedAt.value = performance.now();
    const age = props.stall.receiving_age_seconds;
    // The phone clock can differ from the server. Prefer server age and use
    // monotonic elapsed time while this response remains on screen.
    if (typeof age === "number" && Number.isFinite(age))
      initialAge.value = Math.max(0, age);
    else if (props.stall.receiving_status === "recent") initialAge.value = 0;
    else {
      const seen = Date.parse(props.stall.receiving_seen_at || "");
      initialAge.value = Number.isFinite(seen)
        ? Math.max(0, (Date.now() - seen) / 1000)
        : null;
    }
  },
  { immediate: true },
);
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  timer = setInterval(() => {
    now.value = performance.now();
  }, 15000);
});
onUnmounted(() => clearInterval(timer));
const state = computed(() => {
  if (props.stall.receiving_status === "stale") return "stale";
  if (props.stall.receiving_status === "unknown" || initialAge.value === null)
    return "unknown";
  return initialAge.value + (now.value - receivedAt.value) / 1000 > 90
    ? "stale"
    : "recent";
});
</script>
<template>
  <p
    v-if="stall.can_order && state !== 'recent'"
    class="receiving-notice"
    :class="{ compact }"
    role="status"
  >
    <Clock3 :size="14" />
    <span
      >{{
        state === "stale"
          ? "商家接单页面近期未更新，可能回复较慢"
          : "暂无法确认接单页面状态"
      }}<small v-if="!compact"
        >仍可下单；以商家接单结果为准，超时未接单会自动取消。</small
      ></span
    >
  </p>
</template>
<style scoped>
.receiving-notice {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 12px 14px;
  margin: 12px 0;
  color: #86511f;
  background: #fff5e6;
  border: 1px solid #ecd9be;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
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
.receiving-notice.compact {
  font-size: 11px;
  padding: 7px 9px;
  margin: 10px 0 0;
}
</style>
