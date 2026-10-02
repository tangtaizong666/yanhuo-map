<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Store,
  LayoutDashboard,
  ClipboardList,
  Utensils,
  ChartNoAxesCombined,
  MessageSquare,
  Settings2,
  ArrowUpRight,
  LogOut,
  RefreshCw,
  MapPin,
  ChevronDown,
  Bell,
  ShieldCheck,
  ArrowRight,
  CircleCheck,
  ChefHat,
  AlertCircle,
} from "lucide-vue-next";
import { api, statusText, confirmedText } from "../lib/api";
import { useSession } from "../stores/session";
import { notify } from "../lib/notify";
import MerchantDashboard from "../components/merchant/MerchantDashboard.vue";
import MerchantAnalytics from "../components/merchant/MerchantAnalytics.vue";
import MerchantStore from "../components/merchant/MerchantStore.vue";
import MerchantOrders from "../components/merchant/MerchantOrders.vue";
import MerchantProducts from "../components/merchant/MerchantProducts.vue";
import MerchantReviews from "../components/merchant/MerchantReviews.vue";
import MerchantAttention from "../components/merchant/MerchantAttention.vue";
import MerchantOnboarding from "../components/merchant/MerchantOnboarding.vue";
import MerchantOperations from "../components/merchant/MerchantOperations.vue";
import StallShare from "../components/StallShare.vue";
import SessionNotifications from "../components/SessionNotifications.vue";
import { acceptReady } from "../components/merchant/delivery";
import "../merchant.css";
const route = useRoute(),
  router = useRouter(),
  session = useSession();
const nav = [
  { id: "", label: "经营首页", short: "首页", icon: LayoutDashboard },
  { id: "orders", label: "订单处理", short: "订单", icon: ClipboardList },
  { id: "products", label: "商品管理", short: "商品", icon: Utensils },
  {
    id: "analytics",
    label: "经营数据",
    short: "数据",
    icon: ChartNoAxesCombined,
  },
  { id: "reviews", label: "顾客评价", short: "评价", icon: MessageSquare },
  { id: "store", label: "店铺设置", short: "店铺", icon: Settings2 },
];
const section = computed(() => String(route.params.section || ""));
const current = computed(
  () => nav.find((n) => n.id === section.value) || nav[0],
);
const allowed = computed(
  () => !!session.user && (session.user.is_merchant || session.user.is_staff),
);
const stalls = ref<any[]>([]),
  selectedId = ref<number | null>(null),
  orders = ref<any[]>([]),
  metrics = ref<any>(null);
const loading = ref(true),
  refreshing = ref(false),
  error = ref(""),
  metricsError = ref(""),
  ordersReady = ref(false),
  metricsLoading = ref(false),
  days = ref(7),
  confirmedBusy = ref(false);
const stall = computed(() =>
  stalls.value.find((s) => s.id === selectedId.value),
);
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
const dateText = new Date().toLocaleDateString("zh-CN", {
  month: "long",
  day: "numeric",
  weekday: "long",
});
let timer: ReturnType<typeof setInterval> | undefined,
  disposed = false,
  mounted = false,
  contextSeq = 0,
  refreshSeq = 0,
  orderSeq = 0,
  metricSeq = 0;
