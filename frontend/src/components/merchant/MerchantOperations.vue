<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import {
  Store,
  MapPin,
  Pause,
  Play,
  ClipboardList,
  MessageSquare,
} from "lucide-vue-next";
import { api, formatTime, statusText } from "../../lib/api";
import { notify } from "../../lib/notify";
import { acceptReady } from "./delivery";
import MerchantQueueSettings from "./MerchantQueueSettings.vue";
const props = withDefaults(
  defineProps<{ stall: any; orders?: any[]; ready?: boolean; compact?: boolean }>(),
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
    (props.stall.activation?.has_location ?? (!!props.stall.address && props.stall.address !== "位置尚未确认")) &&
    props.stall.latitude != null &&
    props.stall.longitude != null,
);
const pending = computed(() => props.orders.filter(acceptReady).length);
const state = computed(() => props.stall.status);
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
  if (!hasLocation.value) { emit("location"); return; }
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
  <section v-if="compact" class="m-business-compact" aria-label="营业与接单">
    <div><strong>{{ stall.name }}</strong><span :class="{ 'is-open': state === 'open' && stall.accepting_orders !== false, 'is-paused': state !== 'open' || stall.accepting_orders === false }">{{ state === 'stale' ? '位置过期，暂停新单' : state === 'closed' ? '已收摊' : state === 'paused' ? '暂歇中' : stall.accepting_orders === false ? '已暂停新单' : '营业中' }}</span></div>
    <button v-if="state === 'stale'" class="compact-open" :disabled="busy" @click="confirmHere">{{ hasLocation ? '确认仍在这里' : '设置位置' }}</button>
    <button v-else-if="state === 'closed'" class="compact-open" :disabled="busy" @click="status('open', true)">{{ hasLocation ? '开始营业' : '设置位置' }}</button>
    <button v-else-if="state === 'paused'" class="compact-open" :disabled="busy" @click="status('open')">恢复营业</button>
    <button v-else :disabled="busy" @click="accepting">{{ stall.accepting_orders === false ? '恢复接单' : '暂停接单' }}</button>
    <RouterLink to="/merchant/store" aria-label="营业设置"><Store :size="18" /><span>设置</span></RouterLink>
    <p v-if="state === 'closed' || state === 'stale'" class="compact-location"><MapPin :size="16" /><span>{{ hasLocation ? stall.address : '先确认实际取餐位置，再开始营业。' }}</span><RouterLink v-if="hasLocation" to="/merchant/store#location">更换位置</RouterLink></p>
    <p v-if="error" class="m-alert" role="alert">{{ error }}</p>
  </section>
  <section v-else class="m-panel operations" aria-label="今天怎样营业">
    <div class="operations-head">
      <div>
        <h2><Store :size="22" /> 营业与接单</h2>
      </div>
      <span :class="['m-status', stall.status]">{{
        statusText(stall.status)
      }}</span>
    </div>
    <p class="location">
      <MapPin :size="17" /><span>{{
        hasLocation ? stall.address : "还没有确认取餐位置"
      }}</span
      ><button class="m-text-link" @click="emit('location')">
        {{ hasLocation ? "更换位置" : "设置位置" }}
      </button>
    </p>
    <p v-if="hasLocation && stall.last_confirmed_at" class="location-time">上次确认 {{ formatTime(stall.last_confirmed_at) }}</p>
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
    <p v-if="stall.order_unavailable_reason || stall.accepting_orders === false" class="helper">{{ stall.order_unavailable_reason || "线上接单已暂停，已有订单继续处理。" }}</p>
    <div class="secondary-actions">
      <button v-if="hasLocation && state !== 'stale' && state !== 'closed'" :disabled="busy" @click="confirmHere"><MapPin :size="15" />我还在这里，确认当前位置</button>
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
.m-business-compact { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; padding: 8px 0; }
.m-business-compact > div { flex: 1; min-width: 100px; display: grid; gap: 5px; }
.m-business-compact strong { font-size: 16px; line-height: 1.4; overflow-wrap: anywhere; }
.m-business-compact > div > span { font-size: 13px; color: #776956; }
.m-business-compact .is-open { color: #3d704d; }
.m-business-compact .is-open::before { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: currentColor; margin-right: 6px; vertical-align: middle; }
.m-business-compact button, .m-business-compact > a { min-height: 44px; display: inline-flex; align-items: center; justify-content: center; gap: 5px; font-size: 14px; padding: 0 12px; border: 1px solid #e6dccf; border-radius: 10px; background: white; }
.m-business-compact button.compact-open { background: #e86a27; border-color: #e86a27; color: white; font-weight: 700; }
.m-business-compact > a { padding: 0 8px; color: #766452; border-color: transparent; background: transparent; }
.m-business-compact .m-alert { flex-basis: 100%; }
.compact-location { display: flex; flex-basis: 100%; align-items: flex-start; gap: 7px; margin: 0; font-size: 14px; line-height: 1.6; color: #65533f; }
.compact-location > svg { flex: none; margin-top: 4px; }
.compact-location > span { flex: 1; overflow-wrap: anywhere; }
.compact-location > a { flex: none; display: inline-flex; align-items: center; min-height: 44px; margin-top: -9px; padding: 0 5px; color: #93451d; text-decoration: underline; text-underline-offset: 3px; }
@media (max-width: 380px) { .m-business-compact > a span { display: none; } }

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
  margin: 0;
  font-size: 20px;
}
.location {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  color: #65533f;
  line-height: 1.7;
}
.location-time { margin: -5px 0 0; font-size: 13px; line-height: 1.6; color: #74624e; }
.location span {
  flex: 1;
  min-width: 140px;
  overflow-wrap: anywhere;
}
.main-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 12px;
}
.main-actions button {
  min-height: 48px;
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
