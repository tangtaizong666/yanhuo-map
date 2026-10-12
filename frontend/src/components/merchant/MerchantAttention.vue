<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  ArrowRight,
  BellRing,
  Clock3,
  ClipboardCheck,
  Volume2,
  VolumeX,
  X,
  WifiOff,
} from "lucide-vue-next";
import type { Order } from "../../lib/types";
import { acceptReady, cancellableDeliveryStatuses } from "./delivery";
import { needsMerchantFollowUp } from "../../lib/orderFollowUp";

const props = defineProps<{
  scope: string;
  orders: Order[];
  ready: boolean;
  syncError?: string;
  compactPending?: boolean;
  showControls?: boolean;
  showTasks?: boolean;
}>();
const emit = defineEmits<{ expired: []; refresh: [] }>();
const now = ref(Date.now());
const arrivals = ref<string[]>([]);
const soundEnabled = ref(false);
const soundBusy = ref(false);
const soundError = ref("");
const online = ref(navigator.onLine);
function connectionChanged() {
  online.value = navigator.onLine;
  if (online.value) emit("refresh");
}
const seenByStall = new Map<string, Set<string>>();
const expiryRefreshes = new Set<string>();
let ticker: ReturnType<typeof setInterval> | undefined;
let audio: AudioContext | undefined;
let disposed = false;

const pending = computed(() => props.orders.filter(acceptReady));
const followUp = computed(() => props.orders.filter(needsMerchantFollowUp));
const cancellations = computed(() =>
  props.orders.filter(
    (order) =>
      order.cancel_requested &&
      cancellableDeliveryStatuses.includes(order.status),
  ),
);
const activeArrivals = computed(() =>
  pending.value.filter((order) => arrivals.value.includes(order.id)),
);
const nearestExpiry = computed(() => {
  const timestamps = pending.value
    .map((order) => Date.parse(order.expires_at || ""))
    .filter(Number.isFinite);
  return timestamps.length ? Math.min(...timestamps) : null;
});
const remaining = computed(() =>
  nearestExpiry.value === null
    ? null
    : Math.max(0, Math.ceil((nearestExpiry.value - now.value) / 1000)),
);
const expiryText = computed(() => {
  if (remaining.value === null) return "接单时限待同步";
  if (!remaining.value) return "有订单接单超时，正在同步状态";
  return `最早一单剩余 ${Math.floor(remaining.value / 60)}:${String(remaining.value % 60).padStart(2, "0")} 接单`;
});

