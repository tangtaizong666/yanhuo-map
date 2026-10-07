<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { useRoute } from "vue-router";
import {
  ArrowLeft,
  Heart,
  Star,
  MapPin,
  Clock3,
  ShieldCheck,
  Navigation,
  Plus,
  Minus,
  ShoppingBag,
  ChevronRight,
  Store,
  Phone,
  AlertCircle,
  Utensils,
  ArrowUpRight,
  CreditCard,
} from "lucide-vue-next";
import {
  api,
  money,
  confirmedText,
  statusText,
  routeUrl,
  formatTime,
  formatClock,
} from "../lib/api";
import { notify } from "../lib/notify";
import { useFollowState } from "../lib/discovery";
import { useSession } from "../stores/session";
import { useCart } from "../stores/cart";
import type { Stall, Product } from "../lib/types";
import StallVisitInfo from "../components/StallVisitInfo.vue";
import StallShare from "../components/StallShare.vue";
import ReceivingNotice from "../components/ReceivingNotice.vue";
import {
  productAvailable,
  productUnavailableReason,
} from "../lib/availability";
const route = useRoute(),
  session = useSession(),
  cart = useCart(),
  stall = ref<Stall | null>(null),
  loading = ref(true),
  error = ref(""),
  tab = ref("menu");
const id = computed(() => Number(route.params.id));
const mobileQuery = window.matchMedia('(max-width: 767px)');
const mobileLayout = ref(mobileQuery.matches);
const updateLayout = () => { mobileLayout.value = mobileQuery.matches; };
onMounted(() => mobileQuery.addEventListener('change', updateLayout));
onUnmounted(() => mobileQuery.removeEventListener('change', updateLayout));
const followState = useFollowState((changedId, followed) => {
  if (stall.value?.id === changedId && id.value === changedId)
    stall.value.is_followed = followed;
});
const { pendingFollows } = followState;
let timer: ReturnType<typeof setInterval>,
  seq = 0;
