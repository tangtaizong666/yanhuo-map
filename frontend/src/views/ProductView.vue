<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
  AlertCircle,
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronRight,
  Clock3,
  MapPin,
  Minus,
  Plus,
  ShoppingBag,
  Store,
  Utensils,
} from "lucide-vue-next";
import { api, confirmedText, money, routeUrl, statusText } from "../lib/api";
import { notify } from "../lib/notify";
import type { Stall, Portion } from "../lib/types";
import { normalizePortions, invalidPortions } from "../lib/portions";
import PortionEditor from "../components/PortionEditor.vue";
import ReceivingNotice from "../components/ReceivingNotice.vue";
import {
  productAvailable,
  productUnavailableReason,
} from "../lib/availability";
import { useCart } from "../stores/cart";
import { useSession } from "../stores/session";

const route = useRoute();
const cart = useCart();
const session = useSession();
const stall = ref<Stall | null>(null);
const loading = ref(true);
const error = ref("");
const imageFailed = ref(false);
const selected = ref(1);
const portions = ref<Portion[]>([]);
const justAdded = ref(false);
const stallId = computed(() => Number(route.params.id));
const productId = computed(() => Number(route.params.productId));
const product = computed(() =>
  stall.value?.products.find((item) => item.id === productId.value),
);
const inCart = computed(
  () =>
    cart
      .items(stallId.value)
      .find((item) => item.product.id === productId.value)?.quantity || 0,
);
const addedToCart = computed(() => justAdded.value && inCart.value > 0);
watch(
  inCart,
  (quantity) => {
    if (!quantity) justAdded.value = false;
  },
  { flush: "sync" },
);
watch(
  () => session.user?.id,
  () => {
    justAdded.value = false;
  },
  { flush: "sync" },
);
const capacity = computed(() =>
  productAvailable(product.value) ? cart.remaining(stallId.value) : 0,
);
const canAdd = computed(() =>
  Boolean(
    stall.value?.can_order &&
    productAvailable(product.value) &&
    capacity.value > 0 &&
    !error.value,
  ),
);
const photo = computed(() => product.value?.image || "");
const unavailable = computed(() => {
  if (!stall.value || !product.value) return "";
  if (error.value) return "暂时无法确认最新库存，请刷新后再点单。";
  if (!productAvailable(product.value))
    return productUnavailableReason(product.value);
  if (!stall.value.can_order && stall.value.order_unavailable_reason)
    return stall.value.order_unavailable_reason;
  if (!stall.value.transaction_enabled)
    return "这家小摊目前仅支持线下到访，暂未开通在线点单。";
  if (stall.value.status === "stale")
    return "摊位位置需要重新确认，在线点单暂时关闭。";
  if (stall.value.status === "closed")
    return "摊主已经收摊，营业后再来看看吧。";
  if (stall.value.status === "paused")
    return "摊主暂时休息，恢复营业后即可点单。";
  if (!stall.value.can_order) return "这家小摊暂时无法接单，请稍后再来。";
  if (product.value.availability === "sold_out")
    return "这道餐点暂时售罄，可以看看小摊的其他好味道。";
  return "";
});
const actionText = computed(() => {
  if (product.value?.sale_paused) return "暂停供应";
  if (product.value?.is_active === false) return "已下架";
  if (product.value && product.value.availability === "sold_out")
    return "暂时售罄";
  if (!stall.value?.transaction_enabled) return "仅限线下到访";
  if (!stall.value?.can_order || error.value) return "暂不可点单";
  if (capacity.value <= 0) return "每单合计最多 10 份";
  return "加入餐袋";
});
let sequence = 0;
let timer: ReturnType<typeof setInterval> | undefined;
async function load() {
  const current = ++sequence;
  try {
    const data = await api<Stall>(`/stalls/${stallId.value}`);
    if (current !== sequence) return;
    stall.value = data;
    error.value = "";
  } catch (cause) {
    if (current === sequence) error.value = (cause as Error).message;
  } finally {
    if (current === sequence) loading.value = false;
  }
}
function start() {
  justAdded.value = false;
  stall.value = null;
  error.value = "";
  loading.value = true;
  imageFailed.value = false;
  selected.value = 1;
  portions.value = [];
  void load();
}
function refreshVisible() {
  if (!document.hidden) void load();
}
function addToCart() {
  if (addedToCart.value || !canAdd.value || !product.value) return;
  if (invalidPortions(product.value, portions.value, selected.value)) {
    notify("口味选项已变更，请重新选择", "info");
    return;
  }
  const amount = Math.min(selected.value, capacity.value);
  const existing = cart
    .items(stallId.value)
    .find((item) => item.product.id === productId.value);
  cart.setQuantity(stallId.value, product.value, inCart.value + amount, [
    ...normalizePortions(existing?.portions, inCart.value),
    ...normalizePortions(portions.value, amount),
  ]);
  notify(`已加入 ${amount} 份${product.value.name}`, "success");
  selected.value = 1;
  portions.value = [];
  justAdded.value = true;
}
watch(
  [selected, () => JSON.stringify(portions.value)],
  () => {
    justAdded.value = false;
  },
  { flush: "sync" },
);
watch(
  selected,
  (quantity) => {
    portions.value = normalizePortions(portions.value, quantity);
  },
  { flush: "sync" },
);
watch(capacity, (value) => {
  selected.value = Math.max(1, Math.min(selected.value, value));
});
watch(photo, () => {
  imageFailed.value = false;
});
watch([stallId, productId], start);
onMounted(() => {
  start();
  timer = setInterval(refreshVisible, 30000);
  document.addEventListener("visibilitychange", refreshVisible);
});
onUnmounted(() => {
  sequence++;
  clearInterval(timer);
  document.removeEventListener("visibilitychange", refreshVisible);
});
</script>

