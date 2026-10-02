<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import {
  Store,
  MapPin,
  Pause,
  Play,
  ClipboardList,
  MessageSquare,
  CircleCheck,
} from "lucide-vue-next";
import { api, statusText } from "../../lib/api";
import { notify } from "../../lib/notify";
import { acceptReady } from "./delivery";
import MerchantQueueSettings from "./MerchantQueueSettings.vue";
const props = withDefaults(
  defineProps<{ stall: any; orders?: any[]; ready?: boolean }>(),
  { orders: () => [], ready: false },
);
const emit = defineEmits<{ refresh: []; location: [] }>();
const busy = ref(false),
  error = ref(""),
  reports = ref<any>(null),
  reportBusy = ref(false);
let disposed = false,
  controller: AbortController | undefined;
onBeforeUnmount(() => {
  disposed = true;
  controller?.abort();
});
const unfinished = computed(() =>
  props.orders.filter(
    (o) => !["completed", "cancelled", "rejected"].includes(o.status),
  ),
);
const hasLocation = computed(
  () =>
    !!props.stall.address &&
    props.stall.latitude != null &&
    props.stall.longitude != null,
);
const pending = computed(() => props.orders.filter(acceptReady).length);
const state = computed(() => props.stall.status);
const summary = computed(() =>
  state.value === "stale"
    ? "位置需要重新确认"
    : state.value === "closed"
      ? "核对位置，准备今天开摊"
      : state.value === "paused"
        ? "暂歇中，回来后恢复出摊"
        : props.stall.accepting_orders === false
          ? "线下营业中，线上接单已暂停"
          : pending.value
            ? `${pending.value} 笔新订单，先回应同学`
            : "正在营业，安心做好每一份",
);
async function status(value: string, confirmLocation = false) {
  if (busy.value) return;
  if (value === "open" && !hasLocation.value) {
    emit("location");
    return;
  }
  busy.value = true;
  error.value = "";
  controller = new AbortController();
  try {
    await api(`/merchant/stalls/${props.stall.id}/status`, {
      method: "POST",
      body: { status: value, confirm_location: confirmLocation },
      signal: controller.signal,
    });
    if (disposed) return;
    emit("refresh");
    notify(
      confirmLocation
        ? value === "open"
          ? "已确认位置并开始出摊"
          : "已确认当前位置，营业状态保持不变"
        : value === "open"
          ? "已恢复出摊，位置确认时间未改变"
          : value === "paused"
            ? "已标记暂歇，位置确认时间未改变"
            : "已收摊，已有订单请继续处理",
      "success",
    );
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    if (!disposed) busy.value = false;
  }
}
async function confirmHere() {
  await status(props.stall.session_status || "open", true);
}
async function accepting() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  controller = new AbortController();
  try {
    await api(`/merchant/stalls/${props.stall.id}/profile`, {
      method: "PATCH",
      body: { accepting_orders: props.stall.accepting_orders === false },
      signal: controller.signal,
    });
    if (!disposed) {
      emit("refresh");
      notify(
        props.stall.accepting_orders === false
          ? "已恢复线上接单偏好，仍按营业与核验条件接单"
          : "已暂停线上接单，线下出摊状态不变",
        "success",
      );
    }
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    if (!disposed) busy.value = false;
  }
}
async function loadReports() {
  if (reportBusy.value) return;
  reportBusy.value = true;
  error.value = "";
  try {
    const result = await api(
      `/merchant/stalls/${props.stall.id}/location-reports`,
    );
    if (!disposed) reports.value = result;
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    if (!disposed) reportBusy.value = false;
  }
}
</script>
<template>
  <section class="m-panel operations" aria-label="今天怎样营业">
    <div class="operations-head">
      <div>
        <span class="m-eyebrow">让同学少跑空</span>
        <h2><Store :size="22" /> 今天怎样营业</h2>
      </div>
      <span :class="['m-status', stall.status]">{{
        statusText(stall.status)
      }}</span>
    </div>
    <p class="location">
      <MapPin :size="17" /><span>{{
        stall.address || "还没有确认取餐位置"
      }}</span
      ><button class="m-text-link" @click="emit('location')">
        {{ hasLocation ? "更换位置" : "设置位置" }}
      </button>
    </p>
    <p class="operation-summary" role="status">{{ summary }}</p>
    <div class="main-actions">
      <button
        v-if="state === 'stale'"
        class="btn btn-primary"
        :disabled="busy"
        @click="confirmHere"
      >
        <MapPin :size="17" />核对过了，我仍在这里
      </button>
      <button
        v-else-if="state === 'closed'"
        class="btn btn-primary"
        :disabled="busy"
        @click="status('open', true)"
      >
        <Play :size="17" />{{
          hasLocation ? "就在这里，开始出摊" : "先设置出摊位置"
        }}
      </button>
      <button
        v-else-if="state === 'paused'"
        class="btn btn-primary"
        :disabled="busy"
        @click="status('open')"
      >
        <Play :size="17" />回到摊位，恢复出摊
      </button>
      <RouterLink
        v-else-if="ready && pending"
        class="btn btn-primary"
        to="/merchant/orders?filter=pending"
        ><ClipboardList :size="18" />处理待接单 <b>{{ pending }}</b></RouterLink
      >
      <button
        v-else-if="stall.accepting_orders === false"
        class="btn btn-primary"
        :disabled="busy"
        @click="accepting"
      >
        <Play :size="17" />恢复线上接单
      </button>
      <div v-else class="open-indicator">
        <CircleCheck :size="20" />营业中<span>{{
          ready ? "新订单到达后会在这里显示" : "待处理订单正在同步"
        }}</span>
      </div>
      <button
        v-if="state === 'open' && (stall.accepting_orders !== false || pending)"
        class="btn btn-secondary"
        :disabled="busy"
        @click="accepting"
      >
        <Pause v-if="stall.accepting_orders !== false" :size="17" /><Play
          v-else
          :size="17"
        />{{
          stall.accepting_orders === false
            ? "恢复线上接单"
            : "忙不过来，暂停接单"
        }}
      </button>
    </div>
    <p class="helper">
      {{
        stall.accepting_orders === false
          ? "线上接单已暂停，同学仍能看到你的线下营业状态。已有订单继续处理。"
          : stall.order_unavailable_reason ||
            "线上接单按营业、位置和经营核验情况开放。已有订单不会因暂停而取消。"
      }}
    </p>
    <div class="secondary-actions">
      <button
        v-if="state !== 'closed' && state !== 'paused'"
        :disabled="busy"
        @click="status('paused')"
      >
        暂时离开摊位</button
      ><button
        v-if="state !== 'closed'"
        :disabled="busy"
        @click="status('closed')"
      >
        今日收摊</button
      ><button :disabled="reportBusy" @click="loadReports">
        <MessageSquare :size="15" />{{
          reportBusy ? "读取中…" : "查看找摊反馈"
        }}
      </button>
    </div>
    <p v-if="error" class="m-alert" role="alert">{{ error }}</p>
    <MerchantQueueSettings
      :key="stall.id"
      :stall="stall"
      :orders="orders"
      :ready="ready"
      @refresh="emit('refresh')"
    />
    <div v-if="stall.status === 'closed'" class="closing">
      <ClipboardList :size="21" />
      <div>
        <strong>{{
          ready
            ? `还有 ${unfinished.length} 单未完成`
            : "请同步订单，确认今天是否还有未完成订单"
        }}</strong>
        <p>收摊仅停止新订单，请继续出餐、交付或处理取消申请。</p>
        <RouterLink class="m-text-link" to="/merchant/orders?filter=active"
          >查看未完成订单 →</RouterLink
        >
      </div>
    </div>
    <div v-if="reports" class="reports">
      <strong>{{
        reports.unresolved_count
          ? `${reports.unresolved_count} 条找摊反馈待核实`
          : "暂没有待核实的找摊反馈"
      }}</strong>
      <p class="helper">
        反馈尚待核实。请核对实际位置和出摊状态；仅确认位置不会自动解决反馈。
      </p>
      <article v-for="report in reports.reports" :key="report.id">
        <b>{{
          report.kind === "not_found"
            ? "同学没有找到摊位"
            : report.kind === "wrong_location"
              ? "同学反馈位置不对"
              : report.kind === "mismatch"
                ? "同学反馈信息不符"
                : "位置反馈"
        }}</b>
        <p>请核对实际出摊位置。</p>
        <small
          >同学当时看到的位置：{{
            report.location_snapshot?.address || "未附位置说明"
          }}</small
        >
      </article>
    </div>
  </section>
