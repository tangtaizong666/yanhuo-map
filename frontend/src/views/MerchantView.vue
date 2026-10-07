<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Store, MapPin, ClipboardList, Utensils, Settings2, ArrowUpRight, LogOut, RefreshCw, ArrowRight } from "lucide-vue-next";
import { api } from "../lib/api";
import { useSession } from "../stores/session";
import { attentionOrders, type OrderCounts } from "../lib/orderPages";
import { notify } from "../lib/notify";
import MerchantMore from "../components/merchant/MerchantMore.vue";
import MerchantAnalytics from "../components/merchant/MerchantAnalytics.vue";
import MerchantStore from "../components/merchant/MerchantStore.vue";
import MerchantOrders from "../components/merchant/MerchantOrders.vue";
import MerchantProducts from "../components/merchant/MerchantProducts.vue";
import MerchantReviews from "../components/merchant/MerchantReviews.vue";
import MerchantAttention from "../components/merchant/MerchantAttention.vue";
import MerchantOnboarding from "../components/merchant/MerchantOnboarding.vue";
import MerchantOperations from "../components/merchant/MerchantOperations.vue";
import SessionNotifications from "../components/SessionNotifications.vue";
import { acceptReady } from "../components/merchant/delivery";
import { hasPickupEligibility } from "../components/merchant/mode";
import "../merchant.css";
const route = useRoute(),
  router = useRouter(),
  session = useSession();
const nav = computed(() => [
  { id: "", label: discoveryOnly.value ? "今天出摊" : "接单台", short: discoveryOnly.value ? "出摊" : "接单", icon: discoveryOnly.value ? MapPin : ClipboardList },
  { id: "products", label: "菜品管理", short: "菜品", icon: Utensils },
  { id: "more", label: "更多", short: "更多", icon: Settings2 },
]);
const section = computed(() => String(route.params.section || ""));
const orderWorkspace = computed(() => section.value === "orders" || (section.value === "" && !discoveryOnly.value));
const discoveryHome = computed(() => section.value === "" && discoveryOnly.value);
const sectionLabels: Record<string, string> = {
  orders: "接单台", store: "店铺与营业", analytics: "经营数据", reviews: "顾客评价",
};
const current = computed(() => ({ label: sectionLabels[section.value] || nav.value.find(n => n.id === section.value)?.label || "商家工作台" }));
const activeNav = computed(() => section.value === "" || (orderWorkspace.value && !discoveryOnly.value) ? "" : section.value === "products" ? "products" : "more");
const allowed = computed(
  () => !!session.user && (session.user.is_merchant || session.user.is_staff),
);
const stalls = ref<any[]>([]),
  selectedId = ref<number | null>(null),
  orders = ref<any[]>([]),
  metrics = ref<any>(null);
const orderCounts = ref<OrderCounts>({});
const loading = ref(true),
  refreshing = ref(false),
  error = ref(""),
  metricsError = ref(""),
  ordersReady = ref(false),
  metricsLoading = ref(false),
  days = ref(7);
