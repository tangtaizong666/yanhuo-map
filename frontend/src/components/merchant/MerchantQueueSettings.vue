<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, ApiError, formatTime } from "../../lib/api";
import { useSession } from "../../stores/session";
const props = withDefaults(
  defineProps<{ stall: any; orders?: any[]; ready?: boolean }>(),
  { orders: () => [], ready: false },
);
const emit = defineEmits<{ refresh: [] }>();
const session = useSession(),
  owner = session.user?.id;
const enabled = ref(props.stall.prep_capacity != null),
  capacity = ref(props.stall.prep_capacity || 5),
  cutoffSessionId = ref<number | null | undefined>(
    props.stall.business_session_id,
  ),
  cutoff = ref(""),
  busy = ref(""),
  error = ref(""),
  notice = ref("");
let baseCapacity = props.stall.prep_capacity ?? null,
  baseCutoff = "",
  controller: AbortController | undefined,
  disposed = false;
function localTime(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}T${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}
watch(
  () => props.stall,
  (value) => {
    const current = enabled.value ? Number(capacity.value) : null;
    if (current === baseCapacity) {
      enabled.value = value.prep_capacity != null;
      capacity.value = value.prep_capacity || 5;
    }
    baseCapacity = value.prep_capacity ?? null;
    if (cutoffSessionId.value !== value.business_session_id) {
      cutoffSessionId.value = value.business_session_id;
      cutoff.value = localTime(value.stop_orders_at);
      notice.value = "营业场次已变化，请重新核对本场截止时间。";
    } else if (cutoff.value === baseCutoff)
      cutoff.value = localTime(value.stop_orders_at);
    baseCutoff = localTime(value.stop_orders_at);
  },
  { immediate: true },
);
watch(
  () => session.user?.id,
  () => controller?.abort(),
  { flush: "sync" },
);
onBeforeUnmount(() => {
  disposed = true;
  controller?.abort();
});
const counts = computed(() => ({
  pending_payment: props.orders.filter((o) => o.status === "pending_payment")
    .length,
  pending: props.orders.filter((o) => o.status === "pending").length,
  preparing: props.orders.filter((o) => o.status === "preparing").length,
}));
async function save(kind: "capacity" | "cutoff") {
  if (busy.value || session.user?.id !== owner) return;
  error.value = "";
  notice.value = "";
  const value = enabled.value ? Number(capacity.value) : null;
  if (
    kind === "capacity" &&
    value != null &&
    (!Number.isInteger(value) || value < 1 || value > 100)
  ) {
    error.value = "同时备餐上限请填写 1 至 100 单。";
    return;
  }
  const time = cutoff.value ? new Date(cutoff.value) : null;
  if (
    kind === "cutoff" &&
    (!Number.isInteger(cutoffSessionId.value) ||
      (cutoffSessionId.value ?? 0) <= 0)
  ) {
    error.value = "暂未获取当前营业场次，请刷新工作台后再设置截止时间。";
    return;
  }
  if (
    kind === "cutoff" &&
    (props.stall.status === "closed" ||
      (time &&
        (!Number.isFinite(time.getTime()) ||
          (props.stall.closes_at &&
            time.getTime() > Date.parse(props.stall.closes_at)))))
  ) {
    error.value = "请开摊后设置，停止接新单时间不能晚于预计收摊时间。";
    return;
  }
  busy.value = kind;
  controller = new AbortController();
  try {
    await api(
      `/merchant/stalls/${props.stall.id}/${kind === "capacity" ? "profile" : "status"}`,
      {
        method: kind === "capacity" ? "PATCH" : "POST",
        signal: controller.signal,
        body:
          kind === "capacity"
            ? { prep_capacity: value }
            : {
                cutoff_only: true,
                expected_session_id: cutoffSessionId.value,
                stop_orders_at: time?.toISOString() || null,
              },
      },
    );
    if (disposed || session.user?.id !== owner) return;
    if (kind === "capacity") baseCapacity = value;
    else baseCutoff = cutoff.value;
    notice.value =
      kind === "capacity"
        ? "备餐上限已保存，已有订单继续处理。"
        : "本场接单截止已保存，位置确认时间和已有订单未改变。";
    emit("refresh");
  } catch (cause) {
    if (!disposed && session.user?.id === owner) {
      error.value = (cause as Error).message;
      if (
        cause instanceof ApiError &&
        ["business_session_changed", "business_session_ended"].includes(
          cause.code || "",
        )
      )
        emit("refresh");
    }
  } finally {
    if (!disposed) busy.value = "";
  }
}
</script>
<template>
  <div class="queue-settings">
    <details>
      <summary>接单量与本场截止时间</summary>
      <div class="queue-counts" aria-label="当前备餐占位">
        <span
          >待付款占位 <b>{{ ready ? counts.pending_payment : "—" }}</b></span
        ><span
          >待接单 <b>{{ ready ? counts.pending : "—" }}</b></span
        ><span
          >制作中 <b>{{ ready ? counts.preparing : "—" }}</b></span
        >
      </div>
      <p class="queue-hint">
        {{
          stall.prep_capacity == null
            ? "备餐容量限制未开启"
            : `备餐占位 ${stall.prep_active_orders ?? "—"}/${stall.prep_capacity} 单`
        }}
        · 只统计系统内订单；现场排队忙不过来时，仍请暂停接单。
      </p>
      <p v-if="stall.stop_orders_at" class="queue-cutoff">
        本场 {{ formatTime(stall.stop_orders_at) }} 停止接新单，已有订单照常完成。
      </p>
      <form @submit.prevent="save('capacity')">
        <label class="queue-toggle"
          ><input
            v-model="enabled"
            type="checkbox"
            :disabled="!!busy"
          />限制同时备餐订单</label
        ><label v-if="enabled"
          >最多占位（单）<input
            v-model.number="capacity"
            type="number"
            min="1"
            max="100"
            required
            :disabled="!!busy"
        /></label>
        <p>
          关闭时不限单；开启后默认 5
          单，可调整。待付款、待接单和制作中共用上限，出餐后释放占位。不会取消已接订单。
        </p>
        <button type="submit" :disabled="!!busy">保存备餐上限</button>
      </form>
      <form @submit.prevent="save('cutoff')">
        <label
          >本场停止接新单时间（选填）<input
            v-model="cutoff"
            type="datetime-local"
            :max="localTime(stall.closes_at) || undefined"
            :disabled="!!busy || stall.status === 'closed'"
        /></label>
        <p>
          {{
            stall.status === "closed"
              ? "开摊后可为本场设置截止时间。下次重新开摊不会继承旧截止时间。"
              : "不晚于预计收摊时间；留空代表不额外提前截止。设置不会重新确认位置。"
          }}
        </p>
        <button type="submit" :disabled="!!busy || stall.status === 'closed'">
          保存本场截止时间
        </button>
      </form>
    </details>
    <p v-if="error" role="alert" class="queue-error">{{ error }}</p>
    <p v-if="notice" role="status" class="queue-success">{{ notice }}</p>
  </div>
