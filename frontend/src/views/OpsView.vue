<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowUpRight,
  ClipboardCheck,
  KeyRound,
  LayoutDashboard,
  LayoutGrid,
  LogOut,
  MapPin,
  MessageSquareOff,
  MessageSquareWarning,
  ReceiptText,
  RefreshCw,
  ShieldCheck,
  Store,
  Users,
  X,
} from "lucide-vue-next";
import { api } from "../lib/api";
import { isOpsLoginPath } from "../lib/identity";
import { notify } from "../lib/notify";
import { useSession } from "../stores/session";
import type { Config, OpsPermissions } from "../lib/types";
import OpsOverview from "../components/ops/OpsOverview.vue";
import OpsApplications from "../components/ops/OpsApplications.vue";
import OpsStalls from "../components/ops/OpsStalls.vue";
import OpsAreas from "../components/ops/OpsAreas.vue";
import OpsOrders from "../components/ops/OpsOrders.vue";
import OpsFeedback from "../components/ops/OpsFeedback.vue";
import OpsReviews from "../components/ops/OpsReviews.vue";
import OpsAuditUsers from "../components/ops/OpsAuditUsers.vue";
import "../ops.css";

const route = useRoute();
const router = useRouter();
const session = useSession();

const overviewData = ref<any>(null);
const loading = ref(true);
const refreshing = ref(false);
const error = ref("");
const days = ref(7);
const mode = ref<"live" | "simulation" | null>(null);

const validSections = [
  "",
  "applications",
  "stalls",
  "areas",
  "orders",
  "feedback",
  "reviews",
  "users",
];

const section = computed(() => String(route.params.section || ""));

const navItems = computed(() => {
  const s = overviewData.value?.summary || {};
  const attentionOrders =
    (s.orders_live_attention || 0) + (s.orders_simulation_attention || 0);
  return [
    {
      id: "",
      label: "治理总览",
      short: "总览",
      icon: LayoutDashboard,
      badge: 0,
      allowed: true,
    },
    {
      id: "applications",
      label: "入驻审核",
      short: "入驻",
      icon: ClipboardCheck,
      badge: s.applications_submitted || 0,
      allowed: session.hasOpsPerm("view_applications"),
    },
    {
      id: "stalls",
      label: "商户与摊位",
      short: "摊位",
      icon: Store,
      badge: s.stalls_stale || 0,
      allowed: session.hasOpsPerm("view_merchants"),
    },
    {
      id: "areas",
      label: "区域与交接点",
      short: "区域",
      icon: MapPin,
      badge: 0,
      allowed: session.hasOpsPerm("view_areas"),
    },
    {
      id: "orders",
      label: "订单与对账",
      short: "订单",
      icon: ReceiptText,
      badge: attentionOrders,
      allowed:
        session.hasOpsPerm("view_orders") ||
        session.hasOpsPerm("export_reconciliation"),
    },
    {
      id: "feedback",
      label: "位置与反馈",
      short: "反馈",
      icon: MessageSquareWarning,
      badge: s.feedback_unresolved || 0,
      allowed: session.hasOpsPerm("view_feedback"),
    },
    {
      id: "reviews",
      label: "评价与风控",
      short: "评价",
      icon: MessageSquareOff,
      badge: s.reviews_flagged || 0,
      allowed: session.hasOpsPerm("view_reviews"),
    },
    {
      id: "users",
      label: "账号与审计",
      short: "账号",
      icon: Users,
      badge: 0,
      allowed: true,
    },
  ];
});