const stall = computed(() =>
  stalls.value.find((s) => s.id === selectedId.value),
);
const discoveryOnly = computed(() => !!stall.value && !hasPickupEligibility(stall.value));
const hasOrderHistory = computed(() => orders.value.length > 0 || (orderCounts.value.all || 0) > 0);
const pending = computed(
  () => orders.value.filter((o) => o.status === "pending").length,
);
const notificationEvents = computed(() =>
  orders.value.filter(acceptReady).map((order) => ({
    id: `${order.id}:pending`,
    title: "有新订单待接单",
    body: `${stall.value?.name || "你的摊位"}有一笔新订单，请打开工作台查看。`,
    url: "/merchant/orders?filter=pending",
  })),
);
const clock = ref(Date.now());
const locationDue = computed(() => {
  if (
    !stall.value?.last_confirmed_at ||
    !["open", "paused"].includes(stall.value.status)
  )
    return false;
  const expires =
    Date.parse(stall.value.last_confirmed_at) +
    (session.config?.stale_minutes || 60) * 60000;
  return expires > clock.value && expires - clock.value <= 10 * 60000;
});
const heartbeatError = ref("");
const heartbeatTimes = new Map<string, number>();
let heartbeatController: AbortController | undefined;
async function recordReceivingHeartbeat(id: number, context: number) {
  if (document.hidden || !currentContext(context) || selectedId.value !== id)
    return;
  if (discoveryOnly.value && !orders.value.length) return;
  // Older servers do not expose receiving status or the heartbeat endpoint.
  if (
    !["unknown", "recent", "stale"].includes(
      stalls.value.find((item) => item.id === id)?.receiving_status,
    )
  )
    return;
  const key = `${session.user?.id}:${id}`,
    now = Date.now();
  if (now - (heartbeatTimes.get(key) || 0) < 30000) return;
  heartbeatTimes.set(key, now);
  heartbeatController?.abort();
  const controller = new AbortController();
  heartbeatController = controller;
  try {
    const value = await api<any>(`/merchant/stalls/${id}/receiving-heartbeat`, {
      method: "POST",
      body: {},
      signal: controller.signal,
    });
    if (!currentContext(context) || selectedId.value !== id || document.hidden)
      return;
    if (
      value?.receiving_status !== "recent" ||
      !Number.isFinite(Date.parse(value?.receiving_seen_at))
    )
      throw new Error("接单端状态待确认");
    const currentStall = stalls.value.find((item) => item.id === id);
    if (currentStall) {
      currentStall.receiving_status = value.receiving_status;
      currentStall.receiving_seen_at = value.receiving_seen_at;
    }
    heartbeatError.value = "";
  } catch {
    if (
      currentContext(context) &&
      selectedId.value === id &&
      !controller.signal.aborted
    )
      heartbeatError.value =
        "接单端在线状态未能更新，请保持页面在前台并检查连接；这不会自动暂停新单。";
  }
}
const sellable = computed(() =>
  stall.value?.products?.some(
    (p: any) => p.is_active && !p.sale_paused && p.stock > 0,
  ),
);
async function applicationApproved() {
  await router.replace("/merchant");
  if (allowed.value) await refresh();
}
let timer: ReturnType<typeof setInterval> | undefined,
  disposed = false,
  mounted = false,
  contextSeq = 0,
  refreshSeq = 0,
  orderSeq = 0,
  metricSeq = 0;
