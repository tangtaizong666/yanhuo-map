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
const capacity = computed(() =>
  Math.max(0, Math.min(99, product.value?.stock || 0) - inCart.value),
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
  if (product.value.stock <= 0)
    return "这道餐点暂时售罄，可以看看小摊的其他好味道。";
  return "";
});
const actionText = computed(() => {
  if (product.value?.sale_paused) return "暂停供应";
  if (product.value?.is_active === false) return "已下架";
  if (product.value && product.value.stock <= 0) return "暂时售罄";
  if (!stall.value?.transaction_enabled) return "仅限线下到访";
  if (!stall.value?.can_order || error.value) return "暂不可点单";
  if (capacity.value <= 0)
    return inCart.value >= 99 ? "已达数量上限" : "已达库存上限";
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
  if (!canAdd.value || !product.value) return;
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
}
watch(selected, (quantity) => {
  portions.value = normalizePortions(portions.value, quantity);
});
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
          <div class="dish-photo-caption">
            <span>一餐一味，生活有滋有味。</span
            ><Utensils :size="16" :stroke-width="1.5" />
          </div>
        </div>

        <section class="dish-summary">
          <div class="dish-title-top">
            <span class="dish-eyebrow">小摊里的好味道</span
            ><span :class="['badge', stall.status]">{{
              statusText(stall.status)
            }}</span>
          </div>
          <h1>{{ product.name }}</h1>
          <p class="dish-description">
            {{
              product.description ||
              "商家尚未填写餐点介绍，可以在到访前向商家了解详情。"
            }}
          </p>
          <div class="dish-price-line">
            <strong><small>¥</small>{{ money(product.price_cents) }}</strong
            ><span>每份</span
            ><span
              class="dish-stock"
              :class="{ low: product.stock > 0 && product.stock < 10 }"
              >{{
                productAvailable(product)
                  ? `线上剩余 ${product.stock} 份`
                  : productUnavailableReason(product)
              }}</span
            >
          </div>

          <ReceivingNotice :stall="stall" />
          <RouterLink :to="`/stalls/${stallId}`" class="dish-stall-link">
            <img
              v-if="stall.image"
              :src="stall.image"
              :alt="stall.name"
              width="52"
              height="52"
            />
            <span v-else class="dish-stall-icon"><Store :size="23" /></span>
            <span
              ><small>来自这家小摊</small
              ><strong>{{ stall.name }}</strong></span
            >
            <span class="dish-stall-more"
              >逛逛菜单<ChevronRight :size="15"
            /></span>
          </RouterLink>

          <div class="dish-pickup-facts">
            <div>
              <span class="dish-fact-icon"><ShoppingBag :size="19" /></span
              ><span
                ><strong>{{
                  stall.transaction_enabled
                    ? stall.delivery?.available
                      ? "到摊自取 / 商家配送"
                      : "到摊自取 · 出餐后付款"
                    : "线下到访"
                }}</strong
                ><small>{{
                  stall.transaction_enabled
                    ? stall.delivery?.available
                      ? "结算时选择取餐方式，配送需先在线付款"
                      : "商家接单后备餐，付款方式在结算页说明"
                    : "尚未开通在线点单，请到摊位了解购买方式"
                }}</small></span
              >
            </div>
            <div v-if="stall.transaction_enabled">
              <span class="dish-fact-icon"><Clock3 :size="19" /></span
              ><span
                ><strong>预计 {{ stall.prep_minutes }} 分钟备餐</strong
                ><small>实际进度以商家接单后的订单状态为准</small></span
              >
            </div>
            <div>
              <span class="dish-fact-icon"><MapPin :size="19" /></span
              ><span
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
          <div class="dish-purchase-panel">
            <PortionEditor
              v-if="canAdd"
              v-model="portions"
              :product="product"
              :quantity="selected"
            />
            <div class="dish-purchase-controls">
              <div
                v-if="canAdd"
                class="dish-quantity"
                aria-label="选择餐点数量"
              >
                <button
                  aria-label="减少份数"
                  :disabled="selected <= 1"
                  @click="selected--"
                >
                  <Minus :size="17" /></button
                ><output aria-live="polite">{{ selected }}</output
                ><button
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
                class="btn btn-primary dish-add-button"
                :disabled="!canAdd"
                @click="addToCart"
              >
                <ShoppingBag :size="19" /><span>{{ actionText }}</span
                ><strong v-if="canAdd"
                  >¥{{ money(product.price_cents * selected) }}</strong
                >
              </button>
            </div>
            <div v-if="cart.count(stallId)" class="dish-cart-line">
              <span
                ><Check :size="14" />本摊餐袋 {{ cart.count(stallId) }} 份 · ¥{{
                  money(cart.total(stallId))
                }}</span
              ><RouterLink
                v-if="stall.can_order && !error"
                :to="`/checkout/${stallId}`"
                >查看餐袋并结算<ChevronRight :size="14" /></RouterLink
              ><RouterLink v-else :to="`/stalls/${stallId}`"
                >返回菜单<ChevronRight :size="14"
              /></RouterLink>
            </div>
            <p v-else class="dish-cart-note">
              同一摊位一起结算 · 提交订单前会再次确认库存与价格
            </p>
          </div>
        </section>
      </div>

      <section class="dish-before-order">
        <div>
          <span class="dish-eyebrow">点单前，了解多一点</span>
          <h2>好好吃饭，也放心选择。</h2>
        </div>
        <p>
          餐点介绍由商家提供。如有忌口、过敏或配料方面的需求，请在点单前向商家确认。
        </p>
        <a v-if="stall.contact_phone" :href="`tel:${stall.contact_phone}`"
          >联系商家<ArrowUpRight :size="16"
        /></a>
        <RouterLink v-else :to="`/stalls/${stallId}`"
          >查看摊位信息<ArrowUpRight :size="16"
        /></RouterLink>
      </section>
    </template>
  </div>
</template>

<style scoped>
.dish-page {
  padding-top: 25px;
  padding-bottom: 55px;
}
.dish-breadcrumb {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 12px;
  color: #897a68;
  margin-bottom: 26px;
  min-height: 30px;
}
.dish-breadcrumb a {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #6f5c48;
  min-height: 44px;
}
.dish-breadcrumb > span {
  display: flex;
  gap: 16px;
  align-items: center;
}
.breadcrumb-divider {
  color: #c8b8a3;
}
.dish-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  gap: clamp(30px, 5vw, 74px);
  align-items: start;
}
.dish-visual-column {
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
  color: #9c8264;
  font-size: 13px;
  background: radial-gradient(circle, #fff8ed, #eaddc7);
}
.dish-photo-category {
  position: absolute;
  top: 20px;
  left: 20px;
  display: inline-flex;
  gap: 8px;
  max-width: calc(100% - 40px);
  padding: 9px 14px;
  border-radius: 30px;
  background: #fff8edee;
  color: #675038;
  font-size: 11px;
  backdrop-filter: blur(12px);
}
.dish-photo-category > span {
  color: #b79a74;
}
.dish-photo figcaption {
  position: absolute;
  bottom: 16px;
  left: 16px;
  background: #261a128c;
  color: #fff5e7;
  border-radius: 7px;
  padding: 5px 9px;
  font-size: 10px;
}
.dish-soldout-stamp {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: #241a1270;
  color: #fff9ef;
  font-size: 26px;
  letter-spacing: 5px;
}
.dish-photo-caption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 19px 2px;
  color: #9b886e;
  font-size: 12px;
  letter-spacing: 1px;
}
.dish-title-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 3px;
}
.dish-eyebrow {
  font-size: 11px;
  color: #9d744c;
  letter-spacing: 2px;
}
.dish-summary h1 {
  font-family: "Noto Serif SC", serif;
  font-size: clamp(28px, 3.2vw, 44px);
  line-height: 1.4;
  margin: 17px 0 13px;
  color: #3d3024;
  letter-spacing: 1px;
  overflow-wrap: anywhere;
}
.dish-description {
  color: #81705d;
  line-height: 1.9;
  font-size: 14px;
  white-space: pre-line;
  overflow-wrap: anywhere;
}
.dish-price-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 22px 0 26px;
  color: #a18465;
  font-size: 12px;
}
.dish-price-line > strong {
  color: #c9602c;
  font-size: 38px;
  font-weight: 650;
  line-height: 1;
}
.dish-price-line strong small {
  font-size: 20px;
  margin-right: 4px;
}
.dish-stock {
  margin-left: auto;
  color: #8b7b64;
}
.dish-stock.low {
  color: #ba652a;
}
.dish-stall-link {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 0;
  border-top: 1px solid #e7dccb;
  border-bottom: 1px solid #e7dccb;
}
.dish-stall-link > img,
.dish-stall-icon {
  width: 48px;
  height: 48px;
  border-radius: 13px;
  object-fit: cover;
  flex-shrink: 0;
}
.dish-stall-icon {
  display: grid;
  place-items: center;
  background: #f5e7d3;
  color: #b27944;
}
.dish-stall-link > span:nth-child(2) {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.dish-stall-link small {
  color: #9b8870;
  font-size: 10px;
}
.dish-stall-link strong {
  color: #514130;
  font-size: 14px;
  overflow-wrap: anywhere;
}
.dish-stall-more {
  margin-left: auto;
  color: #a97647;
  font-size: 11px;
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.dish-pickup-facts {
  display: flex;
  flex-direction: column;
  gap: 19px;
  padding: 25px 0;
}
.dish-pickup-facts > div {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.dish-fact-icon {
  flex-shrink: 0;
  color: #a48460;
  width: 25px;
  padding-top: 2px;
}
.dish-pickup-facts > div > span:nth-child(2) {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.dish-pickup-facts strong {
  font-size: 12px;
  color: #6d5a44;
  font-weight: 550;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.dish-pickup-facts small {
  font-size: 11px;
  color: #92806a;
  line-height: 1.7;
}
.dish-directions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: #b3713c;
  font-size: 11px;
  margin-left: auto;
  min-height: 44px;
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
  color: #99692f;
  font-size: 12px;
  line-height: 1.8;
  margin-bottom: 18px;
}
.dish-unavailable svg {
  flex-shrink: 0;
  margin-top: 1px;
}
.dish-purchase-controls {
  display: flex;
  gap: 14px;
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
  color: #cebfac;
}
.dish-quantity output {
  width: 22px;
  font-size: 14px;
  text-align: center;
  color: #55432f;
}
.dish-add-button {
  flex: 1;
  min-width: 0;
  gap: 9px;
  padding: 13px 18px;
  border-radius: 12px;
  font-size: 13px;
}
.dish-add-button strong {
  margin-left: auto;
  font-size: 16px;
  font-weight: 600;
}
.dish-disabled-price {
  display: flex;
  justify-content: center;
  flex-direction: column;
  gap: 4px;
  min-width: 80px;
}
.dish-disabled-price small {
  font-size: 10px;
  color: #8b7a65;
}
.dish-disabled-price strong {
  font-size: 21px;
  color: #be662e;
}
.dish-cart-line {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: space-between;
  font-size: 10px;
  color: #81725d;
  min-height: 32px;
}
.dish-cart-line > span,
.dish-cart-line > a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.dish-cart-line > a {
  color: #b3632e;
  font-size: 11px;
  min-height: 44px;
  white-space: nowrap;
}
.dish-cart-line > span svg {
  color: #698b60;
}
.dish-cart-note {
  text-align: center;
  font-size: 10px;
  color: #9b886e;
  line-height: 1.8;
  margin-top: 14px;
}
.dish-before-order {
  display: grid;
  grid-template-columns: 1.15fr 1.5fr auto;
  gap: 35px;
  align-items: center;
  margin-top: 45px;
  padding: 28px 0 8px;
  border-top: 1px solid #e6dac7;
}
.dish-before-order h2 {
  font-family: "Noto Serif SC", serif;
  font-size: 19px;
  color: #695139;
  margin-top: 10px;
}
.dish-before-order p {
  font-size: 12px;
  color: #8c7962;
  line-height: 1.9;
}
.dish-before-order > a {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 7px;
  font-size: 12px;
  color: #a26434;
  min-height: 44px;
  white-space: nowrap;
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
  gap: 55px;
}
.dish-loading > div {
  min-height: 450px;
  border-radius: 24px;
}
@media (max-width: 1023px) {
  .dish-layout {
    gap: 28px;
  }
  .dish-summary h1 {
    font-size: 29px;
  }
  .dish-description {
    font-size: 12px;
  }
  .dish-pickup-facts {
    gap: 16px;
    padding: 20px 0;
  }
  .dish-add-button {
    padding: 12px;
    gap: 6px;
  }
  .dish-add-button > svg {
    display: none;
  }
  .dish-purchase-controls {
    gap: 10px;
  }
  .dish-before-order {
    gap: 24px;
    grid-template-columns: 1fr 1.3fr;
  }
  .dish-before-order > a {
    grid-column: 2;
    margin-top: -15px;
  }
  .dish-cart-line {
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 0;
  }
}
@media (max-width: 767px) {
  .dish-page {
    padding-top: 10px;
    padding-bottom: calc(145px + env(safe-area-inset-bottom));
  }
  .dish-breadcrumb {
    gap: 12px;
    margin-bottom: 8px;
    font-size: 11px;
  }
  .dish-breadcrumb > span {
    gap: 12px;
  }
  .dish-layout {
    display: block;
  }
  .dish-photo {
    aspect-ratio: 1.1;
    border-radius: 20px;
  }
  .dish-photo-category {
    top: 14px;
    left: 14px;
    padding: 8px 11px;
    font-size: 10px;
  }
  .dish-photo figcaption {
    left: 12px;
    bottom: 12px;
    font-size: 9px;
  }
  .dish-photo-caption {
    padding: 14px 2px 23px;
    font-size: 10px;
  }
  .dish-summary h1 {
    font-size: 30px;
    margin: 14px 0 12px;
  }
  .dish-eyebrow {
    font-size: 10px;
  }
  .dish-description {
    font-size: 13px;
    line-height: 1.9;
  }
  .dish-price-line {
    margin: 20px 0 24px;
  }
  .dish-price-line > strong {
    font-size: 32px;
  }
  .dish-price-line strong small {
    font-size: 18px;
  }
  .dish-pickup-facts {
    padding: 23px 0;
    gap: 20px;
  }
  .dish-purchase-panel {
    position: fixed;
    z-index: 65;
    left: 0;
    right: 0;
    bottom: 0;
    padding: 12px 18px calc(10px + env(safe-area-inset-bottom));
    background: #fffaf2f5;
    backdrop-filter: blur(18px);
    border-top: 1px solid #e9dcc7;
    box-shadow: 0 -5px 24px #5f3e1510;
  }
  .dish-purchase-controls {
    gap: 13px;
  }
  .dish-quantity {
    min-height: 48px;
  }
  .dish-quantity button {
    min-height: 46px;
  }
  .dish-add-button {
    font-size: 12px;
    padding: 12px 15px;
  }
  .dish-add-button > svg {
    display: block;
    width: 17px;
  }
  .dish-add-button strong {
    font-size: 16px;
  }
  .dish-cart-note {
    margin-top: 9px;
    font-size: 9px;
  }
  .dish-cart-line {
    margin-top: 7px;
    align-items: center;
    flex-wrap: nowrap;
    font-size: 10px;
    min-height: 30px;
  }
  .dish-cart-line > a {
    font-size: 10px;
  }
  .dish-cart-line > span {
    gap: 3px;
  }
  .dish-before-order {
    margin-top: 25px;
    padding-top: 25px;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 14px;
  }
  .dish-before-order h2 {
    font-size: 18px;
  }
  .dish-before-order > a {
    margin-top: -5px;
  }
  .dish-loading {
    grid-template-columns: 1fr;
    gap: 25px;
  }
  .dish-loading > div {
    min-height: 320px;
  }
}
@media (max-width: 374px) {
  .dish-add-button > svg {
    display: none;
  }
  .dish-cart-line > span svg {
    display: none;
  }
}
</style>