function currentContext(context: number) {
  return !disposed && allowed.value && context === contextSeq;
}
function navigate(page: string, filter?: string) {
  router.push({
    path: page ? `/merchant/${page}` : "/merchant",
    query: filter ? { filter } : {},
  });
}
async function loadMetrics() {
  if (!selectedId.value) return;
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
    const result = await api<any[]>(`/merchant/orders?stall=${id}`);
    if (!Array.isArray(result))
      throw new Error("订单数据未能读取，请重新同步；在线接单状态尚未更新。");
    if (
      currentContext(context) &&
      selectedId.value === id &&
      seq === orderSeq
    ) {
      orders.value = result;
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
  if (disposed || refreshing.value || !allowed.value) return;
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
    if (!currentContext(context) || seq !== refreshSeq) return;
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
  if (!nav.some((n) => n.id === section.value) && section.value !== "apply") {
    void router.replace("/merchant");
    return;
  }
  if (allowed.value) void loadMetrics();
});
function changePeriod(value: number) {
  days.value = value;
  void loadMetrics();
}
async function confirmLocation() {
  if (!stall.value || confirmedBusy.value) return;
  if (
    !stall.value.address ||
    stall.value.latitude == null ||
    stall.value.longitude == null
  ) {
    await router.push("/merchant/store#location");
    notify("请先设置实际取餐位置，再确认出摊。", "info");
    return;
  }
  confirmedBusy.value = true;
  try {
    await api(`/merchant/stalls/${stall.value.id}/status`, {
      method: "POST",
      body: {
        status:
          stall.value.status === "closed"
            ? "closed"
            : stall.value.session_status ||
              (stall.value.status === "stale" ? "paused" : stall.value.status),
        confirm_location: true,
      },
    });
    await refresh();
    notify("位置已确认", "success");
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    confirmedBusy.value = false;
  }
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
  void refresh();
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
    <RouterLink class="brand" to="/merchant"
      ><span class="brand-mark"><Store :size="25" /></span
      ><span>烟火地图<small>商家入驻</small></span></RouterLink
    >
    <MerchantOnboarding
      :key="session.user?.id || 'guest'"
      @approved="applicationApproved"
    />
    <RouterLink
      to="/"
      class="m-text-link"
      @click="session.setConsumerPreview(true)"
      >去学生端逛逛 <ArrowUpRight :size="14"
    /></RouterLink>
  </div>
  <div
    v-else
    class="m-shell"
    :class="{ 'm-orders-workspace': section === 'orders' }"
  >
    <aside class="m-sidebar">
      <RouterLink to="/merchant" class="m-brand"
        ><span><Store :size="27" /></span>
        <div>烟火地图<small>商家工作台</small></div></RouterLink
      >
      <p class="m-nav-caption">把好手艺，做成好生意</p>
      <nav aria-label="商家导航">
        <RouterLink
          v-for="n in nav"
          :key="n.id"
          :to="n.id ? `/merchant/${n.id}` : '/merchant'"
          :class="{ active: section === n.id }"
          ><component :is="n.icon" :size="20" /><span>{{ n.label }}</span
          ><b v-if="n.id === 'orders' && pending">{{ pending }}</b></RouterLink
        >
      </nav>
      <div class="m-sidebar-bottom">
        <div class="m-sidebar-note">
          <ChefHat :size="25" /><strong>小小摊位，也有大生意</strong>
          <p>认真对待每一份热乎的期待。</p>
        </div>
        <RouterLink to="/" @click="session.setConsumerPreview(true)"
          ><ArrowUpRight :size="17" /> 预览学生端</RouterLink
        ><button @click="logout"><LogOut :size="17" /> 退出登录</button>
      </div>
    </aside>
    <div class="m-workspace">
      <header class="m-topbar">
        <div class="m-mobile-brand">
          <Store :size="23" /><b>烟火地图</b><span>商家版</span>
        </div>
        <div class="m-breadcrumb">
          商家工作台 <span>/</span> <strong>{{ current.label }}</strong>
        </div>
        <div class="m-topbar-right">
          <span class="m-desktop-date">{{ dateText }}</span
          ><button
            class="m-bell"
            @click="navigate('orders', 'pending')"
            aria-label="查看待接单订单"
          >
            <Bell :size="20" /><i v-if="pending" /></button
          ><button
            class="m-avatar"
            @click="navigate('store')"
            aria-label="店铺设置"
          >
            {{ session.user?.display_name.slice(0, 1) }}
          </button>
        </div>
      </header>
      <div v-if="session.config?.demo_mode" class="m-demo-strip">
        <ShieldCheck :size="13" /> 商家体验站 ·
        示例数据，模拟支付与配送不涉及真实交易
      </div>
      <div class="m-content">
        <div class="m-page-heading">
          <div>
            <span class="m-eyebrow">{{
              section ? "让经营的每一步，都更从容" : "今天，也好好出摊"
            }}</span>
            <h1>{{ current.label }}<span>.</span></h1>
          </div>
          <button
            class="btn btn-secondary m-refresh"
            @click="refresh"
            :disabled="refreshing"
          >
            <RefreshCw :size="16" :class="{ 'm-spinning': refreshing }" /><span
              >刷新工作台</span
            >
          </button>
        </div>
        <div v-if="loading" class="m-panel m-empty">
          <span class="spinner" />
          <p>正在准备商家工作台…</p>
        </div>
        <template v-else
          ><p v-if="error" class="m-alert" role="alert">{{ error }}</p>
          <MerchantOnboarding
            v-if="!stall"
            :key="session.user!.id"
            @approved="applicationApproved" />
          <template v-else
            ><section class="m-stall-bar">
              <img :src="stall.image" :alt="stall.name" />
              <div class="m-stall-bar-info">
                <label class="m-stall-selector"
                  ><select v-model="selectedId" aria-label="选择管理的摊位">
                    <option v-for="s in stalls" :key="s.id" :value="s.id">
                      {{ s.name }}
                    </option></select
                  ><ChevronDown :size="15"
                /></label>
                <div>
                  <span :class="['m-status', stall.status]">{{
                    statusText(stall.status)
                  }}</span
                  ><span class="m-stall-address"
                    ><MapPin :size="12" />{{ stall.area_name }}</span
                  >
                </div>
              </div>
              <div class="m-stall-confirm">
                <span>{{ confirmedText(stall.last_confirmed_at) }}</span
                ><button @click="confirmLocation" :disabled="confirmedBusy">
                  <CircleCheck :size="15" /> 我还在这里
                </button>
              </div>
            </section>
            <div v-if="stall.status === 'stale'" class="m-alert">
              <AlertCircle :size="18" />
              位置确认已过期，新订单暂停。请核对实际位置后确认“我还在这里”。
            </div>
            <div v-else-if="locationDue" class="m-info-banner" role="status">
              <MapPin :size="18" /> 位置将在 10
              分钟内需要重新确认。仍在原处时，点击“我还在这里”，避免同学跑空。
            </div>
            <div v-if="!stall.transaction_enabled" class="m-info-banner">
              <ShieldCheck :size="17" />
              当前摊位仅展示，在线接单需由运营核验后开通。
            </div>
            <MerchantOperations
              v-if="section === ''"
              :key="`${session.user!.id}:${stall.id}`"
              :stall="stall"
              :orders="orders"
              :ready="ordersReady"
              @refresh="refresh"
              @location="router.push('/merchant/store#location')"
            />
            <MerchantAttention
              :key="session.user!.id"
              :scope="`${session.user!.id}:${stall.id}`"
              :orders="orders"
              :ready="ordersReady"
              :sync-error="error"
              :compact-pending="
                section === 'orders' ||
                (section === '' && stall.status === 'open')
              "
              @expired="refresh"
              @refresh="refresh"
            />
            <p
              v-if="
                heartbeatError ||
                stall.receiving_status === 'stale' ||
                stall.receiving_status === 'unknown'
              "
              class="m-info-banner"
              role="status"
            >
              {{
                heartbeatError ||
                "接单端最近未同步，请确认有人照看订单。营业展示和新单开关不会因这个提示自动改变。"
              }}
            </p>
            <SessionNotifications
              :key="`${session.user!.id}:${stall.id}`"
              :user-id="session.user!.id"
              :events="notificationEvents"
              :ready="ordersReady && !error"
            />
            <template v-if="section === ''">
              <section
                v-if="!sellable || !stall.address || !stall.transaction_enabled"
                class="m-panel m-preparation"
                aria-label="开摊准备"
              >
                <h2>先准备好，再让同学来找你</h2>
                <p>资料可以分步完善。营业展示与线上接单分别核验。</p>
                <div>
                  <RouterLink to="/merchant/products"
                    >{{
                      sellable ? "✓ 菜单已有可售商品" : "1. 添加第一道可售商品"
                    }}
                    <ArrowRight :size="16" /></RouterLink
                  ><RouterLink to="/merchant/store#location"
                    >{{
                      stall.address ? "✓ 核对今天的取餐位置" : "2. 设置取餐位置"
                    }}
                    <ArrowRight :size="16" /></RouterLink
                  ><span>{{
                    stall.transaction_enabled
                      ? "✓ 线上接单已获核验"
                      : "3. 线上接单等待运营核验；不会自动开通"
                  }}</span>
                </div>
              </section>
              <StallShare :stall="stall" compact />
            </template>
            <MerchantDashboard
              v-if="section === ''"
              :stall="stall"
              :orders="orders"
              :metrics="metrics"
              :metrics-error="metricsError"
              :orders-ready="ordersReady"
              :sync-error="error"
              @navigate="navigate"
            />
            <MerchantOrders
              v-else-if="section === 'orders'"
              :key="`${stall.id}:${route.query.filter || 'active'}`"
              :stall="stall"
              :orders="orders"
              :orders-ready="ordersReady"
              :sync-error="error"
              :loading="refreshing"
              :initial-filter="String(route.query.filter || 'active')"
              @refresh="orderChanged"
            />
            <MerchantProducts
              v-else-if="section === 'products'"
              :key="stall.id"
              :stall="stall"
              @refresh="refresh"
            />
            <MerchantAnalytics
              v-else-if="section === 'analytics'"
              :metrics="metrics"
              :days="days"
              :error="metricsError"
              :loading="metricsLoading"
              @period="changePeriod"
            />
            <MerchantReviews
              v-else-if="section === 'reviews'"
              :key="stall.id"
              :stall="stall"
              @refresh="refresh"
            />
            <MerchantStore
              v-else-if="section === 'store'"
              :key="`${session.user!.id}:${stall.id}`"
              :stall="stall"
              :orders="orders"
              :orders-ready="ordersReady"
              @refresh="refresh"
            /> </template
        ></template>
        <footer class="m-workspace-footer">
          <div class="m-mobile-account">
            <RouterLink to="/merchant/reviews">顾客评价</RouterLink
            ><RouterLink to="/" @click="session.setConsumerPreview(true)"
              >预览学生端</RouterLink
            ><button @click="logout">退出登录</button>
          </div>
          烟火地图 · 好手艺，值得被看见
        </footer>
      </div>
      <nav class="m-mobile-nav" aria-label="商家底部导航">
        <RouterLink
          v-for="n in nav.filter((n) => n.id !== 'reviews')"
          :key="n.id"
          :to="n.id ? `/merchant/${n.id}` : '/merchant'"
          :class="{
            active:
              section === n.id || (n.id === 'store' && section === 'reviews'),
          }"
          ><component :is="n.icon" :size="20" /><span>{{ n.short }}</span
          ><i v-if="n.id === 'orders' && pending">{{ pending }}</i></RouterLink
        >
      </nav>
    </div>
  </div>
</template>
<style scoped>
.m-application-page {
  max-width: 920px;
  margin: 0 auto;
  padding: 28px 18px;
}
.m-preparation {
  margin-bottom: 20px;
}
.m-preparation h2 {
  font-size: 20px;
  margin-top: 0;
}
.m-preparation p {
  color: #7a6552;
  line-height: 1.7;
  font-size: 14px;
}
.m-preparation > div {
  display: grid;
  gap: 8px;
}
.m-preparation a,
.m-preparation > div > span {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  min-height: 48px;
  padding: 10px 14px;
  border-radius: 12px;
  background: #fff5e9;
  color: #765238;
  font-size: 14px;
  box-sizing: border-box;
}
.m-preparation > div > span {
  background: #f6f2eb;
}
</style>