let catalogVersion = 0;
let refreshQueued = false;
const lifetime = new AbortController();
function mutationRefresh() {
  catalogVersion++;
  orderSeq++;
  metricSeq++;
  void refresh();
}
function currentContext(context: number) {
  return !disposed && allowed.value && context === contextSeq;
}
async function loadMetrics() {
  if (!selectedId.value || section.value !== "analytics") return;
  const seq = ++metricSeq,
    id = selectedId.value,
    context = contextSeq;
  metricsLoading.value = true;
  try {
    const result = await api(
      `/merchant/metrics?stall=${id}&days=${section.value === "analytics" ? days.value : 7}&mode=${stall.value?.services?.mode || "live"}`,
    );
    if (
      currentContext(context) &&
      seq === metricSeq &&
      id === selectedId.value
    ) {
      metrics.value = result;
      metricsError.value = "";
    }
  } catch (e) {
    if (
      currentContext(context) &&
      seq === metricSeq &&
      id === selectedId.value
    ) {
      metrics.value = null;
      metricsError.value = (e as Error).message;
    }
  } finally {
    if (currentContext(context) && seq === metricSeq)
      metricsLoading.value = false;
  }
}
async function loadOrders() {
  if (!selectedId.value) return;
  const id = selectedId.value,
    context = contextSeq,
    seq = ++orderSeq;
  try {
    const page = await attentionOrders(
      `/merchant/orders?stall=${id}`,
      lifetime.signal,
    );
    const result = page.results;
    if (!Array.isArray(result))
      throw new Error("订单数据未能读取，请重新同步；在线接单状态尚未更新。");
    if (
      currentContext(context) &&
      selectedId.value === id &&
      seq === orderSeq
    ) {
      orders.value = result;
      orderCounts.value = page.counts;
      ordersReady.value = true;
      void recordReceivingHeartbeat(id, context);
    }
  } catch (error) {
    // An old stall's failed response must not replace the current stall's error state.
    if (currentContext(context) && selectedId.value === id && seq === orderSeq)
      throw error;
  }
}
async function refresh() {
  if (disposed || !allowed.value) return;
  if (refreshing.value) {
    refreshQueued = true;
    return;
  }
  const version = catalogVersion;
  const context = contextSeq,
    seq = ++refreshSeq;
  let requestedStall = selectedId.value,
    requestedOrders = orderSeq;
  const canUpdateError = () =>
    currentContext(context) &&
    seq === refreshSeq &&
    requestedStall === selectedId.value &&
    requestedOrders === orderSeq;
  refreshing.value = true;
  try {
    const result = await api<any[]>("/merchant/stalls");
    if (
      !currentContext(context) ||
      seq !== refreshSeq ||
      version !== catalogVersion
    )
      return;
    stalls.value = result;
    if (!stalls.value.some((s) => s.id === selectedId.value)) {
      let saved = 0;
      try {
        saved = Number(
          localStorage.getItem(`merchant-stall:${session.user!.id}`),
        );
      } catch {
        /* Session selection still works without storage. */
      }
      selectedId.value =
        (stalls.value.find((s) => s.id === saved) || stalls.value[0])?.id ??
        null;
    }
    const ordersRequest = loadOrders();
    requestedStall = selectedId.value;
    requestedOrders = orderSeq;
    await Promise.all([ordersRequest, loadMetrics()]);
    // loadOrders discards stale results, but the outer batch must also leave a
    // newer stall/request's failure untouched when that discarded request ends.
    if (canUpdateError()) error.value = "";
  } catch (e) {
    if (canUpdateError()) error.value = (e as Error).message;
  } finally {
    if (currentContext(context) && seq === refreshSeq) {
      refreshing.value = false;
      loading.value = false;
      if (refreshQueued) {
        refreshQueued = false;
        void refresh();
      }
    }
  }
}
watch(selectedId, async (id, old) => {
  heartbeatController?.abort();
  heartbeatError.value = "";
  if (id !== null && session.user) {
    try {
      localStorage.setItem(`merchant-stall:${session.user.id}`, String(id));
    } catch {
      /* Optional preference. */
    }
  }
  if (old !== null) {
    orderSeq++;
    metricSeq++;
    orders.value = [];
    orderCounts.value = {};
    ordersReady.value = false;
    metrics.value = null;
    const context = contextSeq;
    const ordersRequest = loadOrders(),
      requestedOrders = orderSeq;
    const canUpdateError = () =>
      currentContext(context) &&
      selectedId.value === id &&
      requestedOrders === orderSeq;
    try {
      await Promise.all([ordersRequest, loadMetrics()]);
      if (canUpdateError()) error.value = "";
    } catch (e) {
      if (canUpdateError()) error.value = (e as Error).message;
    }
  }
});
watch(
  () => `${session.user?.id || "guest"}:${allowed.value}`,
  () => {
    heartbeatController?.abort();
    heartbeatError.value = "";
    contextSeq++;
    refreshSeq++;
    orderSeq++;
    metricSeq++;
    stalls.value = [];
    selectedId.value = null;
    orders.value = [];
    orderCounts.value = {};
    ordersReady.value = false;
    metrics.value = null;
    metricsLoading.value = false;
    error.value = "";
    metricsError.value = "";
    refreshing.value = false;
    loading.value = allowed.value;
    if (mounted && allowed.value) void refresh();
  },
  { flush: "sync" },
);
watch(section, () => {
  if (!["", "orders", "products", "more", "analytics", "reviews", "store", "apply"].includes(section.value)) {
    void router.replace("/merchant");
    return;
  }
  if (allowed.value) void loadMetrics();
});
function changePeriod(value: number) {
  days.value = value;
  void loadMetrics();
}
async function logout() {
  try {
    await session.logout();
    await router.push("/login?returnTo=/merchant");
  } catch (e) {
    notify((e as Error).message, "error");
  }
}
function orderChanged(updated?: any) {
  if (updated && updated.stall_id === selectedId.value) {
    // An older polling response must not undo an action that just succeeded.
    orderSeq++;
    orders.value = orders.value.map((o) => (o.id === updated.id ? updated : o));
  }
  mutationRefresh();
}
function refreshOnReturn() {
  if (document.hidden) heartbeatController?.abort();
  if (!document.hidden) void refresh();
}
onMounted(async () => {
  if (!session.loaded) await session.load();
  if (disposed) return;
  mounted = true;
  window.addEventListener("focus", refreshOnReturn);
  document.addEventListener("visibilitychange", refreshOnReturn);
  if (allowed.value) {
    await refresh();
  } else loading.value = false;
  if (!disposed)
    timer = setInterval(() => {
      clock.value = Date.now();
      if (!document.hidden && allowed.value) void refresh();
    }, 10000);
});
onUnmounted(() => {
  lifetime.abort();
  heartbeatController?.abort();
  disposed = true;
  clearInterval(timer);
  window.removeEventListener("focus", refreshOnReturn);
  document.removeEventListener("visibilitychange", refreshOnReturn);
  contextSeq++;
  refreshSeq++;
  orderSeq++;
  metricSeq++;
});
</script>
<template>
  <div v-if="!allowed || section === 'apply'" class="m-application-page">
    <RouterLink class="brand" to="/merchant"><span class="brand-mark"><Store :size="25" /></span><span>烟火地图<small>商家入驻</small></span></RouterLink>
    <MerchantOnboarding :key="session.user?.id || 'guest'" @approved="applicationApproved" />
    <RouterLink to="/" class="m-text-link" @click="session.setConsumerPreview(true)">去学生端逛逛 <ArrowUpRight :size="14" /></RouterLink>
  </div>
  <div v-else class="m-shell" :class="{ 'm-orders-workspace': orderWorkspace }">
    <aside class="m-sidebar">
      <RouterLink to="/merchant" class="m-brand"><span><Store :size="27" /></span><div>烟火地图<small>商家工作台</small></div></RouterLink>
      <nav aria-label="商家导航">
        <RouterLink v-for="n in nav" :key="n.id" :to="n.id ? `/merchant/${n.id}` : '/merchant'" :class="{ active: activeNav === n.id }">
          <component :is="n.icon" :size="21" /><span>{{ n.label }}</span><b v-if="n.id === '' && pending">{{ pending }}</b>
        </RouterLink>
      </nav>
      <div class="m-sidebar-bottom">
        <RouterLink to="/" @click="session.setConsumerPreview(true)"><ArrowUpRight :size="17" />预览学生端</RouterLink>
        <button @click="logout"><LogOut :size="17" />退出登录</button>
      </div>
    </aside>
    <div class="m-workspace">
      <header class="m-topbar">
        <div class="m-mobile-brand"><Store :size="23" /><h1>{{ current.label }}</h1></div>
        <div class="m-breadcrumb">烟火地图 <span>/</span><strong>{{ current.label }}</strong></div>
        <div class="m-topbar-right">
          <span v-if="session.config?.demo_mode" class="m-preview-label">{{ session.config.services_simulation_enabled ? '模拟体验' : '示例环境' }}</span>
          <button class="m-refresh-button" :disabled="refreshing" @click="refresh" aria-label="刷新工作台"><RefreshCw :size="18" :class="{ 'm-spinning': refreshing }" /><span>刷新</span></button>
        </div>
      </header>
      <div class="m-content">
        <div class="m-page-heading">
          <h1>{{ current.label }}</h1>
          <RouterLink v-if="!['', 'products', 'more'].includes(section)" to="/merchant/more" class="m-back-link">返回更多</RouterLink>
        </div>
        <div v-if="loading" class="m-panel m-empty"><span class="spinner" /><p>正在读取工作台…</p></div>
        <template v-else>
          <p v-if="error" class="m-alert" role="alert">{{ error }}</p>
          <MerchantOnboarding v-if="!stall" :key="session.user!.id" @approved="applicationApproved" />
          <template v-else>
            <label v-if="stalls.length > 1" class="m-work-stall-selector"><span>当前摊位</span><select v-model="selectedId" aria-label="选择管理的摊位"><option v-for="s in stalls" :key="s.id" :value="s.id">{{ s.name }}</option></select></label>
            <MerchantOperations v-if="discoveryHome || (orderWorkspace && !discoveryOnly)" :compact="!discoveryHome" :discovery-only="discoveryOnly" :key="`${session.user!.id}:${stall.id}`" :stall="stall" :orders="orders" :ready="ordersReady" @refresh="mutationRefresh" @location="router.push('/merchant/store#location')" />
            <p v-if="orderWorkspace && !discoveryOnly && (!stall.is_visible || !stall.activation?.has_location)" class="m-setup-link"><span>完成开摊准备后，才能接到新订单。</span><RouterLink to="/merchant/more#activation">查看准备事项 <ArrowRight :size="16" /></RouterLink></p>
            <p v-else-if="orderWorkspace && locationDue" class="m-setup-link"><span>位置即将过期，请核对后确认。</span><RouterLink to="/merchant/store">确认位置 <ArrowRight :size="16" /></RouterLink></p>
            <p v-if="heartbeatError" class="m-alert" role="status">{{ heartbeatError }}</p>
            <MerchantOrders v-if="orderWorkspace" :key="`${session.user!.id}:${stall.id}:${route.query.filter || 'active'}`" :stall="stall" :orders="orders" :orders-ready="ordersReady" :sync-error="error" :loading="refreshing" :initial-filter="String(route.query.filter || 'active')" :counts="orderCounts" @refresh="orderChanged" />
            <MerchantProducts v-else-if="section === 'products'" :key="stall.id" :stall="stall" :discovery-only="discoveryOnly" @refresh="mutationRefresh" />
            <MerchantMore v-else-if="section === 'more'" :stall="stall" :sellable="!!sellable" :discovery-only="discoveryOnly" @logout="logout" />
            <MerchantAnalytics v-else-if="section === 'analytics'" :metrics="metrics" :days="days" :error="metricsError" :loading="metricsLoading" @period="changePeriod" />
            <MerchantReviews v-else-if="section === 'reviews'" :key="stall.id" :stall="stall" @refresh="mutationRefresh" />
            <MerchantStore v-else-if="section === 'store'" :key="`${session.user!.id}:${stall.id}`" :stall="stall" :orders="orders" :orders-ready="ordersReady" :discovery-only="discoveryOnly" @refresh="mutationRefresh" />
            <div id="notifications" :class="{ 'm-reminder-settings': section === 'more' }">
              <h2 v-if="section === 'more' && (!discoveryOnly || hasOrderHistory)">{{ discoveryOnly ? '已有订单提醒' : '新订单提醒' }}</h2>
              <MerchantAttention :key="session.user!.id" :scope="`${session.user!.id}:${stall.id}`" :orders="orders" :ready="ordersReady" :sync-error="error" :compact-pending="orderWorkspace" :show-controls="section === 'more' && (!discoveryOnly || hasOrderHistory)" :show-tasks="!orderWorkspace" @expired="refresh" @refresh="mutationRefresh" />
              <SessionNotifications v-show="section === 'more' && (!discoveryOnly || hasOrderHistory)" :key="`${session.user!.id}:${stall.id}`" :user-id="session.user!.id" :events="notificationEvents" :ready="ordersReady && !error" />
            </div>
          </template>
        </template>
      </div>
      <nav class="m-mobile-nav" aria-label="商家底部导航">
        <RouterLink v-for="n in nav" :key="n.id" :to="n.id ? `/merchant/${n.id}` : '/merchant'" :class="{ active: activeNav === n.id }"><component :is="n.icon" :size="23" /><span>{{ n.short }}</span><i v-if="n.id === '' && pending">{{ pending }}</i></RouterLink>
      </nav>
    </div>
  </div>
</template>
<style scoped>
.m-application-page { max-width: 920px; margin: 0 auto; padding: 28px 18px; }
</style>
