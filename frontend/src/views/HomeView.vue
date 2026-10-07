<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Search,
  ArrowUpRight,
  MapPin,
  Utensils,
  Coffee,
  Flame,
  Soup,
  Sandwich,
  IceCreamBowl,
  Heart,
  Compass,
  Check,
  ArrowRight,
  X,
  Map,
  RefreshCw,
  History,
  SlidersHorizontal,
  ChevronDown,
} from "lucide-vue-next";
import StallCard from "../components/StallCard.vue";
import DishCard from "../components/DishCard.vue";
import DiscoveryFilters from "../components/DiscoveryFilters.vue";
import DiscoveryEmpty from "../components/DiscoveryEmpty.vue";
import ReorderDialog from "../components/ReorderDialog.vue";
import MealBudget from "../components/MealBudget.vue";
import { productAvailable } from "../lib/availability";
import { useStalls, useMeals } from "../lib/discovery";
import { useBrowseReturn } from "../lib/browseReturn";
import { useSession } from "../stores/session";
import { api, money } from "../lib/api";
import type { Order, Product, Stall } from "../lib/types";
const {
    stalls,
    loading,
    error,
    load,
    follow,
    pendingFollows,
    filters,
    lastSyncedAt,
    next: nextStalls,
    loadMore: loadMoreStalls,
    loadedPages: stallPages,
  } = useStalls(),
  session = useSession(),
  route = useRoute(),
  router = useRouter();
const searching = computed(() => route.path === "/search"),
  followOnly = computed(() => route.query.follow === "1");