function playTone() {
  if (!audio || audio.state !== "running") {
    soundEnabled.value = false;
    soundError.value = "浏览器暂停了声音，请再次点击开启；页面提醒仍会显示。";
    return;
  }
  // A short, gentle two-note cue; never requests notification or microphone access.
  const start = audio.currentTime;
  for (const [offset, frequency] of [
    [0, 660],
    [0.18, 880],
  ]) {
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.frequency.value = frequency!;
    oscillator.type = "sine";
    gain.gain.setValueAtTime(0, start + offset!);
    gain.gain.linearRampToValueAtTime(0.09, start + offset! + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.001, start + offset! + 0.16);
    oscillator.connect(gain);
    gain.connect(audio.destination);
    oscillator.start(start + offset!);
    oscillator.stop(start + offset! + 0.18);
    oscillator.onended = () => {
      oscillator.disconnect();
      gain.disconnect();
    };
  }
}
async function toggleSound() {
  if (soundBusy.value) return;
  if (soundEnabled.value) {
    soundEnabled.value = false;
    soundError.value = "";
    return;
  }
  soundBusy.value = true;
  soundError.value = "";
  try {
    const Audio =
      window.AudioContext ||
      (window as Window & { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!Audio) throw new Error("当前浏览器不支持声音提醒，页面提醒仍会显示。");
    audio ??= new Audio();
    await audio.resume();
    if (disposed) return;
    if (audio.state !== "running")
      throw new Error(
        "声音未能开启，请检查浏览器设置后重试。页面提醒仍会显示。",
      );
    soundEnabled.value = true;
    playTone();
  } catch (error) {
    soundEnabled.value = false;
    soundError.value =
      error instanceof Error
        ? error.message
        : "声音未能开启，页面提醒仍会显示。";
  } finally {
    if (!disposed) soundBusy.value = false;
  }
}

watch(
  () => props.scope,
  () => {
    arrivals.value = [];
  },
  { flush: "sync" },
);
watch(
  () => [props.scope, props.ready, props.orders] as const,
  () => {
    if (!props.ready) return;
    // A delivery awaiting payment becomes a new actionable arrival only after
    // verified payment. Seeing its draft earlier must not consume that alert.
    const currentIds = new Set(pending.value.map((order) => order.id));
    const seen = seenByStall.get(props.scope);
    if (!seen) {
      // Existing orders remain actionable, but initial loading never pretends they just arrived.
      seenByStall.set(props.scope, currentIds);
      return;
    }
    const newOrders = pending.value.filter((order) => !seen.has(order.id));
    for (const id of currentIds) seen.add(id);
    arrivals.value = [
      ...arrivals.value.filter((id) =>
        pending.value.some((order) => order.id === id),
      ),
      ...newOrders.map((order) => order.id),
    ];
    if (newOrders.length && soundEnabled.value && !document.hidden) {
      try {
        playTone();
      } catch {
        soundEnabled.value = false;
        soundError.value =
          "声音播放失败，页面提醒仍会显示。请再次点击开启重试。";
      }
    }
  },
  { immediate: true },
);

onMounted(() => {
  window.addEventListener("online", connectionChanged);
  window.addEventListener("offline", connectionChanged);
  ticker = setInterval(() => {
    now.value = Date.now();
    let expired = false;
    for (const order of pending.value) {
      const time = Date.parse(order.expires_at || "");
      const key = `${props.scope}:${order.id}`;
      if (
        Number.isFinite(time) &&
        time <= now.value &&
        !expiryRefreshes.has(key) &&
        !document.hidden
      ) {
        expiryRefreshes.add(key);
        expired = true;
      }
    }
    if (expired) emit("expired");
  }, 1000);
});
onUnmounted(() => {
  disposed = true;
  clearInterval(ticker);
  window.removeEventListener("online", connectionChanged);
  window.removeEventListener("offline", connectionChanged);
  void audio?.close().catch(() => {});
});
</script>

<template>
  <section
    v-show="showControls !== false || !online || syncError || activeArrivals.length || soundError || (showTasks !== false && (pending.length || cancellations.length || followUp.length))"
    class="m-attention"
    :class="{
      'has-orders': showTasks !== false && (pending.length || cancellations.length || followUp.length),
    }"
    aria-label="接单提醒"
    :data-pending-count="pending.length"
    :data-cancellation-count="cancellations.length"
    :data-followup-count="followUp.length"
  >
    <div
      v-if="!online || syncError"
      class="m-attention-disconnected"
      role="alert"
    >
      <WifiOff :size="22" />
      <div>
        <strong>{{
          !online ? "当前已断网，无法收到新订单" : "订单同步中断，请先检查连接"
        }}</strong>
        <p>
          屏幕上的数量和状态可能已过时。恢复连接后立即同步，确认后再安排出餐。
        </p>
      </div>
    </div>
    <div
      v-if="showTasks !== false && ((!compactPending && pending.length) || cancellations.length)"
      class="m-attention-tasks"
    >
      <div class="m-attention-summary">
        <span class="m-attention-icon"><BellRing :size="20" /></span>
        <div>
          <strong v-if="pending.length && !compactPending"
            >有 {{ pending.length }} 笔订单等你接单</strong
          >
          <strong v-else>有 {{ cancellations.length }} 笔取消申请待处理</strong>
          <p
            v-if="pending.length && !compactPending"
            class="m-attention-countdown"
            :class="{ urgent: remaining !== null && remaining < 60 }"
          >
            <Clock3 :size="13" />{{ expiryText }}
          </p>
          <p v-else>及时回应顾客，妥善安排这一餐。</p>
        </div>
      </div>
      <div class="m-attention-actions">
        <RouterLink
          v-if="cancellations.length"
          to="/merchant/orders?filter=cancellation"
          class="m-attention-cancel"
          >处理取消申请 <b>{{ cancellations.length }}</b></RouterLink
        >
        <RouterLink
          v-if="pending.length && !compactPending"
          to="/merchant/orders?filter=pending"
          class="m-attention-primary"
          >去接单 <ArrowRight :size="16"
        /></RouterLink>
      </div>
    </div>
    <div v-if="showTasks !== false && followUp.length" class="m-attention-followup">
      <div class="m-attention-summary">
        <span class="m-attention-icon"><ClipboardCheck :size="20" /></span>
        <div>
          <strong>有 {{ followUp.length }} 笔售后需要跟进</strong>
          <p>退款未完成、付款待核对或配送异常，订单结束后也会保留提醒。</p>
        </div>
      </div>
      <RouterLink
        to="/merchant/orders?filter=followup"
        class="m-attention-followup-link"
      >
        跟进售后 <b>{{ followUp.length }}</b
        ><ArrowRight :size="16" />
      </RouterLink>
    </div>
    <div
      v-if="activeArrivals.length"
      class="m-attention-arrival"
      data-testid="merchant-new-order-notice"
    >
      <p role="status">
        刚收到 {{ activeArrivals.length }} 笔新订单，请及时接单。
      </p>
      <button
        type="button"
        aria-label="知道了，收起新订单提示"
        @click="arrivals = []"
      >
        <X :size="16" />
      </button>
    </div>
    <div v-show="showControls !== false" class="m-attention-tools">
      <p>
        <b v-if="compactPending && pending.length" class="sound-readiness">{{
          expiryText
        }}</b>
        <b class="sound-readiness" :class="{ ready: soundEnabled }">{{
          soundEnabled ? "本页声音已就绪" : "声音提醒尚未开启"
        }}</b>
        {{
          syncError
            ? "订单同步中断，当前显示上次同步结果"
            : ready
              ? "每 10 秒同步，回到页面会立即刷新"
              : "正在同步待处理订单…"
        }}<small>声音仅在此页面打开且处于前台时生效；开启后会试音。</small>
      </p>
      <button
        type="button"
        class="m-attention-sound"
        :aria-pressed="soundEnabled"
        :disabled="soundBusy"
        @click="toggleSound"
      >
        <component :is="soundEnabled ? Volume2 : VolumeX" :size="16" />{{
          soundBusy
            ? "正在开启…"
            : soundEnabled
              ? "关闭声音提醒"
              : "开启声音提醒"
        }}
      </button>
    </div>
    <div v-if="syncError" class="m-attention-retry" role="status">
      <span>待处理数量可能已变化，请联网后重试。</span
      ><button type="button" @click="emit('refresh')">重新同步</button>
    </div>
    <p v-if="soundError" class="m-attention-error" role="status">
      {{ soundError }}
    </p>
  </section>
