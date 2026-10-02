<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Store,
  Map,
  House,
  ReceiptText,
  UserRound,
  ChevronDown,
  ArrowUpRight,
  Flame,
  Check,
  AlertCircle,
  X,
} from "lucide-vue-next";
import { useSession } from "./stores/session";
import { useDiscovery } from "./stores/discovery";
import { notices } from "./lib/notify";
import { isMerchantPath, safeReturnTo } from "./lib/identity";
import CartShortcut from "./components/CartShortcut.vue";
import StudentOrderStatus from "./components/StudentOrderStatus.vue";
const session = useSession(),
  discovery = useDiscovery(),
  route = useRoute(),
  router = useRouter(),
  error = ref("");
const mainNav = computed(() =>
  ["/", "/map", "/orders", "/me", "/search", "/cart"].includes(route.path),
);
const merchantArea = computed(
  () =>
    isMerchantPath(route.path) ||
    (route.path === "/login" &&
      (route.query.role === "merchant" ||
        isMerchantPath(safeReturnTo(route.query.returnTo)))),
);
const consumerPreview = computed(
  () =>
    session.isMerchant &&
    !merchantArea.value &&
    !["/login", "/recover", "/account-security"].includes(route.path),
);
const consumerShell = computed(
  () =>
    session.loaded &&
    route.matched.length > 0 &&
    !["/recover", "/account-security"].includes(route.path) &&
    !merchantArea.value &&
    !(session.isMerchant && route.path === "/" && !session.consumerPreview),
);
const areaName = computed(
  () =>
    session.config?.areas.find((a) => a.id === discovery.area)?.name ||
    "选择校园",
);
async function load() {
  error.value = "";
  try {
    await session.load();
  } catch (e) {
    error.value = (e as Error).message;
  }
}
let refreshingIdentity = false;
async function refreshIdentity() {
  if (document.hidden || !session.loaded || refreshingIdentity) return;
  refreshingIdentity = true;
  try {
    await session.refreshUser();
  } catch {
    /* Existing screens retain their retryable API errors. */
  } finally {
    refreshingIdentity = false;
  }
}
watch(
  () => [
    session.loaded,
    session.isMerchant,
    session.consumerPreview,
    route.path,
  ],
  () => {
    if (
      // An unresolved initial route looks like "/" while its guard loads the session.
      // Let the router finish that navigation before reacting to later identity changes.
      route.matched.length > 0 &&
      session.loaded &&
      session.isMerchant &&
      !session.consumerPreview &&
      route.path === "/"
    )
      void router.replace("/merchant");
  },
);
onMounted(() => {
  void load();
  window.addEventListener("focus", refreshIdentity);
  document.addEventListener("visibilitychange", refreshIdentity);
});
onUnmounted(() => {
  window.removeEventListener("focus", refreshIdentity);
  document.removeEventListener("visibilitychange", refreshIdentity);
});
</script>
<template>
  <div v-if="consumerPreview" class="consumer-preview-bar">
    <span><Store :size="16" /> 正在预览学生端</span>
    <RouterLink to="/merchant"
      >返回商家工作台 <ArrowUpRight :size="16"
    /></RouterLink>
  </div>
  <header v-if="consumerShell" class="site-header">
    <div class="header-inner">
      <RouterLink class="brand" to="/" aria-label="烟火地图首页"
        ><span class="brand-mark"><Store :size="25" :stroke-width="1.8" /></span
        ><span>烟火地图<small>发现身边的好味道</small></span></RouterLink
      >
      <div class="header-location">
        <Map :size="15" /><select
          :value="discovery.area"
          @change="
            discovery.setArea(
              Number(($event.target as HTMLSelectElement).value),
            )
          "
          aria-label="选择校园"
        >
          <option :value="0">全校周边</option>
          <option v-for="a in session.config?.areas" :key="a.id" :value="a.id">
            {{ a.name }}
          </option></select
        ><ChevronDown :size="13" />
      </div>
      <nav class="desktop-nav" aria-label="主导航">
        <RouterLink
          to="/"
          :class="{ active: route.path === '/' || route.path === '/search' }"
          >发现好味</RouterLink
        ><RouterLink to="/map" :class="{ active: route.path === '/map' }"
          >地图找摊</RouterLink
        ><RouterLink
          to="/orders"
          :class="{ active: route.path.startsWith('/orders') }"
          >我的订单</RouterLink
        >
      </nav>
      <CartShortcut class="header-cart" />
      <RouterLink class="header-user" to="/me"
        ><span class="user-dot"><UserRound :size="17" /></span
        ><span>{{ session.user?.display_name || "我的" }}</span></RouterLink
      >
    </div>
  </header>
  <div v-if="session.config?.demo_mode && consumerShell" class="demo-strip">
    <Flame :size="12" />
    {{
      session.config?.services_simulation_enabled
        ? "模拟体验 · 微信支付与配送仅供试跑，不会扣款或送货"
        : "校园体验站 · 摊位与账号均为示例，订单仅用于测试"
    }}
    <RouterLink
      v-if="session.user?.is_merchant || session.user?.is_staff"
      to="/merchant"
      >商家工作台 <ArrowUpRight :size="12"
    /></RouterLink>
  </div>
  <main :class="['app-main', { 'with-nav': mainNav }]">
    <StudentOrderStatus
      v-if="
        consumerShell &&
        session.user &&
        !session.isMerchant &&
        ['/', '/map', '/search', '/me', '/cart'].includes(route.path)
      "
      :key="session.user.id"
    />
    <div v-if="error" class="page empty-state">
      <AlertCircle :size="36" />
      <h1>暂时连接不上</h1>
      <p>{{ error }}</p>
      <button class="btn btn-primary" @click="load">重新连接</button>
    </div>
    <RouterView v-else v-slot="{ Component }"
      ><component
        :is="Component"
        :key="`${isMerchantPath(route.path) ? 'merchant' : route.path}:${session.user?.id || 'guest'}`"
        v-if="session.loaded" />
      <div v-else class="page loading-shell">
        <div class="skeleton skeleton-banner"></div>
        <div class="skeleton skeleton-line"></div>
        <div class="skeleton-grid">
          <div v-for="n in 6" :key="n" class="skeleton skeleton-card"></div>
        </div></div
    ></RouterView>
  </main>
  <footer v-if="consumerShell" class="site-footer">
    <span><Store :size="16" /> 烟火地图</span>
    <p>
      好好吃饭，慢慢发现生活。<span v-if="session.config?.demo_mode">
        · 校园示例环境</span
      >
    </p>
    <RouterLink to="/me">帮助与反馈 <ArrowUpRight :size="13" /></RouterLink>
    <RouterLink :to="session.isMerchant ? '/merchant' : '/merchant/apply'"
      >{{ session.isMerchant ? "商家工作台" : "我是商家，申请入驻" }}
      <ArrowUpRight :size="13"
    /></RouterLink>
  </footer>
  <nav v-if="mainNav && consumerShell" class="mobile-nav" aria-label="底部导航">
    <RouterLink
      to="/"
      :class="{ active: route.path === '/' || route.path === '/search' }"
      ><House :size="21" /><span>首页</span></RouterLink
    ><RouterLink to="/map" :class="{ active: route.path === '/map' }"
      ><Map :size="21" /><span>地图</span></RouterLink
    ><RouterLink
      to="/orders"
      :class="{ active: route.path.startsWith('/orders') }"
      ><ReceiptText :size="21" /><span>订单</span></RouterLink
    ><RouterLink to="/me" :class="{ active: route.path === '/me' }"
      ><UserRound :size="21" /><span>我的</span></RouterLink
    >
  </nav>
  <div class="toast-stack" aria-live="polite">
    <div v-for="n in notices" :key="n.id" :class="['toast', n.type]">
      <Check v-if="n.type === 'success'" :size="18" /><AlertCircle
        v-else
        :size="18"
      /><span>{{ n.message }}</span
      ><button
        @click="notices = notices.filter((x) => x.id !== n.id)"
        aria-label="关闭提示"
      >
        <X :size="15" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.consumer-preview-bar {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 24px;
  min-height: 44px;
  padding: 8px 20px;
  color: #ffead4;
  background: #31463b;
  font-size: 12px;
}
.consumer-preview-bar span,
.consumer-preview-bar a {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.header-inner {
  gap: 24px;
}
@media (max-width: 767px) {
  .header-inner {
    gap: 9px;
  }
  .header-location {
    min-width: 0;
  }
  .header-location select {
    max-width: 84px;
  }
  .header-cart {
    position: relative;
    padding: 0;
    width: 44px;
  }
  .header-cart :deep(.cart-shortcut-label) {
    display: none;
  }
  .header-cart :deep(.cart-shortcut-count) {
    position: absolute;
    top: -5px;
    right: -5px;
    min-width: 18px;
    height: 18px;
    font-size: 10px;
  }
}
.consumer-preview-bar a {
  min-height: 28px;
  color: #fff;
  font-weight: 700;
}
</style>