<template>
  <div class="page dish-page">
    <nav class="dish-breadcrumb" aria-label="餐点导航">
      <RouterLink :to="`/stalls/${stallId}`"
        ><ArrowLeft :size="16" />返回小摊</RouterLink
      >
      <span v-if="stall"
        ><span class="breadcrumb-divider">/</span>{{ stall.name }}</span
      >
    </nav>

    <div v-if="loading" class="dish-loading" aria-label="正在加载餐点">
      <div class="skeleton"></div>
      <div class="skeleton"></div>
    </div>
    <section v-else-if="error && !stall" class="empty-state card dish-empty">
      <AlertCircle :size="32" />
      <h1>暂时无法查看这道餐点</h1>
      <p>{{ error }}</p>
      <button class="btn btn-primary" @click="load">重新加载</button>
    </section>
    <section v-else-if="!product" class="empty-state card dish-empty">
      <Utensils :size="32" />
      <h1>这道餐点已下架或不存在</h1>
      <p>菜单会随小摊更新，去看看现在有哪些好味道。</p>
      <RouterLink :to="`/stalls/${stallId}`" class="btn btn-primary"
        >查看小摊菜单</RouterLink
      >
    </section>

    <template v-else-if="stall">
      <div v-if="error" class="error-message dish-error">
        {{ error }} · 当前展示上次加载的内容 <button @click="load">刷新</button>
      </div>
      <div class="dish-layout">
        <div class="dish-visual-column">
          <figure class="dish-photo">
            <img
              v-if="photo && !imageFailed"
              :src="photo"
              :alt="product.name"
              width="1000"
              height="1000"
              fetchpriority="high"
              @error="imageFailed = true"
            />
            <div v-else class="dish-photo-missing">
              <Utensils :size="54" :stroke-width="1" /><span
                >商家暂未提供餐点照片</span
              >
            </div>
            <span class="dish-photo-category"
              >{{ stall.category }}<span>·</span>{{ stall.area_name }}</span
            >
            <span
              v-if="!productAvailable(product)"
              class="dish-soldout-stamp"
              >{{ product.sale_paused ? "暂停供应" : "暂时售罄" }}</span
            >
            <figcaption v-if="session.config?.demo_mode">
              校园示例餐点 · 配图仅供参考
            </figcaption>
          </figure>
        </div>

        <section class="dish-summary">
          <div class="dish-title-top">
            <h1>{{ product.name }}</h1>
            <span :class="['badge', stall.status]">{{
              statusText(stall.status)
            }}</span>
          </div>
          <div class="dish-price-line">
            <strong><small>¥</small>{{ money(product.price_cents) }}</strong
            ><span>每份</span>
          </div>
          <div class="dish-decision-facts" aria-label="备餐与取餐方式">
            <div v-if="stall.transaction_enabled" class="dish-prep-fact">
              <Clock3 :size="18" />
              <span
                ><strong>预计 {{ stall.prep_minutes }} 分钟备餐</strong
                ><small>商家接单后更新进度</small></span
              >
            </div>
            <div class="dish-fulfillment-fact">
              <ShoppingBag :size="18" /><span
                ><strong>{{
                  stall.transaction_enabled
                    ? stall.delivery?.available
                      ? "到摊自取 / 商家配送"
                      : "到摊自取"
                    : "线下到访"
                }}</strong
                ><small>{{
                  stall.transaction_enabled
                    ? stall.delivery?.available
                      ? "配送需先在线付款"
                      : "出餐后付款取餐"
                    : "到摊了解购买方式"
                }}</small></span
              >
            </div>
          </div>
          <p v-if="product.description" class="dish-description">
            {{ product.description }}
          </p>

          <div class="dish-visit">
            <RouterLink :to="`/stalls/${stallId}`" class="dish-stall-link">
              <Store :size="18" /><strong>{{ stall.name }}</strong>
              <span>看菜单<ChevronRight :size="15" /></span>
            </RouterLink>
            <div class="dish-location-fact">
              <MapPin :size="18" /><span
                ><strong>{{ stall.address || "商家暂未提供具体地址" }}</strong
                ><small>{{
                  confirmedText(stall.last_confirmed_at)
                }}</small></span
              ><a
                v-if="stall.address"
                class="dish-directions"
                :href="routeUrl(stall.latitude, stall.longitude, stall.name)"
                target="_blank"
                rel="noopener"
                >路线<ArrowUpRight :size="15"
              /></a>
            </div>
          </div>

          <p v-if="unavailable" class="dish-unavailable" role="status">
            <AlertCircle :size="18" />{{ unavailable }}
          </p>
          <ReceivingNotice :stall="stall" compact />
          <form class="dish-purchase-panel" @submit.prevent="addToCart">
            <PortionEditor
              v-if="canAdd && !addedToCart"
              v-model="portions"
              :product="product"
              :quantity="selected"
            />
            <div v-if="addedToCart" class="dish-added-feedback" role="status">
              <span><Check :size="17" />已加入餐袋，口味与备注已保留</span>
              <button v-if="canAdd" type="button" @click="justAdded = false">
                再加这道餐点
              </button>
            </div>
            <p v-if="canAdd && !addedToCart" class="dish-allowance">
              同一摊位合计最多 10 份，当前还可加入 {{ capacity }} 份
            </p>
            <div
              class="dish-action-bar"
              :aria-label="
                addedToCart ? '已加入餐袋，继续结算' : '选择份数并加入餐袋'
              "
            >
              <div
                v-if="addedToCart"
                class="dish-purchase-controls dish-next-step"
              >
                <div class="dish-added-total">
                  <small>本摊餐袋 {{ cart.count(stallId) }} 份</small
                  ><strong>¥{{ money(cart.total(stallId)) }}</strong>
                </div>
                <RouterLink
                  v-if="stall.can_order && !error"
                  :to="`/checkout/${stallId}`"
                  class="btn btn-primary dish-checkout-button"
                  aria-label="去结算"
                  >去结算<ChevronRight :size="18"
                /></RouterLink>
                <button
                  v-else
                  type="button"
                  class="btn btn-primary dish-checkout-button"
                  disabled
                >
                  暂不可点单
                </button>
              </div>
              <div v-else class="dish-purchase-controls">
                <div
                  v-if="canAdd"
                  class="dish-quantity"
                  aria-label="选择餐点数量"
                >
                  <button
                    type="button"
                    aria-label="减少份数"
                    :disabled="selected <= 1"
                    @click="selected--"
                  >
                    <Minus :size="17" /></button
                  ><output aria-live="polite">{{ selected }}</output
                  ><button
                    type="button"
                    aria-label="增加份数"
                    :disabled="selected >= capacity"
                    @click="selected++"
                  >
                    <Plus :size="17" />
                  </button>
                </div>
                <div v-else class="dish-disabled-price">
                  <small>餐点单价</small
                  ><strong>¥{{ money(product.price_cents) }}</strong>
                </div>
                <button
                  type="submit"
                  class="btn btn-primary dish-add-button"
                  :disabled="!canAdd"
                >
                  <ShoppingBag :size="19" /><span>{{ actionText }}</span
                  ><strong v-if="canAdd"
                    >¥{{ money(product.price_cents * selected) }}</strong
                  >
                </button>
              </div>
              <div v-if="addedToCart" class="dish-cart-line dish-added-actions">
                <span><Check :size="14" />口味与备注已保留</span>
                <RouterLink :to="`/stalls/${stallId}`"
                  >继续选餐<ChevronRight :size="14"
                /></RouterLink>
              </div>
              <div v-else-if="cart.count(stallId)" class="dish-cart-line">
                <span
                  ><Check :size="14" />本摊餐袋 {{ cart.count(stallId) }} 份 ·
                  ¥{{ money(cart.total(stallId)) }}</span
                ><RouterLink
                  v-if="stall.can_order && !error"
                  :to="`/checkout/${stallId}`"
                  >去结算<ChevronRight :size="14" /></RouterLink
                ><RouterLink v-else :to="`/stalls/${stallId}`"
                  >返回菜单<ChevronRight :size="14"
                /></RouterLink>
              </div>
            </div>
          </form>
          <div class="dish-contact-note">
            <span>忌口或配料需求，请先向商家确认。</span>
            <a v-if="stall.contact_phone" :href="`tel:${stall.contact_phone}`"
              >联系商家<ArrowUpRight :size="15"
            /></a>
            <RouterLink v-else :to="`/stalls/${stallId}`"
              >摊位信息<ChevronRight :size="15"
            /></RouterLink>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dish-page {
  padding-top: 24px;
  padding-bottom: 56px;
}
.dish-breadcrumb {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 22px;
  font-size: 13px;
  color: #81705d;
}
.dish-breadcrumb a {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  color: #6f5c48;
}
.dish-breadcrumb > span {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.breadcrumb-divider {
  color: #c8b8a3;
}
.dish-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  gap: clamp(28px, 5vw, 72px);
  align-items: start;
}
.dish-visual-column,
.dish-summary {
  min-width: 0;
}
.dish-photo {
  aspect-ratio: 1;
  margin: 0;
  border-radius: 24px;
  overflow: hidden;
  position: relative;
  background: #e9ddc9;
}
.dish-photo > img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.dish-photo-missing {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  color: #79634a;
  font-size: 13px;
  background: radial-gradient(circle, #fff8ed, #eaddc7);
}
.dish-photo-category {
  position: absolute;
  top: 18px;
  left: 18px;
  display: inline-flex;
  gap: 8px;
  max-width: calc(100% - 36px);
  padding: 8px 12px;
  border-radius: 30px;
  background: #fff8edee;
  color: #675038;
  font-size: 12px;
}
.dish-photo-category > span {
  color: #9c7950;
}
.dish-photo figcaption {
  position: absolute;
  bottom: 14px;
  left: 14px;
  background: #261a129c;
  color: #fff5e7;
  border-radius: 7px;
  padding: 5px 9px;
  font-size: 11px;
}
.dish-soldout-stamp {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: #241a1270;
  color: #fff9ef;
  font-size: 26px;
  letter-spacing: 3px;
}
.dish-title-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.dish-title-top .badge {
  flex-shrink: 0;
  margin-top: 9px;
}
.dish-summary h1 {
  font-family: "Noto Serif SC", serif;
  font-size: clamp(28px, 3vw, 40px);
  line-height: 1.35;
  margin: 0;
  color: #3d3024;
  overflow-wrap: anywhere;
}
.dish-price-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 12px 0 18px;
  color: #81705d;
  font-size: 13px;
}
.dish-price-line > strong {
  color: #c25726;
  font-size: 34px;
  font-weight: 650;
  line-height: 1;
}
.dish-price-line strong small {
  font-size: 18px;
  margin-right: 3px;
}
.dish-decision-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  padding: 16px 0;
  border-top: 1px solid #e7dccb;
  border-bottom: 1px solid #e7dccb;
}
.dish-decision-facts > div {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
}
.dish-decision-facts svg {
  flex-shrink: 0;
  color: #a06b3f;
  margin-top: 2px;
}
.dish-decision-facts span {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.dish-decision-facts strong {
  color: #514130;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.dish-decision-facts small {
  color: #81705d;
  font-size: 12px;
  line-height: 1.6;
}
.dish-description {
  color: #78654f;
  line-height: 1.8;
  font-size: 14px;
  margin: 16px 0;
  white-space: pre-line;
  overflow-wrap: anywhere;
}
.dish-visit {
  margin: 12px 0;
}
.dish-stall-link {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 44px;
  color: #6f5135;
}
.dish-stall-link > svg {
  flex-shrink: 0;
}
.dish-stall-link strong {
  font-size: 14px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.dish-stall-link > span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: auto;
  flex-shrink: 0;
  font-size: 12px;
  color: #a14f25;
}
.dish-location-fact {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 8px 0;
}
.dish-location-fact > svg {
  flex-shrink: 0;
  color: #94704c;
  margin-top: 3px;
}
.dish-location-fact > span {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.dish-location-fact strong {
  color: #6d5a44;
  font-size: 13px;
  line-height: 1.6;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.dish-location-fact small {
  color: #81705d;
  font-size: 12px;
  line-height: 1.5;
}
.dish-directions {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: #a14f25;
  font-size: 13px;
  margin-left: auto;
  min-height: 44px;
  min-width: 48px;
  white-space: nowrap;
}
.dish-unavailable {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px;
  border: 1px solid #ebd5b7;
  border-radius: 10px;
  background: #fff3df;
  color: #855320;
  font-size: 13px;
  line-height: 1.7;
  margin: 14px 0;
}
.dish-unavailable svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.dish-purchase-panel {
  margin-top: 16px;
  padding-top: 8px;
  border-top: 1px solid #e7dccb;
}
.dish-allowance {
  color: #81705d;
  font-size: 12px;
  line-height: 1.6;
  margin: 0 0 14px;
}
.dish-added-feedback {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
  color: #526d45;
  font-size: 13px;
}
.dish-added-feedback > span {
  display: flex;
  align-items: center;
  gap: 7px;
  line-height: 1.6;
}
.dish-added-feedback button {
  justify-self: start;
  min-height: 44px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #a14f25;
  text-decoration: underline;
}
.dish-added-total {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  min-width: 110px;
}
.dish-added-total small {
  color: #75624c;
  font-size: 12px;
}
.dish-added-total strong {
  color: #b84f1f;
  font-size: 24px;
}
.dish-checkout-button {
  flex: 1;
  min-height: 50px;
  font-size: 15px;
  border-radius: 12px;
}
.dish-purchase-controls {
  display: flex;
  gap: 12px;
  align-items: stretch;
}
.dish-quantity {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 50px;
  border: 1px solid #ddccb5;
  border-radius: 12px;
  background: #fffdf8;
  flex-shrink: 0;
}
.dish-quantity button {
  min-width: 44px;
  min-height: 48px;
  display: grid;
  place-items: center;
  border: 0;
  background: transparent;
  color: #865b36;
}
.dish-quantity button:disabled {
  color: #a89b8b;
}
.dish-quantity output {
  width: 22px;
  font-size: 15px;
  text-align: center;
  color: #55432f;
}
.dish-add-button {
  flex: 1;
  min-width: 0;
  gap: 8px;
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
}
.dish-add-button strong {
  margin-left: auto;
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
}
.dish-disabled-price {
  display: flex;
  justify-content: center;
  flex-direction: column;
  gap: 4px;
  min-width: 80px;
}
.dish-disabled-price small {
  font-size: 12px;
  color: #81705d;
}
.dish-disabled-price strong {
  font-size: 22px;
  color: #c25726;
}
.dish-cart-line {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: space-between;
  font-size: 12px;
  color: #75624c;
  min-height: 44px;
  flex-wrap: wrap;
}
.dish-cart-line > span,
.dish-cart-line > a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.dish-cart-line > a {
  color: #a14f25;
  font-size: 13px;
  min-height: 44px;
  white-space: nowrap;
}
.dish-cart-line > span svg {
  color: #55764d;
}
.dish-contact-note {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 12px;
  line-height: 1.7;
  color: #81705d;
  margin-top: 10px;
}
.dish-contact-note a {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  min-height: 44px;
  color: #a14f25;
}
.dish-error {
  margin-bottom: 20px;
}
.dish-error button {
  border: 0;
  background: transparent;
  color: inherit;
  text-decoration: underline;
  margin-left: 10px;
  min-height: 44px;
}
.dish-empty {
  padding: 65px 20px;
}
.dish-empty h1 {
  font-size: 22px;
}
.dish-loading {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 40px;
}
.dish-loading > div {
  min-height: 450px;
  border-radius: 24px;
}
@media (min-width: 768px) and (max-width: 1023px) {
  .dish-layout {
    gap: 26px;
  }
  .dish-decision-facts {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .dish-add-button {
    flex-wrap: wrap;
    gap: 4px;
    padding: 10px;
  }
  .dish-add-button > svg {
    display: none;
  }
  .dish-add-button strong {
    margin-left: 0;
  }
}
@media (max-width: 767px) {
  .dish-page {
    padding-top: 8px;
    padding-bottom: calc(150px + env(safe-area-inset-bottom));
  }
  .dish-breadcrumb {
    gap: 10px;
    margin-bottom: 8px;
    font-size: 12px;
  }
  .dish-breadcrumb > span {
    gap: 10px;
  }
  .dish-layout {
    display: block;
  }
  .dish-photo {
    aspect-ratio: 1.48;
    border-radius: 16px;
    margin-bottom: 16px;
  }
  .dish-photo-category {
    top: 12px;
    left: 12px;
    padding: 6px 10px;
    font-size: 11px;
  }
  .dish-photo figcaption {
    left: 12px;
    bottom: 12px;
  }
  .dish-summary h1 {
    font-size: 26px;
  }
  .dish-title-top .badge {
    margin-top: 5px;
  }
  .dish-price-line {
    margin: 10px 0 14px;
  }
  .dish-price-line > strong {
    font-size: 30px;
  }
  .dish-decision-facts {
    gap: 10px;
    padding: 12px 0;
  }
  .dish-decision-facts > div {
    gap: 6px;
  }
  .dish-decision-facts strong {
    font-size: 13px;
  }
  .dish-decision-facts small {
    font-size: 12px;
  }
  .dish-description {
    font-size: 13px;
    line-height: 1.7;
    margin: 12px 0 8px;
  }
  .dish-visit {
    margin: 8px 0;
  }
  .dish-purchase-panel {
    margin-top: 10px;
  }
  .dish-action-bar {
    position: fixed;
    z-index: 60;
    left: 0;
    right: 0;
    bottom: 0;
    padding: 10px 16px calc(10px + env(safe-area-inset-bottom));
    background: #fffaf2f5;
    backdrop-filter: blur(16px);
    border-top: 1px solid #e9dcc7;
    box-shadow: 0 -5px 24px #5f3e1510;
  }
  .dish-purchase-controls {
    gap: 10px;
  }
  .dish-quantity {
    min-height: 48px;
  }
  .dish-quantity button {
    min-height: 46px;
  }
  .dish-add-button {
    font-size: 13px;
    padding: 12px;
    gap: 6px;
  }
  .dish-add-button > svg {
    display: none;
  }
  .dish-add-button strong {
    font-size: 16px;
  }
  .dish-cart-line {
    margin-top: 4px;
  }
  .dish-contact-note {
    margin-top: 4px;
  }
  .dish-loading {
    grid-template-columns: 1fr;
    gap: 24px;
  }
  .dish-loading > div {
    min-height: 280px;
  }
}
</style>