async function load() {
  const token = ++seq;
  const read = followState.snapshot();
  try {
    const data = await api<Stall>(`/stalls/${id.value}`);
    if (token === seq && followState.isCurrent(read)) {
      stall.value = followState.reconcile(data, read);
      error.value = "";
    }
  } catch (e) {
    if (token === seq && followState.isCurrent(read))
      error.value = (e as Error).message;
  } finally {
    if (token === seq && followState.isCurrent(read)) loading.value = false;
  }
}
function quantity(p: Product) {
  return cart.items(id.value).find((i) => i.product.id === p.id)?.quantity || 0;
}
function change(p: Product, d: number) {
  // Removing items is always allowed; adding waits for a fresh, orderable stall state.
  if (d > 0 && (!stall.value?.can_order || error.value || !productAvailable(p))) return;
  if (d > 0 && cart.remaining(id.value) <= 0) {
    notify("每个摊位一单合计最多 10 份，请调整餐袋。", "info");
    return;
  }
  cart.setQuantity(id.value, p, quantity(p) + d);
}
async function follow() {
  if (stall.value?.id === id.value) await followState.follow(stall.value);
}
const availability = computed(
  () =>
    stall.value?.order_unavailable_reason ||
    (!stall.value?.transaction_enabled
      ? "该摊位目前仅支持线下到访。"
      : stall.value?.status === "stale"
        ? "商家需要重新确认位置，暂时无法点单。"
        : stall.value?.status === "closed"
          ? "今天已经收摊，期待下次相见。"
          : stall.value?.status === "paused"
            ? "摊主正在忙，暂时停止接单。"
            : stall.value?.accepting_orders === false
              ? "摊位仍在出摊，线上接单暂停，可到摊选购。"
              : ""),
);
function start() {
  loading.value = true;
  load();
  api("/events", {
    method: "POST",
    body: { type: "stall_view", stall_id: id.value },
  }).catch(() => {});
}
watch([id, () => session.user?.id], () => {
  stall.value = null;
  start();
});
let resumeTimer: ReturnType<typeof setTimeout> | undefined;
function resume() {
  if (document.hidden) return;
  clearTimeout(resumeTimer);
  resumeTimer = setTimeout(load, 50);
}
onMounted(() => {
  start();
  timer = setInterval(() => {
    if (!document.hidden) load();
  }, 60000);
  window.addEventListener("focus", resume);
  document.addEventListener("visibilitychange", resume);
});
onUnmounted(() => {
  seq++;
  clearInterval(timer);
  clearTimeout(resumeTimer);
  window.removeEventListener("focus", resume);
  document.removeEventListener("visibilitychange", resume);
});
</script>
<template>
  <div class="page stall-page">
    <RouterLink class="back-link" to="/"
      ><ArrowLeft :size="16" />返回附近摊位</RouterLink
    >
    <div v-if="loading" class="skeleton skeleton-banner"></div>
    <div v-else-if="error && !stall" class="empty-state card">
      <AlertCircle :size="30" />
      <h2>暂时找不到这个摊位</h2>
      <p>{{ error }}</p>
      <button class="btn btn-secondary" @click="load">重新加载</button>
    </div>
    <template v-else-if="stall"
      ><div v-if="error" class="error-message">
        暂未同步最新状态，当前展示上次加载的内容。{{ error }}
        <button class="btn btn-secondary" @click="load">刷新摊位</button>
      </div>
      <section class="stall-hero">
        <img :src="stall.image" :alt="stall.name" width="1200" height="500" />
        <div class="stall-hero-shade"></div>
        <div class="stall-hero-caption">
          <span>{{ stall.category }} / {{ stall.area_name }}</span>
          <h1>{{ stall.name }}</h1>
          <p>{{ stall.description }}</p>
        </div>
        <span v-if="session.config?.demo_mode" class="demo-image-label"
          >美食示例配图</span
        ><button
          :class="['hero-follow', { followed: stall.is_followed }]"
          :disabled="pendingFollows.has(stall.id)"
          :aria-busy="pendingFollows.has(stall.id)"
          :aria-pressed="stall.is_followed"
          @click="follow"
        >
          <Heart
            :size="17"
            :fill="stall.is_followed ? 'currentColor' : 'none'"
          />{{ stall.is_followed ? "已关注" : "关注小摊" }}
        </button>
      </section>
      <div class="stall-layout" :class="{ 'visit-only': !stall.transaction_enabled }">
        <div class="stall-main">
          <section class="stall-overview card">
            <div class="overview-top">
              <div class="overview-rating">
                <Star :size="17" fill="currentColor" /><b>{{
                  Number(stall.rating) > 0
                    ? Number(stall.rating).toFixed(1)
                    : "新摊"
                }}</b
                ><span>{{ stall.review_count }} 条评价</span>
              </div>
              <span :class="['badge', stall.status]">{{
                statusText(stall.status)
              }}</span
              ><span class="freshness-text">{{
                stall.can_order
                  ? "可线上点单"
                  : stall.transaction_enabled && stall.accepting_orders === false
                    ? "线上接单暂停"
                    : "线下到访"
              }}</span>
            </div>
            <div class="overview-facts">
              <div>
                <Clock3 :size="16" /><span
                  >预计 {{ stall.prep_minutes }} 分钟备餐</span
                >
              </div>
              <div>
                <ShieldCheck :size="16" /><span>{{
                  stall.can_order
                    ? "支持线上点单 · 到摊自取"
                    : stall.transaction_enabled && stall.accepting_orders === false
                      ? "线上接单暂停 · 可到摊选购"
                      : "线下到访 · 暂未开通在线点单"
                }}</span>
              </div>
              <div
                v-if="stall.transaction_enabled"
                class="stall-payment-status"
              >
                <CreditCard :size="16" /><span>{{
                  stall.wechat_payment?.mode === "simulation"
                    ? "模拟微信支付"
                    : "微信支付"
                }}</span
                ><strong>{{
                  stall.wechat_payment?.available
                    ? "出餐后可线上付款"
                    : "尚未开通"
                }}</strong
                ><span v-if="!stall.wechat_payment?.available">到摊扫码付款</span>
              </div>
              <div
                v-if="stall.transaction_enabled"
                class="stall-delivery-status"
              >
                <MapPin :size="16" /><strong>{{
                  stall.delivery?.mode === "simulation"
                    ? "模拟商家配送"
                    : "商家配送"
                }}</strong
                ><span>{{
                  stall.delivery?.available
                    ? `配送费 ¥${money(stall.delivery.fee_cents)} · 送至校园交接点`
                    : "暂未开放"
                }}</span>
              </div>
            </div>
          </section>
          <details class="stall-arrival" :open="!mobileLayout">
            <summary>
              <MapPin :size="19" />
              <span><strong>{{ stall.address || '商家暂未提供位置' }}</strong><small>{{ confirmedText(stall.last_confirmed_at) }}</small></span>
              <span class="arrival-action">认摊与路线<ChevronRight :size="15" /></span>
            </summary>
            <StallVisitInfo :stall="stall" />
            <div class="stall-share-entry"><StallShare :stall="stall" /></div>
          </details>
          <ReceivingNotice :stall="stall" />
          <div v-if="availability" class="availability-note">
            <AlertCircle :size="16" />{{ availability }}
          </div>
          <nav class="tabs detail-tabs">
            <button :class="{ active: tab === 'menu' }" @click="tab = 'menu'">
              小摊菜单</button
            ><button
              :class="{ active: tab === 'reviews' }"
              @click="tab = 'reviews'"
            >
              大家的评价 <span>{{ stall.review_count }}</span></button
            ><button :class="{ active: tab === 'info' }" @click="tab = 'info'">
              摊位信息
            </button>
          </nav>
          <section v-if="tab === 'menu'">
            <div class="menu-intro">
              <h2>现做现吃，热乎刚好</h2>
              <span>{{ stall.products.length }} 款好味道</span>
            </div>
            <div class="menu-list">
              <article
                v-for="p in stall.products"
                :key="p.id"
                :class="['product-row', { soldout: !productAvailable(p) }]"
              >
                <RouterLink
                  class="product-image"
                  :to="`/stalls/${id}/products/${p.id}`"
                  :aria-label="`查看${p.name}照片与详情`"
                >
                  <img
                    :src="p.image || stall.image"
                    :alt="p.name"
                    loading="lazy"
                    width="160"
                    height="160"
                  /><span v-if="!productAvailable(p)">{{
                    p.sale_paused ? "暂停供应" : p.display_only ? "今天卖完了" : "线上售罄"
                  }}</span>
                </RouterLink>
                <div class="product-content">
                  <h3>
                    <RouterLink
                      class="product-detail-link"
                      :to="`/stalls/${id}/products/${p.id}`"
                      :aria-label="`查看${p.name}详情`"
                      >{{ p.name }}<ArrowUpRight :size="14"
                    /></RouterLink>
                  </h3>
                  <p>{{ p.description }}</p>
                  <small v-if="productAvailable(p)"
                    >{{ stall.transaction_enabled ? "可选餐，提交时核对余量" : "今天有，到摊选购" }}</small
                  >
                  <div class="product-bottom">
                    <span class="price"
                      ><span class="currency">¥</span
                      >{{ money(p.price_cents) }}</span
                    >
                    <div
                      v-if="stall.can_order && !error && productAvailable(p)"
                      class="qty-control"
                    >
                      <button
                        v-if="quantity(p)"
                        @click="change(p, -1)"
                        :aria-label="'减少' + p.name"
                      >
                        <Minus :size="14" /></button
                      ><span v-if="quantity(p)">{{ quantity(p) }}</span
                      ><button
                        class="plus"
                        @click="change(p, 1)"
                        :disabled="cart.remaining(id) <= 0"
                        :aria-label="'添加' + p.name"
                      >
                        <Plus :size="16" />
                      </button>
                    </div>
                    <span v-else class="product-off">{{
                      productUnavailableReason(p) || (stall.transaction_enabled ? "暂不可点单" : "到摊选购")
                    }}</span>
                  </div>
                </div>
              </article>
            </div>
            <div v-if="!stall.products.length" class="empty-state">
              <Utensils :size="28" />
              <p>摊主还在整理菜单，稍后再来看看。</p>
            </div>
          </section>
          <section v-else-if="tab === 'reviews'" class="reviews-list">
            <div v-if="!stall.reviews.length" class="card empty-state">
              <Star :size="30" />
              <h3>等待第一份真实评价</h3>
              <p>完成订单后，就可以写下你的感受。</p>
            </div>
            <article
              v-for="r in stall.reviews"
              :key="r.id"
              class="card review-card"
            >
              <div class="review-head">
                <span class="review-avatar">{{
                  (r.display_name || r.user_name || r.username || "食")[0]
                }}</span>
                <div>
                  <h3>
                    {{
                      r.display_name || r.user_name || r.username || "校园食客"
                    }}
                  </h3>
                  <span class="review-stars"
                    ><Star
                      v-for="n in 5"
                      :key="n"
                      :size="12"
                      :fill="n <= r.rating ? 'currentColor' : 'none'"
                  /></span>
                </div>
                <time>{{ formatTime(r.created_at) }}</time>
              </div>
              <p>{{ r.content }}</p>
              <div v-if="r.merchant_reply" class="merchant-public-reply">
                <strong>商家回复</strong>
                <p>{{ r.merchant_reply }}</p>
                <small>{{ formatTime(r.replied_at) }}</small>
              </div>
            </article>
          </section>
          <section v-else class="card info-panel">
            <p class="eyebrow">ABOUT THIS LITTLE STALL</p>
            <h2>关于这份烟火气</h2>
            <p class="about-description">{{ stall.description }}</p>
            <dl>
              <dt>经营主体</dt>
              <dd>{{ stall.merchant_name }}</dd>
              <dt>经营地址</dt>
              <dd>{{ stall.address }}</dd>
              <dt>经营信息</dt>
              <dd>{{ stall.qualification_note || "暂未提供" }}</dd>
              <dt>接单方式</dt>
              <dd>
                {{
                  stall.transaction_enabled
                    ? stall.wechat_payment?.available
                      ? "线上点单，出餐后微信或到摊付款自取"
                      : "线上点单，到摊扫码付款自取"
                    : "仅提供摊位信息，线下到访"
                }}
              </dd>
              <dt>联系电话</dt>
              <dd>
                <a
                  v-if="stall.contact_phone"
                  :href="`tel:${stall.contact_phone}`"
                  >{{ stall.contact_phone }}</a
                ><span v-else>暂未提供</span>
              </dd>
            </dl>
          </section>
        </div>
        <aside v-if="stall.transaction_enabled" class="pickup-aside">
          <div class="card pickup-card">
            <span class="pickup-icon"
              ><ShoppingBag :size="24" :stroke-width="1.5"
            /></span>
            <p class="eyebrow">PICK UP SOMETHING GOOD</p>
            <h2>提前点好，到摊就取</h2>
            <p class="pickup-description">
              少一点排队，多一点热乎。<br />{{
                stall.wechat_payment?.available
                  ? "出餐后可在订单页微信付款。"
                  : "取餐时直接向商家付款。"
              }}
            </p>
            <div class="pickup-details">
              <span><Clock3 :size="14" />预计备餐</span
              ><b>约 {{ stall.prep_minutes }} 分钟</b
              ><span><MapPin :size="14" />取餐地点</span>
              <p>{{ stall.address }}</p>
            </div>
            <div v-if="cart.count(id)" class="cart-preview">
              <div v-for="item in cart.items(id)" :key="item.product.id">
                <RouterLink
                  :to="`/stalls/${id}/products/${item.product.id}`"
                  :aria-label="`查看餐袋中${item.product.name}详情`"
                  >{{ item.product.name
                  }}<small> ×{{ item.quantity }}</small></RouterLink
                ><b>¥{{ money(item.product.price_cents * item.quantity) }}</b>
              </div>
              <div class="cart-total">
                <span>应付合计</span
                ><strong class="price">¥{{ money(cart.total(id)) }}</strong>
              </div>
            </div>
            <RouterLink
              v-if="cart.count(id) && stall.can_order"
              :to="`/checkout/${id}`"
              class="btn btn-primary checkout-button"
              >去结算<ChevronRight :size="16" /></RouterLink
            ><button v-else class="btn btn-primary checkout-button" disabled>
              {{ stall.can_order ? "先选几份喜欢的" : "暂时无法点单" }}
            </button>
            <p class="pickup-footnote">单摊结算 · 出餐后付款 · 凭码取餐</p>
          </div>
          <p class="aside-note">
            出发前，记得确认摊位的最新位置。<br />好味道，值得一次恰好的相遇。
          </p>
        </aside>
      </div>
      <div v-if="stall.transaction_enabled && cart.count(id)" class="mobile-cart">
        <div class="cart-bag">
          <ShoppingBag :size="22" /><b>{{ cart.count(id) }}</b>
        </div>
        <div class="mobile-cart-total">
          <span class="price">¥{{ money(cart.total(id)) }}</span
          ><small>{{
            stall.delivery?.available
              ? "结算时选择自取或配送 · 配送费另计"
              : "到摊自取 · 配送暂未开放"
          }}</small>
        </div>
        <RouterLink
          v-if="stall.can_order"
          :to="`/checkout/${id}`"
          class="btn btn-primary"
          >去结算<ChevronRight :size="15" /></RouterLink
        ><button v-else class="btn btn-primary" disabled>暂不可点单</button>
      </div></template
    >
  </div>