</template>
<style scoped>
.queue-settings {
  margin-top: 16px;
  border-top: 1px solid #eddfcf;
  padding-top: 14px;
  color: #6f553f;
}
.queue-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.queue-counts span {
  padding: 9px 11px;
  background: #f9f4eb;
  border-radius: 10px;
  font-size: 12px;
}
.queue-counts b {
  font-size: 17px;
  margin-left: 6px;
  color: #6d3d20;
}
.queue-hint,
.queue-cutoff {
  font-size: 12px;
  line-height: 1.8;
  margin: 10px 0;
}
.queue-cutoff {
  color: #915427;
}
.queue-settings summary {
  min-height: 44px;
  display: flex;
  align-items: center;
  cursor: pointer;
  font-size: 13px;
}
.queue-settings form {
  padding: 14px 0;
  border-top: 1px solid #eee1d3;
  display: grid;
  gap: 10px;
  max-width: 550px;
}
.queue-settings label {
  display: grid;
  gap: 8px;
  font-size: 13px;
}
.queue-settings .queue-toggle {
  display: flex;
  align-items: center;
  min-height: 44px;
}
.queue-toggle input {
  width: 22px;
  height: 22px;
  accent-color: #d2672e;
}
.queue-settings input:not([type="checkbox"]) {
  width: 100%;
  min-height: 44px;
  box-sizing: border-box;
  padding: 10px;
  border: 1px solid #dcc9b2;
  border-radius: 10px;
  background: #fffdfa;
}
.queue-settings form p {
  font-size: 12px;
  line-height: 1.8;
  margin: 0;
}
.queue-settings button {
  min-height: 44px;
  justify-self: start;
  padding: 10px 14px;
  border: 1px solid #ddc1a1;
  border-radius: 10px;
  color: #8b441d;
  background: #fff0df;
  cursor: pointer;
}
.queue-error {
  color: #9d3926;
  font-size: 13px;
  line-height: 1.7;
}
.queue-success {
  color: #456843;
  font-size: 13px;
  line-height: 1.7;
}
.queue-settings button:disabled {
  opacity: 0.6;
}
</style>
