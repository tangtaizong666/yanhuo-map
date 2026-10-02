<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  ArrowRight,
  ChefHat,
  Clock3,
  RefreshCw,
  ShoppingBag,
} from "lucide-vue-next";
import { api, ApiError } from "../lib/api";
import { useSession } from "../stores/session";
import SessionNotifications from "./SessionNotifications.vue";

interface Summary {
  user_id: number;
  counts: { pending: number; preparing: number; ready: number; total: number };
  order: {
    id: string;
    stall_name: string;
    status:
      | "pending_payment"
      | "pending"
      | "preparing"
      | "ready"
      | "delivering"
      | "arrived";
    fulfillment_type?: "pickup" | "delivery";
    cancel_requested: boolean;
  } | null;
}
const session = useSession();
const summary = ref<Summary | null>(null);
const error = ref(false);
const busy = ref(false);
const order = computed(() => summary.value?.order);
const title = computed(() => {
  if (order.value?.cancel_requested) return "取消申请正在处理中";
  if (order.value?.fulfillment_type === "delivery")
    return (
      {
        pending_payment: "配送订单待付款",
        pending: "付款已确认，等待商家接单",
        preparing: "你的好味道，正在制作",
        ready: "餐点做好啦，等待配送",
        delivering: "商家正在送来你的餐点",
        arrived: "已到交接点，记得收餐",
      } as Record<string, string>
    )[order.value.status];
  return order.value?.status === "ready"
    ? "餐点做好啦，记得来取"
    : order.value?.status === "preparing"
      ? "你的好味道，正在制作"
      : "订单已送达，等待商家接单";
});
let sequence = 0;
let controller: AbortController | null = null;
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;

function reset() {
  sequence++;
  controller?.abort();
  controller = null;
  summary.value = null;
  error.value = false;
  busy.value = false;
}
async function refresh() {
  const userId = session.user?.id;
  if (!userId || document.hidden || busy.value || disposed) return;
  const request = ++sequence;
  const abort = new AbortController();
  controller = abort;
  const timeout = setTimeout(() => abort.abort(), 8000);
  busy.value = true;
  try {
    const result = await api<Summary>("/orders/active-summary", {
      signal: abort.signal,
    });
    if (request !== sequence || session.user?.id !== userId || disposed) return;
    if (result.user_id !== userId) {
      // A different tab may have switched the shared session cookie during this request.
      reset();
      await session.refreshUser();
      return;
    }
    summary.value = result;
    error.value = false;
  } catch (failure) {
    if (request !== sequence || session.user?.id !== userId || disposed) return;
    error.value = true;
    if (failure instanceof ApiError && failure.status === 403) {
      summary.value = null;
      void session.refreshUser().catch(() => {});
    }
  } finally {
    clearTimeout(timeout);
    if (request === sequence) {
      busy.value = false;
      controller = null;
    }
  }
}
watch(
  () => session.user?.id,
  () => {
    reset();
    void refresh();
  },
);
onMounted(() => {
  void refresh();
  timer = setInterval(refresh, 15000);
  window.addEventListener("focus", refresh);
  window.addEventListener("online", refresh);
  document.addEventListener("visibilitychange", refresh);
});
onUnmounted(() => {
  disposed = true;
  reset();
  clearInterval(timer);
  window.removeEventListener("focus", refresh);
  window.removeEventListener("online", refresh);
  document.removeEventListener("visibilitychange", refresh);
});
</script>

<template>
  <aside
    v-if="session.user && (order || error)"
    class="order-status-wrap"
    aria-label="进行中订单提醒"
  >
    <div
      class="order-status-banner"
      :class="{
        ready: order?.status === 'ready' && !order.cancel_requested,
        stale: error,
      }"
    >
      <span class="order-status-icon" aria-hidden="true">
        <ShoppingBag v-if="order?.status === 'ready'" :size="23" />
        <ChefHat v-else-if="order?.status === 'preparing'" :size="23" />
        <Clock3 v-else :size="23" />
      </span>
      <div class="order-status-copy" aria-live="polite" aria-atomic="true">
        <strong>{{ error ? "订单状态暂未同步" : title }}</strong>
        <p v-if="order">
          {{ order.stall_name }}<span v-if="error"> · 请刷新后确认进度</span
          ><span v-else-if="summary!.counts.total > 1">
            · 共 {{ summary!.counts.total }} 笔进行中</span
          ><span v-else>
            ·
            {{
              order.fulfillment_type === "delivery" ? "商家配送" : "到摊自取"
            }}</span
          >
        </p>
        <p v-else>连接恢复后即可查看制作与取餐进度</p>
      </div>
      <button
        v-if="error"
        class="order-status-retry"
        :disabled="busy"
        @click="refresh"
        aria-label="重新同步订单状态"
      >
        <RefreshCw :size="16" />重试
      </button>
      <RouterLink
        v-else-if="order"
        class="order-status-action"
        :to="`/orders/${order.id}`"
      >
        {{
          order.status === "ready" &&
          order.fulfillment_type !== "delivery" &&
          !order.cancel_requested
            ? "查看取餐码"
            : "查看进度"
        }}<ArrowRight :size="16" />
      </RouterLink>
    </div>
    <SessionNotifications
      v-if="session.user"
      :key="session.user.id"
      :user-id="session.user.id"
      :ready="!!summary && !error"
      :events="
        order &&
        !order.cancel_requested &&
        ['ready', 'arrived'].includes(order.status)
          ? [
              {
                id: order.id + ':' + order.status,
                title: title,
                body: order.stall_name + ' · 请打开订单核对交付状态',
                url: '/orders/' + order.id,
              },
            ]
          : []
      "
    />
  </aside>
</template>

<style scoped>
.order-status-wrap {
  max-width: 1240px;
  margin: 20px auto 0;
  padding: 0 28px;
}
.order-status-banner {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 20px;
  border: 1px solid #ead9c1;
  border-radius: 17px;
  background: #fff4e4;
  color: #7c4e28;
}
.order-status-icon {
  height: 44px;
  width: 44px;
  display: grid;
  place-items: center;
  border-radius: 13px;
  background: #ffffffa6;
  flex-shrink: 0;
}
.order-status-copy {
  flex: 1;
  min-width: 0;
}
.order-status-copy strong {
  display: block;
  font-size: 14px;
  line-height: 1.5;
}
.order-status-copy p {
  font-size: 12px;
  margin-top: 3px;
  overflow-wrap: anywhere;
}
.order-status-action,
.order-status-retry {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  min-height: 44px;
  font-size: 13px;
  font-weight: 700;
}
.ready {
  background: #edf5ea;
  border-color: #d0e0cc;
  color: #365b3c;
}
.stale {
  color: #72634f;
  background: #f5f0e8;
  border-color: #e5ded2;
}
@media (max-width: 767px) {
  .order-status-wrap {
    padding: 0 18px;
    margin-top: 14px;
  }
  .order-status-banner {
    padding: 12px;
    gap: 9px;
    flex-wrap: wrap;
  }
  .order-status-icon {
    height: 36px;
    width: 36px;
    border-radius: 11px;
  }
  .order-status-copy {
    flex-basis: calc(100% - 50px);
  }
  .order-status-action,
  .order-status-retry {
    margin: -4px 0 -7px auto;
  }
}
</style>