</template>

<style scoped>
.m-attention-disconnected {
  display: flex;
  gap: 12px;
  padding: 16px;
  margin-bottom: 12px;
  border: 1px solid #df9773;
  border-radius: 12px;
  background: #fff0e7;
  color: #943d23;
}
.m-attention-disconnected > svg {
  flex-shrink: 0;
}
.m-attention-disconnected strong {
  font-size: 15px;
}
.m-attention-disconnected p {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
}
.sound-readiness {
  display: block;
  color: #82582f;
  font-size: 12px;
}
.sound-readiness.ready {
  color: #3d6a48;
}
.m-attention {
  margin: 0 0 25px;
  color: #73543d;
}
.m-attention.has-orders {
  border: 1px solid #efd5b7;
  border-radius: 16px;
  background: #fff8ec;
  overflow: hidden;
}
.m-attention-tasks {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 18px 20px 12px;
}
.m-attention-summary {
  display: flex;
  align-items: center;
  gap: 13px;
  min-width: 0;
}
.m-attention-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  color: #b66031;
  background: #f8e7ce;
  border-radius: 13px;
}
.m-attention-summary strong {
  color: #664029;
  font-size: 15px;
}
.m-attention-summary p {
  margin: 7px 0 0;
  font-size: 12px;
  line-height: 1.6;
}
.m-attention-countdown {
  display: flex;
  gap: 5px;
  align-items: center;
  font-variant-numeric: tabular-nums;
}
.m-attention-countdown.urgent {
  color: #a83222;
}
.m-attention-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.m-attention-actions a,
.m-attention-followup-link,
.m-attention-sound {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 44px;
  border-radius: 10px;
  padding: 10px 15px;
  font-size: 12px;
  line-height: 1.5;
}
.m-attention-followup {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 20px 12px;
}
.m-attention-tasks + .m-attention-followup {
  border-top: 1px solid #eddcc7;
}
.m-attention-followup-link {
  flex-shrink: 0;
  color: #805333;
  background: #f5e8d6;
}
.m-attention-primary {
  color: white;
  background: #c96a36;
}
.m-attention-primary:hover {
  background: #af5528;
}
.m-attention-cancel {
  color: #805333;
  background: #f5e8d6;
}
.m-attention-cancel b {
  font-size: 12px;
}
.m-attention-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.has-orders .m-attention-tools {
  padding: 9px 20px 13px;
}
.m-attention-tools p {
  margin: 0;
  color: #826951;
  font-size: 11px;
  line-height: 1.7;
}
.m-attention-tools small {
  display: block;
  font-size: 10px;
  color: #826951;
}
.m-attention-sound {
  background: #fffcf7;
  color: #795c46;
  border: 1px solid #e5d6c2;
  flex-shrink: 0;
  padding: 9px 12px;
}
.m-attention-sound[aria-pressed="true"] {
  color: #496044;
  background: #eef2e9;
  border-color: #d8e0cd;
}
.m-attention-sound:disabled {
  opacity: 0.6;
  cursor: wait;
}
.m-attention-arrival {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 0 10px 0 20px;
  color: #9b512b;
  background: #f9e8d1;
}
.m-attention-arrival p {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
}
.m-attention-arrival button {
  display: grid;
  place-items: center;
  color: inherit;
  border: 0;
  background: none;
  min-width: 44px;
  min-height: 44px;
}
.m-attention-error {
  margin: 8px 0 0;
  color: #a83222;
  font-size: 12px;
  line-height: 1.7;
}
.has-orders .m-attention-error {
  margin: 0;
  padding: 0 20px 15px;
}
.m-attention-retry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #a83222;
  font-size: 11px;
  line-height: 1.7;
}
.has-orders .m-attention-retry {
  padding: 0 20px 12px;
}
.m-attention-retry button {
  min-height: 44px;
  padding: 8px;
  border: 0;
  background: none;
  color: inherit;
  text-decoration: underline;
  flex-shrink: 0;
}
.m-attention :is(button, a):focus-visible {
  outline: 3px solid #d17c3b;
  outline-offset: 3px;
}
@media (max-width: 700px) {
  .m-attention {
    margin-bottom: 20px;
  }
  .m-attention-tasks {
    flex-wrap: wrap;
    padding: 15px 15px 8px;
    gap: 12px;
  }
  .m-attention-followup {
    flex-wrap: wrap;
    padding: 15px 15px 8px;
    gap: 12px;
  }
  .m-attention-followup-link {
    width: 100%;
  }
  .m-attention-actions {
    width: 100%;
  }
  .m-attention-actions a {
    flex: 1;
  }
  .m-attention-summary strong {
    font-size: 14px;
  }
  .has-orders .m-attention-tools {
    padding: 8px 15px 12px;
  }
  .m-attention-tools {
    flex-wrap: wrap;
    gap: 6px;
  }
  .m-attention-tools p {
    flex: 1 1 190px;
  }
  .m-attention-sound {
    padding: 8px 10px;
    font-size: 11px;
  }
  .m-attention-arrival {
    padding-left: 15px;
    padding-right: 4px;
  }
  .has-orders .m-attention-error {
    padding: 0 15px 12px;
  }
  .has-orders .m-attention-retry {
    padding: 0 15px 10px;
  }
}
</style>