</template>
<style scoped>
.operations {
  margin-bottom: 20px;
}
.operations-head {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: center;
}
.operations h2 {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 8px 0 16px;
  font-size: 22px;
}
.location {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  color: #65533f;
  line-height: 1.7;
}
.location span {
  flex: 1;
  min-width: 140px;
  overflow-wrap: anywhere;
}
.main-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 18px;
}
.main-actions button {
  min-height: 48px;
}
.operation-summary {
  color: #4f3829;
  font-weight: 650;
  margin: 14px 0 0;
  line-height: 1.6;
}
.open-indicator {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  min-height: 48px;
  color: #346447;
}
.open-indicator span {
  font-size: 13px;
  font-weight: 400;
  color: #74624e;
}
.main-actions a {
  min-height: 48px;
  box-sizing: border-box;
}
.main-actions b {
  padding: 2px 8px;
  border-radius: 20px;
  background: #ffffff30;
}
.helper {
  font-size: 13px;
  color: #7c6652;
  line-height: 1.8;
}
.secondary-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  border-top: 1px solid #eee2d4;
  padding-top: 8px;
}
.secondary-actions button {
  min-height: 44px;
  background: transparent;
  border: 0;
  color: #6c5139;
  padding: 8px 10px;
  display: flex;
  gap: 6px;
  align-items: center;
  cursor: pointer;
}
.closing {
  display: flex;
  gap: 12px;
  padding: 16px;
  background: #fff1e0;
  border-radius: 14px;
  margin-top: 14px;
}
.closing p {
  font-size: 13px;
  line-height: 1.7;
  color: #785e46;
}
.reports {
  margin-top: 18px;
}
.reports article {
  padding: 12px 0;
  border-top: 1px solid #eee2d4;
}
.reports p {
  font-size: 14px;
  overflow-wrap: anywhere;
}
@media (max-width: 600px) {
  .main-actions button,
  .main-actions a {
    width: 100%;
  }
  .operations h2 {
    font-size: 20px;
  }
  .operations-head {
    align-items: flex-start;
  }
  .operations .m-status {
    flex: none;
  }
}
.operations button {
  min-height: 44px;
}
</style>