const visible = computed(() =>
  followOnly.value ? stalls.value.filter((s) => s.is_followed) : stalls.value,
);
watch(
  followOnly,
  (only) => {
    if (only) filters.status = "";
  },
  { immediate: true },
);
const resultType = computed(() => String(route.query.type || "all"));
const mealBudget = computed(() => {
  const value = Number(route.query.meal_budget);
  return [1000, 1500, 2000].includes(value) ? value : 0;
});
const mealSort = computed(() =>
  route.query.meal_sort === "price" ? "price" : "default",
);
const filterMedia = window.matchMedia("(min-width: 768px)");
const searchFiltersExpanded = ref(filterMedia.matches);
function syncFilterLayout(event: MediaQueryListEvent) {
  searchFiltersExpanded.value = event.matches;
}
onMounted(() => filterMedia.addEventListener("change", syncFilterLayout));
onUnmounted(() => filterMedia.removeEventListener("change", syncFilterLayout));
const searchFilterSummary = computed(() => {
  const labels = [
    ...(filters.category ? [filters.category] : []),
    followOnly.value
      ? "我的关注"
      : filters.status === "open"
        ? "正在出摊"
        : "全部摊位",
  ];
  if (resultType.value !== "dishes") {
    labels.push(
      filters.sort === "rating"
        ? "摊位评分优先"
        : filters.sort === "distance"
          ? "摊位距离最近"
          : "最近确认",
    );
  }
  if (resultType.value !== "stalls") {
    if (mealBudget.value) labels.push(`餐点 ${mealBudget.value / 100} 元以内`);
    if (mealSort.value === "price") labels.push("餐点价格从低到高");
  }
  return labels.join(" · ");
});
const hasSearchFilters = computed(
  () =>
    !!(
      filters.category ||
      filters.status ||
      followOnly.value ||
      filters.sort !== "freshness" ||
      mealBudget.value ||
      mealSort.value !== "default"
    ),
);
function clearSearchFilters() {
  filters.category = "";
  filters.status = "";
  filters.sort = "freshness";
  void router.replace({
    query: {
      ...route.query,
      follow: undefined,
      meal_budget: undefined,
      meal_sort: undefined,
    },
  });
}
function setMealBudget(value: number) {
  void router.replace({
    query: { ...route.query, meal_budget: value || undefined },
  });
}
function setMealSort(value: string) {
  void router.replace({
    query: {
      ...route.query,
      meal_sort: value === "price" ? "price" : undefined,
    },
  });
}
const {
  meals,
  loading: mealsLoading,
  error: mealsError,
  next: nextMeals,
  load: loadMeals,
  loadMore: loadMoreMeals,
  loadedPages: mealPages,
} = useMeals(mealBudget, mealSort);
useBrowseReturn({
  capture: () => ({
    pages: { stalls: stallPages.value, meals: mealPages.value },
  }),
  async restore(snapshot, control) {
    const meal =
      snapshot.region !== ".nearby-section" &&
      snapshot.anchor.includes("/products/");
    const pendingRead = meal ? mealsLoading : loading;
    const problem = meal ? mealsError : error;
    const next = meal ? nextMeals : nextStalls;
    const pages = meal ? mealPages : stallPages;
    const more = meal ? loadMoreMeals : loadMoreStalls;
    const limit = snapshot.view.pages?.[meal ? "meals" : "stalls"] || 1;
    const match = snapshot.anchor.match(
      meal ? /\/products\/(\d+)$/ : /\/stalls\/(\d+)/,
    );
    const id = Number(match?.[1]);
    const contains = () =>
      meal
        ? meals.value.some((item) => item.product.id === id)
        : visible.value.some((item) => item.id === id);
    if (
      !(await control.wait(
        () => !pendingRead.value && (pages.value > 0 || !!problem.value),
      ))
    )
      return false;
    while (control.active()) {
      if (!(await control.wait(() => !pendingRead.value))) return false;
      if (problem.value || contains() || !next.value || pages.value >= limit)
        break;
      await more();
    }
    return control.active() && !problem.value && contains();
  },
});
const availableMeals = meals;
const featuredMeals = computed(() => meals.value.slice(0, 4));
const compactMealEmpty = computed(
  () =>
    searching.value &&
    resultType.value === "all" &&
    !mealsLoading.value &&
    !mealsError.value &&
    !meals.value.length,
);
const compactStallEmpty = computed(
  () =>
    searching.value &&
    resultType.value === "all" &&
    !loading.value &&
    !error.value &&
    !visible.value.length &&
    meals.value.length > 0,
);
const recentOrders = ref<Order[]>([]);
const recentError = ref("");
const recentLoading = ref(false);
const reorderOrder = ref<Order | null>(null);
let recentSequence = 0;
let recentController: AbortController | undefined;
async function loadRecent() {
  recentController?.abort();
  const sequence = ++recentSequence,
    userId = session.user?.id;
  if (!userId || session.isMerchant) {
    recentOrders.value = [];
    recentLoading.value = false;
    return;
  }
  const controller = new AbortController();
  recentController = controller;
  recentLoading.value = true;
  try {
    const orders = await api<Order[]>("/orders/recent-completed", {
      signal: controller.signal,
    });
    if (sequence === recentSequence && userId === session.user?.id) {
      recentOrders.value = orders
        .filter((order) => order.status === "completed")
        .slice(0, 3);
      recentError.value = "";
    }
  } catch (error) {
    if (sequence === recentSequence && userId === session.user?.id)
      recentError.value = (error as Error).message;
  } finally {
    if (sequence === recentSequence) recentLoading.value = false;
  }
}
function openReorder(order: Order) {
  reorderOrder.value = order;
  api("/events", {
    method: "POST",
    body: {
      type: "reorder",
      stall_id: order.stall_id,
      metadata: { source: "recent_order" },
    },
  }).catch(() => {});
}
watch(
  () => session.user?.id,
  () => {
    recentOrders.value = [];
    recentError.value = "";
    reorderOrder.value = null;
    void loadRecent();
  },
  { immediate: true },
);
function resumeRecent() {
  if (!document.hidden && !recentLoading.value) void loadRecent();
}
onMounted(() => window.addEventListener("focus", resumeRecent));
onUnmounted(() => {
  recentSequence++;
  recentController?.abort();
  window.removeEventListener("focus", resumeRecent);
});
const categories = [
  { name: "全部", value: "", icon: Utensils, image: "" },
  {
    name: "街头小吃",
    value: "小吃",
    icon: Sandwich,
    image: "/images/food-jianbing.jpg",
  },
  {
    name: "烧烤炸串",
    value: "烧烤",
    icon: Flame,
    image: "/images/food-chicken-wings.jpg",
  },
  {
    name: "喝点什么",
    value: "饮品",
    icon: Coffee,
    image: "/images/food-lemon-tea.jpg",
  },
  {
    name: "饱饱一餐",
    value: "正餐",
    icon: Soup,
    image: "/images/food-pork-rice.jpg",
  },
  {
    name: "甜蜜收尾",
    value: "甜品",
    icon: IceCreamBowl,
    image: "/images/food-ciba.jpg",
  },
];
function search() {
  router.push({
    path: "/search",
    query: { ...route.query, q: filters.q || undefined },
  });
}
watch(
  () => route.query.q,
  (query) => {
    if (searching.value) filters.q = typeof query === "string" ? query : "";
  },
  { immediate: true },
);
function showResults(type: string) {
  router.replace({
    path: "/search",
    query: { ...route.query, q: filters.q || undefined, type },
  });
}
onMounted(() =>
  api("/events", { method: "POST", body: { type: "browse" } }).catch(() => {}),
);
</script>
<template>
  <div class="page home-page" :class="{ 'search-page': searching }">
    <div class="discovery-toolbar">
      <div class="welcome-note">
        <span class="little-sun">✳</span
        ><span>{{
          searching ? "找一份心动的好味道" : "下课了，去吃点好的。"
        }}</span>
      </div>
      <form class="search-box" @submit.prevent="search">
        <Search :size="18" /><input
          v-model="filters.q"
          placeholder="搜搜摊位、美食，或今天想吃的…"
          aria-label="搜索摊位或美食"
        /><button
          v-if="filters.q"
          type="button"
          @click="filters.q = ''"
          aria-label="清空搜索"
        >
          <X :size="15" /></button
        ><button type="submit" class="search-submit" aria-label="搜索">
          <ArrowRight :size="17" />
        </button>
      </form>
    </div>
    <section v-if="!searching && !followOnly" class="home-hero">
      <div class="hero-photo">
        <img
          src="/images/night-market.jpg"
          alt="灯火温暖的夜市美食摊"
          fetchpriority="high"
          width="1100"
          height="700"
        />
      </div>
      <div class="hero-copy">
        <div class="hero-kicker"><span></span>先确认出摊，再出发</div>
        <h1>今天的好味，<span>就在附近。</span></h1>
        <p>看看摊主刚确认的位置，少跑空一趟。</p>
        <RouterLink to="/map" class="hero-link"
          >地图找摊 <ArrowUpRight :size="17"
        /></RouterLink>
      </div>
      <div class="hero-stamp">
        <MapPin :size="19" /><span
          >离生活近一点<br /><b>离好味道也近一点</b></span
        >
      </div>
      <div class="hero-index"><span>01</span> / 校园烟火记</div>
    </section>
    <nav v-if="!searching" class="categories" aria-label="美食品类">
      <button
        v-for="c in categories"
        :key="c.name"
        :class="{ selected: filters.category === c.value }"
        :aria-pressed="filters.category === c.value"
        @click="filters.category = c.value"
      >
        <span :class="['category-icon', { 'category-photo': c.image }]"
          ><img
            v-if="c.image"
            :src="c.image"
            alt=""
            width="80"
            height="80"
            loading="lazy" /><component
            v-else
            :is="c.icon"
            :size="25"
            :stroke-width="1.5" /></span
        ><span>{{ c.name }}</span
        ><i v-if="filters.category === c.value"></i>
      </button>
    </nav>
    <div v-if="searching" class="result-switch" aria-label="搜索结果类型">
      <button
        :class="{ active: resultType === 'all' }"
        @click="showResults('all')"
      >
        全部
      </button>
      <button
        :class="{ active: resultType === 'stalls' }"
        @click="showResults('stalls')"
      >
        摊位 <span>{{ visible.length }}</span>
      </button>
      <button
        :class="{ active: resultType === 'dishes' }"
        @click="showResults('dishes')"
      >
        餐点 <span>{{ meals.length }}</span>
      </button>
    </div>
    <section v-if="searching" class="search-filters" aria-label="搜索筛选">
      <div class="search-filter-heading">
        <p class="search-filter-summary" aria-label="当前筛选条件">
          {{ searchFilterSummary }}
        </p>
        <button
          type="button"
          class="search-filter-toggle"
          :aria-expanded="searchFiltersExpanded"
          aria-controls="search-filter-controls"
          @click="searchFiltersExpanded = !searchFiltersExpanded"
        >
          <SlidersHorizontal :size="16" />{{
            searchFiltersExpanded ? "收起筛选" : "筛选"
          }}
          <ChevronDown
            :size="15"
            :class="{ expanded: searchFiltersExpanded }"
          />
        </button>
        <button
          v-if="hasSearchFilters"
          type="button"
          class="search-filter-clear"
          @click="clearSearchFilters"
        >
          清除筛选
        </button>
      </div>
      <div
        v-show="searchFiltersExpanded"
        id="search-filter-controls"
        class="search-filter-controls"
      >
        <nav class="search-categories" aria-label="美食品类">
          <button
            v-for="c in categories"
            :key="c.name"
            type="button"
            :class="{ selected: filters.category === c.value }"
            :aria-pressed="filters.category === c.value"
            @click="filters.category = c.value"
          >
            {{ c.name }}
          </button>
        </nav>
        <DiscoveryFilters :show-sort="resultType !== 'dishes'" />
        <MealBudget
          v-if="resultType !== 'stalls'"
          :budget="mealBudget"
          :sort="mealSort"
          @update:budget="setMealBudget"
          @update:sort="setMealSort"
        />
      </div>
    </section>
    <section
      v-if="searching && resultType !== 'stalls'"
      class="meal-search-section"
      :class="{ 'has-compact-empty': compactMealEmpty }"
      aria-label="餐点搜索结果"
    >
      <div v-if="!compactMealEmpty" class="section-heading">
        <div>
          <p class="eyebrow">FIND YOUR NEXT BITE</p>
          <h2>
            想吃的，在这里 <span class="count">{{ meals.length }}</span>
          </h2>
        </div>
        <span class="section-caption">点开餐点，看看详细介绍</span>
      </div>
      <div v-if="mealsLoading" class="skeleton-grid">
        <div v-for="n in 4" :key="n" class="skeleton skeleton-card"></div>
      </div>
      <div v-else-if="mealsError" class="empty-state card">
        <p>{{ mealsError }}</p>
        <button class="btn btn-secondary" @click="loadMeals">
          重新加载餐点
        </button>
      </div>
      <div v-else-if="meals.length" class="dish-grid">
        <DishCard v-for="item in meals" :key="item.product.id" v-bind="item" />
      </div>
      <p v-else-if="compactMealEmpty" class="search-empty-inline" role="status">
        {{
          mealBudget
            ? "当前餐费预算内没有匹配餐点。"
            : "暂未找到匹配的可售餐点。"
        }}
        <button v-if="mealBudget" type="button" @click="setMealBudget(0)">
          清除餐费预算
        </button>
        <span v-else>可调整筛选，或继续查看摊位。</span>
      </p>
      <div v-else class="empty-state card meal-empty">
        <Utensils :size="24" />
        <h3>还没有找到这道餐点</h3>
        <p>
          {{
            mealBudget
              ? "当前预算内没有匹配的可售餐点，试试调整预算。"
              : "试试“烤冷面”“奶茶”或其他喜欢的口味。"
          }}
        </p>
        <button
          v-if="mealBudget"
          type="button"
          class="btn btn-secondary"
          @click="setMealBudget(0)"
        >
          清除餐费预算
        </button>
      </div>
      <button
        v-if="nextMeals"
        class="btn btn-secondary"
        :disabled="mealsLoading"
        @click="loadMoreMeals"
      >
        {{ mealsLoading ? "正在加载" : "加载更多餐点" }}
      </button>
    </section>
    <section
      v-if="!searching || resultType !== 'dishes'"
      class="nearby-section"
    >
      <div v-if="!compactStallEmpty" class="section-heading">
        <div>
          <p class="eyebrow">
            {{
              followOnly
                ? "YOUR LITTLE FAVORITES"
                : "GOOD FOOD, AROUND THE CORNER"
            }}
          </p>
          <h2>
            {{
              followOnly
                ? "关注的好味道"
                : searching
                  ? "为你找到的好味道"
                  : filters.status === "open"
                    ? "现在，去这里吃"
                    : "附近的烟火气"
            }}<span class="count" v-if="!loading">{{ visible.length }}</span>
          </h2>
        </div>
        <RouterLink v-if="!followOnly" to="/map" class="map-shortcut"
          ><Map :size="14" />在地图上看<ArrowUpRight :size="13" /></RouterLink
        ><RouterLink v-else to="/" class="map-shortcut"
          >发现更多<ArrowUpRight :size="13"
        /></RouterLink>
      </div>
      <DiscoveryFilters v-if="!searching" />
      <div v-if="error && stalls.length" class="discovery-stale" role="status">
        <span>暂未同步最新状态，下面是上次加载的摊位。出发前请刷新确认。</span>
        <button @click="load" :disabled="loading">
          <RefreshCw :size="15" />{{ loading ? "正在刷新" : "刷新摊位" }}
        </button>
      </div>
      <div v-if="followOnly && !session.user" class="empty-state card">
        <Heart :size="27" />
        <h3>登录后，看看你关注的小摊</h3>
        <p>关注会跟随账号保存，下次不用重新找。</p>
        <RouterLink
          :to="{ path: '/login', query: { returnTo: route.fullPath } }"
          class="btn btn-primary"
          >登录查看关注</RouterLink
        >
      </div>
      <div v-else-if="loading && !stalls.length" class="skeleton-grid">
        <div v-for="n in 6" :key="n" class="skeleton skeleton-card"></div>
      </div>
      <div v-else-if="error && !stalls.length" class="empty-state card">
        <Compass :size="32" />
        <h3>附近的好味道暂时走丢了</h3>
        <p>{{ error }}</p>
        <button class="btn btn-secondary" @click="load">重新加载</button>
      </div>
      <p
        v-else-if="compactStallEmpty"
        class="search-empty-inline"
        role="status"
      >
        当前条件下没有匹配摊位；可调整筛选，或查看上方餐点。
      </p>
      <DiscoveryEmpty
        v-else-if="!visible.length"
        :area="filters.area"
        :category="filters.category"
        :query="filters.q"
        :status="filters.status"
        :following="followOnly"
        :synced-at="lastSyncedAt"
        @clear="
          filters.q = '';
          filters.category = '';
          filters.status = '';
          router.push('/');
        "
        @all-areas="
          filters.area = 0;
          filters.q = '';
          filters.category = '';
          filters.status = '';
          router.push('/');
        "
      />
      <div v-else class="food-grid">
        <StallCard
          v-for="stall in visible"
          :key="stall.id"
          :stall="stall"
          @follow="follow"
          :follow-busy="pendingFollows.has(stall.id)"
        />
      </div>
      <button
        v-if="nextStalls"
        class="btn btn-secondary"
        :disabled="loading"
        @click="loadMoreStalls"
      >
        {{ loading ? "正在加载" : "加载更多摊位" }}
      </button>
    </section>
    <section
      v-if="!searching && !followOnly && (recentOrders.length || recentError)"
      class="recent-meals"
      aria-label="最近吃过"
    >
      <div class="section-heading">
        <div>
          <p class="eyebrow">熟悉的一口</p>
          <h2>上次吃过，还想再来</h2>
        </div>
        <RouterLink to="/orders" class="map-shortcut"
          >全部订单<ArrowUpRight :size="15"
        /></RouterLink>
      </div>
      <div v-if="recentError" class="discovery-stale" role="status">
        <span>最近吃过的订单暂未同步。</span
        ><button @click="loadRecent" :disabled="recentLoading">重新加载</button>
      </div>
      <div class="recent-grid">
        <article
          v-for="order in recentOrders"
          :key="order.id"
          class="recent-order-card"
        >
          <img
            v-if="order.items[0]?.image || order.stall_image"
            :src="order.items[0]?.image || order.stall_image"
            :alt="order.items[0]?.name || order.stall_name"
            width="72"
            height="72"
            loading="lazy"
          />
          <div v-else class="recent-placeholder"><History :size="24" /></div>
          <div class="recent-order-copy">
            <h3>{{ order.stall_name }}</h3>
            <p>{{ order.items.map((item) => item.name).join("、") }}</p>
            <small
              >{{ order.mode === "simulation" ? "模拟订单 · " : "" }}上次 ¥{{
                money(order.total_cents)
              }}</small
            >
          </div>
          <button
            @click="openReorder(order)"
            :aria-label="`再来一单，${order.stall_name}`"
          >
            再来一单<ArrowUpRight :size="14" />
          </button>
        </article>
      </div>
      <p class="recent-note">
        先看看今天的菜单、价格和库存，确认后才加入餐袋。
      </p>
    </section>
    <section
      v-if="
        !searching &&
        !followOnly &&
        !mealsError &&
        (availableMeals.length || mealBudget)
      "
      class="meal-inspiration"
      aria-label="餐点灵感"
    >
      <div class="section-heading">
        <div>
          <p class="eyebrow">A LITTLE SOMETHING DELICIOUS</p>
          <h2>今天，想吃哪一口？</h2>
        </div>
        <RouterLink
          :to="{ path: '/search', query: { ...route.query, type: 'dishes' } }"
          class="map-shortcut"
          >逛逛餐点 <ArrowUpRight :size="15"
        /></RouterLink>
      </div>
      <MealBudget
        :budget="mealBudget"
        :sort="mealSort"
        @update:budget="setMealBudget"
        @update:sort="setMealSort"
      />
      <div v-if="!featuredMeals.length" class="empty-state card">
        <Utensils :size="28" />
        <h3>当前预算内暂时没有可售餐点</h3>
        <p>可以调整预算；摊位列表仍然保留。</p>
        <button
          type="button"
          class="btn btn-secondary"
          @click="setMealBudget(0)"
        >
          清除餐费预算
        </button>
      </div>
      <div v-else class="dish-grid">
        <DishCard
          v-for="item in featuredMeals"
          :key="item.product.id"
          v-bind="item"
        />
      </div>
    </section>
    <p v-if="session.config?.demo_mode" class="image-credit-note">
      美食与夜市照片为示例配图，不代表虚构摊位实拍。<a
        href="/images/ATTRIBUTION.md"
        target="_blank"
        rel="noopener"
        >图片来源与许可 ↗</a
      >
    </p>
    <ReorderDialog
      v-if="reorderOrder"
      :key="reorderOrder.id"
      :order="reorderOrder"
      @close="reorderOrder = null"
    />
  </div>