const visibleNav = computed(() => navItems.value.filter((item) => item.allowed));
const mobilePrimaryNav = computed(() => {
  const preferred = ["", "applications", "orders", "reviews"];
  const picked = preferred
    .map((id) => visibleNav.value.find((item) => item.id === id))
    .filter((item): item is NonNullable<typeof item> => Boolean(item));
  for (const item of visibleNav.value) {
    if (picked.length >= 4) break;
    if (!picked.some((p) => p.id === item.id)) picked.push(item);
  }
  return picked.slice(0, 4);
});
const mobileSecondaryNav = computed(() => {
  const primaryIds = new Set(mobilePrimaryNav.value.map((item) => item.id));
  return visibleNav.value.filter((item) => !primaryIds.has(item.id));
});
const mobileSheetOpen = ref(false);
const secondaryBadgeTotal = computed(() =>
  mobileSecondaryNav.value.reduce((sum, item) => sum + (item.badge || 0), 0),
);
const isSecondarySection = computed(() =>
  mobileSecondaryNav.value.some((item) => item.id === section.value),
);
const currentNav = computed(
  () =>
    navItems.value.find((item) => item.id === section.value) || navItems.value[0],
);

const effectiveMode = computed<"live" | "simulation">(
  () => mode.value || overviewData.value?.pilot?.mode || "live",
);
const refreshTick = ref(0);

async function refreshOverview() {
  if (!session.isAdmin) {
    loading.value = false;
    return;
  }
  refreshing.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({ days: String(days.value) });
    if (mode.value) params.set("mode", mode.value);
    const res = await api(`/ops/overview?${params.toString()}`);
    overviewData.value = res;
    if (session.user && res.permissions) {
      session.user = {
        ...session.user,
        ops_permissions: res.permissions as Partial<OpsPermissions>,
      };
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
    refreshing.value = false;
  }
}

watch(
  section,
  (val) => {
    mobileSheetOpen.value = false;
    if (!validSections.includes(val)) {
      void router.replace("/ops");
    }
  },
  { immediate: true },
);

watch(
  () => session.user?.id,
  () => {
    if (session.isAdmin) void refreshOverview();
  },
);

function handleTopRefresh() {
  refreshTick.value += 1;
  void refreshOverview();
}

async function handleAreasChanged() {
  void refreshOverview();
  try {
    const latest = await api<Config>("/config");
    if (session.config) {
      session.config = { ...session.config, areas: latest.areas };
    }
  } catch {
    /* Overview still updates independently if public config reload fails. */
  }
}

function changePeriod(nextDays: number) {
  days.value = nextDays;
  void refreshOverview();
}

function changeMode(nextMode: "live" | "simulation") {
  mode.value = nextMode;
  void refreshOverview();
}

function goSection(sec: string) {
  void router.push(sec ? `/ops/${sec}` : "/ops");
}

async function logout() {
  try {
    await session.logout();
    await router.push("/ops/login");
  } catch (e) {
    notify((e as Error).message, "error");
  }
}

async function switchToAdminLogin() {
  try {
    await session.logout();
    await router.push("/ops/login");
  } catch (e) {
    notify((e as Error).message, "error");
  }
}

onMounted(async () => {
  if (!session.loaded) await session.load();
  if (!session.user) {
    if (!isOpsLoginPath(route.path)) {
      await router.replace({
        path: "/ops/login",
        query: route.fullPath !== "/ops" ? { returnTo: route.fullPath } : {},
      });
    }
    return;
  }
  if (session.isAdmin) {
    await refreshOverview();
  } else {
    loading.value = false;
  }
});
</script>