</template>
<style scoped>
.stall-arrival > summary { display: none; }
.stall-share-entry {
  display: flex;
  justify-content: flex-end;
  margin: 12px 0 18px;
}
.stall-payment-status {
  flex-wrap: wrap;
  gap: 8px !important;
}
.stall-payment-status strong {
  font-size: 10px;
  font-weight: 500;
  padding: 3px 7px;
  border-radius: 5px;
  background: #edf2e6;
  color: #61724f;
}
.stall-page {
  padding-top: 20px;
}
.stall-hero {
  height: 300px;
  border-radius: 22px;
  overflow: hidden;
  position: relative;
  background: #e3ccab;
}
.stall-hero > img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 50% 55%;
}
.stall-hero-shade {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, #211508c9, #21150818 70%),
    linear-gradient(0deg, #241c1550, transparent 55%);
}
.stall-hero-caption {
  position: absolute;
  left: 38px;
  bottom: 32px;
  color: #fff8ee;
}
.stall-hero-caption > span {
  font-size: 11px;
  letter-spacing: 2px;
  color: #f7d9b5;
}
.stall-hero-caption h1 {
  font-family: "Noto Serif SC", serif;
  font-size: 35px;
  margin-top: 12px;
  letter-spacing: 1px;
}
.stall-hero-caption p {
  font-size: 12px;
  margin-top: 12px;
  color: #ecd8c1;
  letter-spacing: 1px;
}
.hero-follow {
  position: absolute;
  right: 28px;
  bottom: 32px;
  display: flex;
  gap: 7px;
  align-items: center;
  min-height: 42px;
  border-radius: 10px;
  border: 1px solid #fff6;
  background: #fff9;
  backdrop-filter: blur(15px);
  padding: 9px 16px;
  font-size: 12px;
  color: #704025;
}
.hero-follow.followed {
  background: #fff2dd;
  color: #b95422;
}
.demo-image-label {
  position: absolute;
  right: 18px;
  top: 15px;
  color: #ffffffbb;
  font-size: 9px;
  background: #24170b44;
  border-radius: 4px;
  padding: 4px 7px;
}
.stall-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 330px;
  gap: 27px;
  margin-top: 25px;
}
.stall-layout.visit-only {
  grid-template-columns: minmax(0, 1fr);
}
.stall-overview {
  padding: 24px;
}
.overview-top {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.overview-rating {
  display: flex;
  gap: 5px;
  align-items: center;
  color: #b76328;
}
.overview-rating b {
  font-size: 18px;
}
.overview-rating > span {
  font-size: 11px;
  color: #a28e75;
  margin-left: 5px;
}
.freshness-text {
  font-size: 11px;
  color: #9e917d;
  margin-left: auto;
}
.overview-facts {
  margin-top: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.overview-facts > div {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #82735d;
  font-size: 12px;
  line-height: 1.7;
}
.overview-facts svg {
  color: #ba9c73;
}
.overview-facts a {
  margin-left: auto;
  color: #b3672c;
  display: flex;
  align-items: center;
  gap: 3px;
  white-space: nowrap;
}
.fact-separator {
  height: 10px;
  width: 1px;
  background: #dfd5c6;
  margin: 0 3px;
}
.availability-note {
  margin-top: 16px;
  padding: 12px 16px;
  border-radius: 10px;
  border: 1px solid #ecdfc6;
  background: #fff5e5;
  color: #987140;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 7px;
}
.detail-tabs {
  margin: 17px 0 22px;
}
.detail-tabs button {
  padding: 15px 10px;
  margin-right: 22px;
  font-size: 14px;
}
.detail-tabs button span {
  font-size: 10px;
  margin-left: 3px;
}
.menu-intro {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.menu-intro h2 {
  font-size: 17px;
  font-weight: 600;
}
.menu-intro > span {
  font-size: 11px;
  color: #aa977e;
}
.menu-list {
  display: flex;
  flex-direction: column;
}
.product-row {
  position: relative;
  display: flex;
  gap: 20px;
  padding: 22px 0;
  border-bottom: 1px solid var(--line);
}
.product-detail-link {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.product-detail-link::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: 12px;
}
.product-detail-link svg {
  color: #b89570;
  transition: transform 180ms ease;
}
.product-row:hover .product-detail-link {
  color: var(--orange-dark);
}
.product-row:hover .product-detail-link svg {
  transform: translate(2px, -2px);
}
.product-detail-link:focus-visible {
  outline: none;
}
.product-detail-link:focus-visible::after {
  outline: 2px solid var(--orange-dark);
  outline-offset: 3px;
}
.product-row .qty-control {
  position: relative;
  z-index: 1;
}
@media (prefers-reduced-motion: reduce) {
  .product-detail-link svg {
    transition: none;
  }
}
.product-image {
  position: relative;
  width: 115px;
  height: 115px;
  border-radius: 14px;
  overflow: hidden;
  background: #f0e4d1;
  flex-shrink: 0;
}
.product-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.product-image > span {
  position: absolute;
  inset: 0;
  background: #2d261166;
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 12px;
}
.soldout .product-image {
  filter: grayscale(0.7);
}
.product-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.product-content h3 {
  font-size: 17px;
  margin-top: 2px;
}
.product-content p {
  font-size: 12px;
  color: #9c8b75;
  margin-top: 7px;
  line-height: 1.6;
}
.product-content small {
  font-size: 10px;
  color: #ba7e3e;
  margin-top: 5px;
}
.product-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: auto;
  padding-top: 10px;
}
.product-bottom > .price {
  font-size: 22px;
  font-weight: 600;
}
.product-off {
  font-size: 11px;
  color: #ad9f8c;
}
.pickup-aside {
  position: relative;
}
.pickup-card {
  position: sticky;
  top: 24px;
  text-align: center;
  padding: 27px 23px;
}
.pickup-icon {
  width: 54px;
  height: 54px;
  display: grid;
  place-items: center;
  margin: 0 auto 18px;
  background: #fff0dc;
  border-radius: 18px;
  color: #b6763a;
}
.pickup-card .eyebrow {
  font-size: 8px;
  letter-spacing: 1.1px;
}
.pickup-card h2 {
  font-family: "Noto Serif SC", serif;
  font-size: 19px;
  margin-top: 10px;
}
.pickup-description {
  font-size: 12px;
  color: #a28d72;
  line-height: 1.9;
  margin-top: 12px;
}
.pickup-details {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 15px;
  text-align: left;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  margin: 23px 0 20px;
  padding: 18px 0;
  font-size: 11px;
  line-height: 1.7;
}
.pickup-details > span {
  display: flex;
  gap: 5px;
  align-items: center;
  color: #9c8c74;
}
.pickup-details b {
  text-align: right;
  font-weight: 500;
  color: #8e6439;
}
.pickup-details p {
  text-align: right;
  color: #86735a;
  font-size: 11px;
}
.cart-preview {
  text-align: left;
  font-size: 11px;
  margin-bottom: 17px;
}
.cart-preview > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 12px 0;
  color: #907960;
}
.cart-preview b {
  font-weight: 500;
  white-space: nowrap;
}
.cart-preview > div > a {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  min-height: 44px;
}
.cart-preview > div > a:hover {
  color: var(--orange-dark);
}
.cart-preview small {
  color: #b0987b;
  font-size: 10px;
}
.cart-preview .cart-total {
  border-top: 1px dashed #e9dac7;
  margin-top: 17px;
  padding-top: 17px;
  font-size: 12px;
}
.cart-total .price {
  font-size: 22px;
}
.checkout-button {
  width: 100%;
  font-size: 13px;
}
.pickup-footnote {
  font-size: 9px;
  color: #baa88d;
  margin-top: 13px;
}
.aside-note {
  text-align: center;
  font-family: "Noto Serif SC", serif;
  font-size: 11px;
  line-height: 2;
  color: #b8a78e;
  margin-top: 22px;
}
.mobile-cart {
  display: none;
}
.reviews-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.review-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.review-avatar {
  height: 36px;
  width: 36px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #f8ebd7;
  color: #b1834c;
  font-size: 14px;
}
.review-head h3 {
  font-size: 12px;
}
.review-stars {
  color: #cc7736;
  display: flex;
  gap: 3px;
  margin-top: 6px;
}
.review-head time {
  margin-left: auto;
  font-size: 10px;
  color: #aa9a84;
}
.review-card > p {
  font-size: 13px;
  line-height: 1.9;
  margin-top: 18px;
  color: #756953;
}
.info-panel h2 {
  font-family: "Noto Serif SC", serif;
  font-size: 20px;
}
.about-description {
  font-size: 13px;
  color: #a08c70;
  margin-top: 17px;
}
.info-panel dl {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 18px 12px;
  border-top: 1px solid var(--line);
  padding-top: 23px;
  margin-top: 23px;
  font-size: 12px;
  line-height: 1.8;
}
.info-panel dt {
  color: #ad9c83;
}
.info-panel dd {
  margin: 0;
  color: #73634c;
}
.info-panel dd a {
  color: var(--orange-dark);
}
@media (max-width: 1023px) {
  .stall-layout {
    grid-template-columns: minmax(0, 1fr) 270px;
    gap: 20px;
  }
  .pickup-card {
    padding: 24px 18px;
  }
  .product-image {
    width: 95px;
    height: 105px;
  }
  .product-row {
    gap: 15px;
  }
  .freshness-text {
    margin-left: 0;
  }
  .overview-top {
    gap: 9px;
  }
}
@media (max-width: 767px) {
  .stall-page {
    padding-top: 12px;
    padding-bottom: 90px;
  }
  .back-link {
    font-size: 11px;
    margin-bottom: 12px;
  }
  .stall-hero {
    height: 196px;
    margin: 0;
    border-radius: 16px;
  }
  .stall-hero-caption {
    left: 20px;
    bottom: 23px;
    right: 20px;
  }
  .stall-hero-caption > span {
    font-size: 9px;
    letter-spacing: 1px;
  }
  .stall-hero-caption h1 {
    font-size: 26px;
    margin-top: 6px;
  }
  .stall-hero-caption p {
    font-size: 12px;
    margin-top: 6px;
    max-width: 67%;
    line-height: 1.6;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .hero-follow {
    right: 18px;
    bottom: 26px;
    min-height: 44px;
    font-size: 12px;
    padding: 7px 10px;
    gap: 5px;
  }
  .hero-follow svg {
    width: 14px;
  }
  .demo-image-label {
    font-size: 8px;
    right: 14px;
    top: 12px;
  }
  .stall-layout {
    display: block;
    margin-top: 14px;
  }
  .pickup-aside {
    display: none;
  }
  .stall-overview {
    padding: 15px;
  }
  .stall-arrival { margin-top: 12px; background: #fffbf5; border: 1px solid var(--line); border-radius: 14px; }
  .stall-arrival > summary { display: flex; align-items: center; gap: 9px; min-height: 64px; padding: 12px 14px; cursor: pointer; list-style: none; color: #795133; }
  .stall-arrival > summary::-webkit-details-marker { display: none; }
  .stall-arrival > summary > svg { flex: none; }
  .stall-arrival > summary > span:not(.arrival-action) { min-width: 0; display: grid; gap: 4px; }
  .stall-arrival summary strong { font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
  .stall-arrival summary small { font-size: 11px; color: #81705f; }
  .arrival-action { flex: none; display: flex; align-items: center; margin-left: auto; font-size: 11px; }
  .stall-arrival[open] .arrival-action svg { transform: rotate(90deg); }
  .stall-arrival :deep(.stall-visit) { margin: 0; border: 0; border-top: 1px solid var(--line); border-radius: 0 0 14px 14px; }
  .overview-top {
    gap: 8px;
  }
  .overview-rating b {
    font-size: 16px;
  }
  .overview-rating svg {
    width: 14px;
  }
  .overview-rating > span {
    font-size: 10px;
  }
  .freshness-text {
    font-size: 9px;
    margin-left: auto;
  }
  .overview-top .badge {
    font-size: 9px;
    padding: 4px 6px;
  }
  .overview-facts {
    gap: 8px;
    margin-top: 12px;
  }
  .overview-facts > div {
    font-size: 12px;
    gap: 6px;
  }
  .overview-facts svg {
    width: 14px;
  }
  .detail-tabs {
    margin-top: 12px;
  }
  .detail-tabs button {
    font-size: 13px;
    margin-right: 17px;
    padding: 13px 8px;
  }
  .menu-intro h2 {
    font-size: 15px;
  }
  .menu-intro > span {
    font-size: 10px;
  }
  .product-row {
    gap: 12px;
    padding: 16px 0;
  }
  .product-image {
    width: 88px;
    height: 88px;
    border-radius: 12px;
  }
  .product-content h3 {
    font-size: 15px;
  }
  .product-content p {
    font-size: 12px;
    margin-top: 5px;
  }
  .product-bottom > .price {
    font-size: 20px;
  }
  .qty-control button {
    width: 44px;
    height: 44px;
  }
  .mobile-cart {
    display: flex;
    position: fixed;
    z-index: 60;
    bottom: 0;
    left: 0;
    right: 0;
    align-items: center;
    gap: 12px;
    padding: 12px 18px calc(12px + env(safe-area-inset-bottom));
    background: #fffdf8f5;
    border-top: 1px solid var(--line);
    backdrop-filter: blur(15px);
  }
  .cart-bag {
    width: 42px;
    height: 42px;
    border-radius: 50%;
    background: #fce6ca;
    color: #a26429;
    display: grid;
    place-items: center;
    position: relative;
  }
  .cart-bag b {
    position: absolute;
    top: -3px;
    right: -3px;
    background: var(--orange-dark);
    color: white;
    border-radius: 50%;
    font-size: 9px;
    min-width: 16px;
    height: 16px;
    display: grid;
    place-items: center;
    border: 2px solid #fff;
  }
  .mobile-cart-total {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .mobile-cart-total .price {
    font-size: 21px;
  }
  .mobile-cart-total small {
    font-size: 9px;
    color: #a6957b;
  }
  .mobile-cart .btn {
    margin-left: auto;
    min-height: 44px;
    font-size: 12px;
    border-radius: 24px;
    padding: 10px 21px;
  }
  .availability-note {
    font-size: 11px;
    align-items: flex-start;
    line-height: 1.8;
  }
  .availability-note svg {
    margin-top: 2px;
  }
  .review-card {
    padding: 18px;
  }
  .review-head time {
    font-size: 9px;
  }
  .info-panel dl {
    grid-template-columns: 65px 1fr;
    font-size: 11px;
  }
}
</style>