</template>
<style scoped>
.search-filters {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fffaf3;
  margin-bottom: 22px;
}
.search-filter-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0 12px;
  padding: 6px 14px;
}
.search-filter-summary {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #735b43;
  overflow-wrap: anywhere;
}
.search-filter-toggle,
.search-filter-clear {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex-shrink: 0;
  font-size: 13px;
  color: #9a4923;
}
.search-filter-toggle .expanded {
  transform: rotate(180deg);
}
.search-filter-clear {
  color: #756450;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.search-filter-controls {
  padding: 4px 14px 14px;
  border-top: 1px solid var(--line);
}
.search-categories {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  padding: 10px 0;
}
.search-categories button {
  min-height: 44px;
  padding: 8px 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  font-size: 13px;
  color: #735b43;
}
.search-categories .selected {
  color: #a74714;
  background: #fff0de;
  border-color: #ecc6a4;
}
.search-filter-controls :deep(.discovery-filters) {
  margin-bottom: 0;
}
.search-filter-controls :deep(.meal-budget) {
  margin: 12px 0 0;
}
.search-empty-inline {
  margin: 0 0 18px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #f5eee4;
  color: #73604c;
  font-size: 13px;
  line-height: 1.7;
}
.search-empty-inline button {
  min-height: 44px;
  color: #9a4923;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.meal-search-section.has-compact-empty {
  margin-bottom: 0;
}
.meal-empty {
  min-height: 0;
  padding: 22px;
  gap: 8px;
}
.discovery-stale {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid #e8c8a0;
  border-radius: 12px;
  background: #fff4df;
  color: #79502b;
  font-size: 13px;
  line-height: 1.7;
}
.discovery-stale button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 44px;
  flex-shrink: 0;
  font-weight: 600;
}
.recent-meals {
  margin-top: 32px;
  padding-top: 26px;
  border-top: 1px solid var(--line);
}
.recent-meals .eyebrow {
  color: #8c6c45;
  font-size: 11px;
}
.recent-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.recent-order-card {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 12px;
  padding: 16px;
  background: #fffdf9;
  border: 1px solid var(--line);
  border-radius: 16px;
}
.recent-order-card > img,
.recent-placeholder {
  width: 64px;
  height: 64px;
  object-fit: cover;
  border-radius: 12px;
  background: #f6eadb;
}
.recent-placeholder {
  display: grid;
  place-items: center;
  color: #9c7349;
}
.recent-order-copy h3 {
  font-size: 15px;
  margin: 0 0 5px;
}
.recent-order-copy p {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 12px;
  color: #786956;
  margin: 0 0 5px;
}
.recent-order-copy small {
  color: #876b4c;
  font-size: 12px;
}
.recent-order-card button {
  grid-column: 1/-1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 44px;
  border: 1px solid #ead8c2;
  border-radius: 10px;
  color: #9d5429;
  font-size: 13px;
}
.recent-note {
  font-size: 12px;
  color: #786956;
  margin-top: 12px;
  line-height: 1.7;
}
.home-page {
  padding-top: 24px;
}
.dish-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px;
}
.meal-inspiration {
  margin-top: 44px;
  padding-top: 30px;
  border-top: 1px solid var(--line);
}
.meal-search-section {
  margin-bottom: 36px;
}
.meal-inspiration .section-heading,
.meal-search-section .section-heading {
  margin: 0 0 22px;
}
.meal-inspiration .eyebrow,
.meal-search-section .eyebrow {
  font-size: 10px;
  margin-bottom: 8px;
  letter-spacing: 1.4px;
}
.section-caption {
  color: #81705c;
  font-size: 12px;
}
.result-switch {
  display: flex;
  gap: 12px;
  margin: 0 0 28px;
  border-bottom: 1px solid var(--line);
}
.result-switch button {
  min-height: 46px;
  padding: 10px 14px;
  color: #766958;
  border-bottom: 2px solid transparent;
}
.result-switch button.active {
  color: #a3481d;
  border-bottom-color: #ce632d;
  font-weight: 600;
}
.result-switch span {
  margin-left: 4px;
  font-size: 11px;
  color: #8d7a61;
}
.discovery-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 22px;
}
.welcome-note {
  display: flex;
  align-items: center;
  gap: 9px;
  color: #8d7459;
  font-size: 14px;
  letter-spacing: 0.6px;
}
.little-sun {
  color: #d68c47;
  font-size: 23px;
}
.discovery-toolbar .search-box {
  width: 47%;
  max-width: 520px;
}
.search-submit {
  color: #b96a39;
}
.home-hero {
  position: relative;
  height: 180px;
  border-radius: 22px;
  overflow: hidden;
  background: #f8ead3;
}
.hero-photo {
  position: absolute;
  inset: 0 0 0 34%;
  overflow: hidden;
}
.hero-photo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 50% 56%;
}
.hero-photo:after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    #f8ead3 0%,
    #f8ead3aa 12%,
    transparent 36%,
    #2c160b20 100%
  );
}
.hero-copy {
  position: relative;
  z-index: 2;
  padding: 21px 32px;
  width: 65%;
}
.hero-kicker {
  font-size: 10px;
  color: #a17443;
  letter-spacing: 2px;
  display: flex;
  align-items: center;
  gap: 7px;
}
.hero-kicker > span {
  width: 17px;
  height: 1px;
  background: #ae7b46;
}
.hero-copy h1 {
  font-family: "Noto Serif SC", serif;
  font-size: 31px;
  font-weight: 800;
  letter-spacing: 1px;
  line-height: 1.5;
  margin-top: 10px;
  color: #43301e;
}
.hero-copy h1 span {
  color: #ae521e;
}
.hero-copy p {
  font-size: 12px;
  line-height: 1.9;
  color: #9b7c59;
  margin-top: 8px;
  letter-spacing: 0.8px;
}
.hero-link {
  display: inline-flex;
  align-items: center;
  gap: 18px;
  margin-top: 3px;
  color: #9c4b1d;
  font-size: 12px;
  font-weight: 600;
  border-bottom: 1px solid #cd9b6c;
  padding-bottom: 5px;
  min-height: 44px;
}
.hero-stamp {
  position: absolute;
  right: 25px;
  bottom: 31px;
  display: flex;
  gap: 8px;
  align-items: center;
  border: 1px solid #ffffff40;
  border-radius: 12px;
  background: #fff8eeec;
  backdrop-filter: blur(10px);
  padding: 12px 15px;
  color: #9a693f;
  box-shadow: 0 4px 18px #28100013;
  transform: rotate(-3deg);
}
.hero-stamp span {
  font-size: 10px;
  line-height: 1.9;
  letter-spacing: 1px;
}
.hero-stamp b {
  font-size: 12px;
  font-weight: 600;
  color: #7a4e2e;
}
.hero-index {
  position: absolute;
  right: 24px;
  top: 22px;
  font-size: 10px;
  color: #fff9e6;
  letter-spacing: 1px;
  text-shadow: 0 1px 3px #0005;
}
.hero-index span {
  font-size: 13px;
}
.categories {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  padding: 22px 0 18px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 30px;
}
.categories button {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #817665;
  position: relative;
  min-height: 75px;
}
.category-icon {
  width: 58px;
  height: 58px;
  display: grid;
  place-items: center;
  color: #ac8c67;
  border-radius: 50%;
  transition:
    background 0.2s,
    box-shadow 0.2s;
}
.category-photo {
  padding: 3px;
  border: 1px solid #e6d9c7;
  background: #fff;
}
.category-photo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}
.categories button.selected .category-photo {
  box-shadow: 0 0 0 2px #e0a679;
  border-color: #fff;
}
.categories button.selected {
  color: #a74e19;
  font-weight: 500;
}
.categories button.selected .category-icon {
  background: #fce9d3;
  color: #b9692c;
}
.categories button:hover .category-icon {
  background: #f6eee4;
}
.categories i {
  width: 4px;
  height: 4px;
  background: #d37536;
  border-radius: 50%;
  position: absolute;
  bottom: -12px;
}
.nearby-section .section-heading {
  margin: 0 0 19px;
}
.nearby-section .eyebrow {
  font-size: 9px;
  letter-spacing: 1.7px;
  margin-bottom: 7px;
}
.count {
  font-size: 12px;
  border: 1px solid #e7dccc;
  color: #a28665;
  border-radius: 6px;
  padding: 1px 6px;
  font-weight: 400;
  margin-left: 1px;
}
.map-shortcut {
  align-self: flex-end;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: #a77b50;
  padding-bottom: 4px;
}
.image-credit-note {
  font-size: 10px;
  color: #a89a89;
  text-align: center;
  margin-top: 20px;
}
.image-credit-note a {
  text-decoration: underline;
  text-underline-offset: 3px;
}
@media (max-width: 1023px) {
  .dish-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .hero-copy h1 {
    font-size: 36px;
  }
  .hero-copy {
    width: 62%;
  }
  .hero-photo {
    left: 35%;
  }
  .hero-stamp {
    right: 16px;
    bottom: 23px;
  }
}
@media (max-width: 767px) {
  .search-page .result-switch {
    margin-bottom: 12px;
  }
  .search-page .meal-search-section .eyebrow {
    display: none;
  }
  .search-page .meal-search-section .section-heading {
    margin-bottom: 12px;
  }
  .search-filters {
    margin-bottom: 16px;
  }
  .search-filter-heading {
    gap: 0 8px;
    padding: 5px 10px;
  }
  .search-filter-clear {
    min-width: 52px;
  }
  .search-filter-controls {
    padding: 4px 10px 10px;
  }
  .search-filter-controls :deep(.discovery-filters) {
    flex-wrap: wrap;
    gap: 0 8px;
  }
  .search-filter-controls :deep(.sort-control) {
    margin-left: auto;
  }
  .search-filter-controls :deep(.sort-control select) {
    max-width: none;
  }
  .search-filter-controls :deep(.filter-chips) {
    flex-wrap: wrap;
  }
  .search-categories {
    gap: 4px;
  }
  .search-categories button {
    padding-inline: 9px;
  }
  .discovery-stale {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .recent-grid {
    grid-template-columns: 1fr;
  }
  .recent-meals h2 {
    font-size: 21px;
  }
  .recent-order-card {
    grid-template-columns: 58px minmax(0, 1fr) auto;
    align-items: center;
    padding: 12px;
    gap: 10px;
  }
  .recent-order-card > img,
  .recent-placeholder {
    width: 58px;
    height: 58px;
  }
  .recent-order-card button {
    grid-column: auto;
    padding: 0 9px;
    font-size: 12px;
  }
  .recent-order-card button svg {
    display: none;
  }
  .dish-grid {
    gap: 12px;
  }
  .meal-inspiration {
    margin-top: 28px;
    padding-top: 23px;
  }
  .meal-inspiration h2,
  .meal-search-section h2 {
    font-size: 20px;
  }
  .section-caption {
    display: none;
  }
  .result-switch {
    gap: 10px;
    margin-bottom: 24px;
  }
  .home-page {
    padding-top: 15px;
  }
  .discovery-toolbar {
    display: block;
    margin-bottom: 15px;
  }
  .welcome-note {
    display: none;
  }
  .discovery-toolbar .search-box {
    width: 100%;
    max-width: none;
    min-height: 46px;
    border-radius: 13px;
  }
  .search-box input {
    min-height: 44px;
  }
  .home-hero {
    height: 112px;
    border-radius: 17px;
  }
  .hero-copy {
    padding: 12px 16px;
    width: 90%;
  }
  .hero-kicker {
    font-size: 11px;
    letter-spacing: 1.2px;
  }
  .hero-kicker > span {
    width: 12px;
  }
  .hero-copy h1 {
    font-size: 23px;
    line-height: 1.5;
    margin-top: 3px;
    letter-spacing: 0;
  }
  .hero-copy h1 span {
    display: inline;
  }
  .hero-copy p {
    display: none;
  }
  .hero-link {
    font-size: 12px;
    gap: 8px;
    margin-top: 0;
    padding-bottom: 4px;
  }
  .hero-photo {
    left: 24%;
  }
  .hero-photo img {
    object-position: 60% 50%;
  }
  .hero-photo:after {
    background: linear-gradient(
      90deg,
      #f8ead3,
      #f8ead3b0 16%,
      #f8ead320 64%,
      #30180811
    );
  }
  .hero-stamp {
    display: none;
    right: 12px;
    bottom: 17px;
    padding: 6px 9px;
    border-radius: 8px;
    gap: 5px;
  }
  .hero-stamp svg {
    width: 14px;
  }
  .hero-stamp span {
    font-size: 7px;
    letter-spacing: 0.5px;
  }
  .hero-stamp b {
    font-size: 9px;
  }
  .hero-index {
    display: none;
    right: 12px;
    top: 12px;
    font-size: 7px;
  }
  .hero-index span {
    font-size: 10px;
  }
  .categories {
    padding: 13px 0 12px;
    margin-bottom: 15px;
    gap: 3px;
  }
  .categories button {
    font-size: 12px;
    gap: 5px;
    min-height: 62px;
    padding: 0;
  }
  .category-icon {
    width: 42px;
    height: 42px;
    border-radius: 50%;
  }
  .category-icon svg {
    width: 21px;
    height: 21px;
  }
  .categories i {
    bottom: -9px;
    width: 3px;
    height: 3px;
  }
  .nearby-section .eyebrow {
    display: none;
    font-size: 12px;
    letter-spacing: 1.2px;
    margin-bottom: 4px;
  }
  .nearby-section .section-heading {
    margin-bottom: 7px;
  }
  .nearby-section h2 {
    font-size: 21px;
  }
  .count {
    font-size: 12px;
  }
  .map-shortcut {
    font-size: 12px;
    gap: 3px;
  }
  .image-credit-note {
    font-size: 12px;
    text-align: left;
  }
}
</style>