<template>
  <div v-if="!session.isAdmin" class="ops-login-page">
    <div class="ops-login-card" style="max-width: 520px; margin: 48px auto">
      <span class="eyebrow">ACCESS RESTRICTED</span>
      <h2>需要平台管理员权限</h2>
      <p class="ops-login-sub">
        当前登录账号（{{ session.user?.username || "未登录" }}）不是平台运营或治理人员账号，无法访问平台管理台。
      </p>
      <div class="o-actions-row">
        <button
          type="button"
          class="o-btn o-btn-pine"
          @click="switchToAdminLogin"
        >
          切换管理员账号登录
        </button>
        <RouterLink
          :to="session.isMerchant ? '/merchant' : '/'"
          class="o-btn"
        >
          {{ session.isMerchant ? "返回商家工作台" : "返回学生端首页" }}
        </RouterLink>
      </div>
    </div>
  </div>

  <div v-else class="o-shell">
    <aside class="o-sidebar">
      <RouterLink to="/ops" class="o-brand">
        <span><ShieldCheck :size="24" /></span>
        <div>烟火地图<small>平台管理台</small></div>
      </RouterLink>

      <div class="o-nav-group-label">GOVERNANCE</div>
      <nav class="o-nav" aria-label="治理模块导航">
        <RouterLink
          v-for="item in visibleNav"
          :key="item.id"
          :to="item.id ? `/ops/${item.id}` : '/ops'"
          :class="{ active: section === item.id }"
        >
          <component :is="item.icon" :size="19" />
          <span>{{ item.label }}</span>
          <b v-if="item.badge" class="o-nav-badge">{{ item.badge }}</b>
        </RouterLink>
      </nav>

      <div class="o-sidebar-footer">
        <div class="o-operator-chip">
          <strong>{{ session.user?.display_name }}</strong>
          <span>{{
            session.user?.is_superuser ? "超级管理员" : "授权运营人员"
          }}</span>
        </div>
        <RouterLink to="/" @click="session.setConsumerPreview(true)">
          <ArrowUpRight :size="16" /> 预览学生端
        </RouterLink>
        <RouterLink to="/ops/users?tab=security">
          <KeyRound :size="16" /> 账号安全与改密
        </RouterLink>
        <RouterLink v-if="session.isMerchant" to="/merchant">
          <Store :size="16" /> 商家工作台
        </RouterLink>
        <button type="button" @click="logout">
          <LogOut :size="16" /> 退出登录
        </button>
      </div>
    </aside>

    <div class="o-workspace">
      <header class="o-topbar">
        <div class="o-topbar-title">
          <ShieldCheck :size="20" style="color: #24372f; flex-shrink: 0" />
          <h1>{{ currentNav.label }}</h1>
        </div>
        <div class="o-topbar-actions">
          <span v-if="session.config?.demo_mode" class="o-env-pill">
            {{
              session.config.services_simulation_enabled
                ? "模拟演练环境"
                : "校园示例环境"
            }}
          </span>
          <button
            type="button"
            class="o-btn"
            :disabled="refreshing"
            aria-label="刷新当前治理模块"
            @click="handleTopRefresh"
          >
            <RefreshCw :size="15" /> 刷新
          </button>
        </div>
      </header>

      <div class="o-mobile-switcher" aria-label="移动端治理快捷栏">
        <RouterLink
          v-for="item in mobileSecondaryNav"
          :key="item.id"
          :to="item.id ? `/ops/${item.id}` : '/ops'"
          :class="{ active: section === item.id }"
        >
          {{ item.short }}
          <span v-if="item.badge">({{ item.badge }})</span>
        </RouterLink>
        <RouterLink to="/ops/users?tab=security">账号安全</RouterLink>
        <RouterLink to="/" @click="session.setConsumerPreview(true)">
          预览学生端 <ArrowUpRight :size="13" />
        </RouterLink>
        <button type="button" @click="logout">退出</button>
      </div>

      <div class="o-content">
        <p v-if="error" class="o-alert" role="alert">{{ error }}</p>

        <div v-if="loading && !overviewData" class="o-card o-empty">
          正在加载平台治理台…
        </div>
        <div v-else-if="!currentNav.allowed" class="o-card o-empty">
          当前运营账号未开通「{{ currentNav.label }}」模块权限，请联系超级管理员授权。
        </div>
        <template v-else>
          <OpsOverview
            v-if="section === ''"
            :data="overviewData"
            :days="days"
            :mode="effectiveMode"
            :loading="refreshing"
            @change-period="changePeriod"
            @change-mode="changeMode"
            @navigate="goSection"
            @refresh="refreshOverview"
          />
          <OpsApplications
            v-else-if="section === 'applications'"
            :refresh-tick="refreshTick"
            @changed="refreshOverview"
          />
          <OpsStalls
            v-else-if="section === 'stalls'"
            :refresh-tick="refreshTick"
            @changed="refreshOverview"
          />
          <OpsAreas
            v-else-if="section === 'areas'"
            :refresh-tick="refreshTick"
            @changed="handleAreasChanged"
          />
          <OpsOrders
            v-else-if="section === 'orders'"
            :refresh-tick="refreshTick"
            :reconciliation-merchants="
              overviewData?.reconciliation_merchants || []
            "
            @changed="refreshOverview"
          />
          <OpsFeedback
            v-else-if="section === 'feedback'"
            :refresh-tick="refreshTick"
            @changed="refreshOverview"
          />
          <OpsReviews
            v-else-if="section === 'reviews'"
            :refresh-tick="refreshTick"
            @changed="refreshOverview"
          />
          <OpsAuditUsers
            v-else-if="section === 'users'"
            :refresh-tick="refreshTick"
            @changed="refreshOverview"
          />
        </template>
      </div>

      <nav class="o-mobile-nav" aria-label="治理台底部导航">
        <RouterLink
          v-for="item in mobilePrimaryNav"
          :key="item.id"
          :to="item.id ? `/ops/${item.id}` : '/ops'"
          :class="{ active: section === item.id && !mobileSheetOpen }"
          @click="mobileSheetOpen = false"
        >
          <component :is="item.icon" :size="19" />
          <span>{{ item.short }}</span>
          <i v-if="item.badge" class="o-mobile-badge">{{ item.badge }}</i>
        </RouterLink>
        <button
          type="button"
          class="o-mobile-more-btn"
          :class="{ active: isSecondarySection || mobileSheetOpen }"
          :aria-expanded="mobileSheetOpen"
          aria-label="全部治理模块"
          @click="mobileSheetOpen = !mobileSheetOpen"
        >
          <LayoutGrid :size="19" />
          <span>更多</span>
          <i v-if="secondaryBadgeTotal" class="o-mobile-badge">{{
            secondaryBadgeTotal
          }}</i>
        </button>
      </nav>

      <div
        v-if="mobileSheetOpen"
        class="o-mobile-sheet-backdrop"
        @click.self="mobileSheetOpen = false"
      >
        <section
          class="o-mobile-sheet"
          role="dialog"
          aria-modal="true"
          aria-label="全部治理模块与快捷操作"
        >
          <header class="o-mobile-sheet-head">
            <div>
              <strong>全部治理模块</strong>
              <small>{{ session.user?.display_name }} · 平台管理台</small>
            </div>
            <button
              type="button"
              class="o-mobile-sheet-close"
              aria-label="关闭模块菜单"
              @click="mobileSheetOpen = false"
            >
              <X :size="18" />
            </button>
          </header>
          <div class="o-mobile-sheet-grid">
            <RouterLink
              v-for="item in visibleNav"
              :key="item.id"
              :to="item.id ? `/ops/${item.id}` : '/ops'"
              :class="{ active: section === item.id }"
              @click="mobileSheetOpen = false"
            >
              <component :is="item.icon" :size="18" />
              <span>{{ item.label }}</span>
              <b v-if="item.badge" class="o-nav-badge">{{ item.badge }}</b>
            </RouterLink>
          </div>
          <div class="o-mobile-sheet-actions">
            <RouterLink
              to="/"
              @click="
                session.setConsumerPreview(true);
                mobileSheetOpen = false;
              "
            >
              <ArrowUpRight :size="16" /> 预览学生端
            </RouterLink>
            <RouterLink
              to="/ops/users?tab=security"
              @click="mobileSheetOpen = false"
            >
              <KeyRound :size="16" /> 账号安全与改密
            </RouterLink>
            <button type="button" @click="logout">
              <LogOut :size="16" /> 退出登录
            </button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
