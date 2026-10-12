<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronRight,
  Clock3,
  Info,
  MapPin,
  Minus,
  Plus,
  RefreshCw,
  ShoppingBag,
  Store,
  Trash2,
  Undo2,
  Wallet,
} from "lucide-vue-next";
import { api, ApiError, confirmedText, money, statusText } from "../lib/api";
import type { CartItem, Product, Stall } from "../lib/types";
import { useCart } from "../stores/cart";
import { useSession } from '../stores/session';
import { listSubmissions } from '../lib/checkoutSubmission';
import PortionEditor from "../components/PortionEditor.vue";
import { normalizePortions, invalidPortions } from "../lib/portions";
import { useBrowseReturn } from "../lib/browseReturn";
import {
  productAvailable,
  productUnavailableReason,
} from "../lib/availability";

const cart = useCart();
const session = useSession();
const recoveries = computed(() => listSubmissions(session.user?.id));
const hasRecovery = (id: string) => recoveries.value.some(row => row.stallId === Number(id));
type StallState = {
  stall: Stall | null;
  error: string;
  missing: boolean;
  loading: boolean;
};
const states = ref<Record<string, StallState>>({});
const refreshing = ref(false);
const checkedAt = ref<Date | null>(null);
const undo = ref<{ id: string; rows: CartItem[]; message: string } | null>(
  null,
);
const groups = computed(() =>
  Object.entries(cart.carts)
    .filter(([, rows]) => rows.length)
    .map(([id, rows]) => ({ id, rows })),
);
useBrowseReturn({
  capture: () => ({}),
  async restore(snapshot, control) {
    const target = snapshot.anchor.match(/^\/stalls\/(\d+)(?:\/products\/(\d+))?$/);
    if (!target) return false;
    const id = target[1]!;
    if (!await control.wait(() => !!states.value[id] && !states.value[id]!.loading)) return false;
    const state = states.value[id]!;
    return !state.error && !!state.stall && (!target[2] || state.stall.products.some(product => product.id === Number(target[2])));
  },
});
const totalCount = computed(() =>
  groups.value.reduce((sum, group) => sum + cart.count(group.id), 0),
);
const timeChecked = computed(() =>
  checkedAt.value?.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }),
);
let timer: ReturnType<typeof setInterval> | undefined;
let disposed = false;
let requestSequence = 0;
let refreshSequence = 0;
const requestTimeoutMs = 10_000;
type StallRequest = {
  sequence: number;
  controller: AbortController;
  timeout?: ReturnType<typeof setTimeout>;
  promise: Promise<void>;
};
const requests = new Map<string, StallRequest>();

function loadStall(id: string): Promise<void> {
  if (disposed) return Promise.resolve();
  const pending = requests.get(id);
  if (pending) return pending.promise;
  const previous = states.value[id];
  const request: StallRequest = {
    sequence: ++requestSequence,
    controller: new AbortController(),
    promise: Promise.resolve(),
  };
  requests.set(id, request);
  const isCurrent = () =>
    !disposed && requests.get(id)?.sequence === request.sequence;
  states.value[id] = {
    stall: previous?.stall || null,
    error: "",
    missing: false,
    loading: true,
  };
  request.promise = (async () => {
    let timedOut = false;
    const timeoutMessage = "请求超时，餐袋已保留。请点击刷新餐袋重试。";
    const deadline = new Promise<never>((_, reject) => {
      request.timeout = setTimeout(() => {
        timedOut = true;
        request.controller.abort();
        reject(new Error(timeoutMessage));
      }, requestTimeoutMs);
    });
    try {
      // Bound the entire response, including a stalled body. A late response can
      // neither overwrite a retry nor keep the shared refresh button locked.
      const stall = await Promise.race([
        api<Stall>(`/stalls/${id}`, { signal: request.controller.signal }),
        deadline,
      ]);
      if (request.controller.signal.aborted) throw new Error(timeoutMessage);
      if (isCurrent())
        states.value[id] = { stall, error: "", missing: false, loading: false };
    } catch (error) {
      if (isCurrent())
        states.value[id] = {
          stall: previous?.stall || null,
          error: timedOut
            ? timeoutMessage
            : error instanceof ApiError && error.status === 404
              ? "这个摊位已不可用，餐袋中的记录仍为你保留。"
              : (error as Error).message,
          missing:
            !timedOut && error instanceof ApiError && error.status === 404,
          loading: false,
        };
    } finally {
      clearTimeout(request.timeout);
      if (requests.get(id)?.sequence === request.sequence) requests.delete(id);
    }
  })();
  return request.promise;
}
async function refresh() {
  if (disposed || refreshing.value) return;
  const sequence = ++refreshSequence;
  refreshing.value = true;
  const ids = groups.value.map((group) => group.id);
  try {
    await Promise.all(ids.map(loadStall));
    if (
      !disposed &&
      sequence === refreshSequence &&
      ids.every((id) => states.value[id]?.stall && !states.value[id]?.error)
    )
      checkedAt.value = new Date();
  } finally {
    if (!disposed && sequence === refreshSequence) refreshing.value = false;
  }
}
function onVisibility() {
  if (document.visibilityState === "visible") void refresh();
}
onMounted(() => {
  void refresh();
  timer = setInterval(onVisibility, 30_000);
  window.addEventListener("focus", onVisibility);
  document.addEventListener("visibilitychange", onVisibility);
});
onUnmounted(() => {
  disposed = true;
  refreshSequence++;
  if (timer) clearInterval(timer);
  for (const request of requests.values()) {
    clearTimeout(request.timeout);
    request.controller.abort();
  }
  requests.clear();
  window.removeEventListener("focus", onVisibility);
  document.removeEventListener("visibilitychange", onVisibility);
});
watch(
  () => groups.value.map((group) => group.id).join(","),
  () => {
    for (const { id } of groups.value)
      if (!states.value[id]) void loadStall(id);
  },
);
function current(id: string, item: CartItem): Product | undefined {
  return states.value[id]?.stall?.products.find(
    (product) => product.id === item.product.id,
  );
}
function productIssue(id: string, item: CartItem) {
  const state = states.value[id];
  if (!state?.stall || state.error || state.loading) return "";
  const product = current(id, item);
  if (!product) return "已下架或不再供应";
  if (!productAvailable(product)) return productUnavailableReason(product);
  if (cart.count(id) > 10) return "每个摊位一单合计最多 10 份，请调整数量";
  if (invalidPortions(product, item.portions, item.quantity))
    return "口味选项已变更，请重新选择后再结算";
  return "";
}
function priceChanged(id: string, item: CartItem) {
  const state = states.value[id];
  const product = current(id, item);
  return (
    !state?.error &&
    !state?.loading &&
    !!product &&
    product.price_cents !== item.product.price_cents
  );
}
function stallIssue(id: string) {
  const state = states.value[id];
  if (!state || state.loading) return "正在核对营业状态与库存…";
  if (state.error) return state.error;
  if (!state.stall) return "尚未取得摊位信息，请刷新后重试。";
  if (!state.stall.can_order && state.stall.order_unavailable_reason)
    return state.stall.order_unavailable_reason;
  if (!state.stall.transaction_enabled)
    return "仅支持线下到访，暂未开放在线点单。";
  if (!state.stall.can_order)
    return state.stall.status === "stale"
      ? "摊位位置需要商家重新确认，暂时不能下单。"
      : `摊位${statusText(state.stall.status)}，稍后再来看看。`;
  return "";
}
function canCheckout(id: string) {
  return (
    !hasRecovery(id) && !stallIssue(id) &&
    cart.items(id).length > 0 &&
    cart
      .items(id)
      .every((item) => !productIssue(id, item) && !priceChanged(id, item))
  );
}
function increaseDisabled(id: string, item: CartItem) {
  const state = states.value[id];
  return (
    !state?.stall ||
    state.loading ||
    !!state.error ||
    !state.stall.can_order ||
    !productAvailable(current(id, item)) ||
    cart.remaining(id) <= 0
  );
}
function setQuantity(id: string, item: CartItem, quantity: number) {
  if (!quantity) {
    remove(id, item);
    return;
  }
  // Quantity edits retain the selected price until the customer explicitly accepts a change.
  cart.setQuantity(id, item.product, quantity);
}
function remove(id: string, item: CartItem) {
  undo.value = {
    id,
    rows: [
      {
        product: { ...item.product },
        quantity: item.quantity,
        portions: normalizePortions(item.portions, item.quantity),
      },
    ],
    message: `已移除「${item.product.name}」`,
  };
  cart.remove(id, item.product.id);
}
function clear(id: string) {
  undo.value = {
    id,
    rows: cart.items(id).map((item) => ({
      product: { ...item.product },
      quantity: item.quantity,
      portions: normalizePortions(item.portions, item.quantity),
    })),
    message: "已清空这个摊位的餐袋",
  };
  cart.clear(id);
}
function restore() {
  if (!undo.value) return;
  for (const item of undo.value.rows) {
    // Preserve any new selection made after removal rather than adding duplicates.
    if (
      !cart
        .items(undo.value.id)
        .some((row) => row.product.id === item.product.id)
    )
      cart.setQuantity(
        undo.value.id,
        item.product,
        item.quantity,
        item.portions,
      );
  }
  undo.value = null;
  void refresh();
}
function acceptPrice(id: string, item: CartItem) {
  const product = current(id, item);
  if (product) cart.setQuantity(id, product, item.quantity);
}
</script>

<template>
  <div class="page cart-page">
    <RouterLink to="/search?type=dishes" class="cart-back"
      ><ArrowLeft :size="16" /> 继续发现好味道</RouterLink
    >
    <header class="cart-heading">
      <div>
        <span class="eyebrow">A LITTLE SOMETHING FOR LATER</span>
        <h1>我的餐袋<span class="cart-title-dot">。</span></h1>
        <p>惦记的好味道，都放在这里。</p>
      </div>
      <div v-if="groups.length" class="cart-heading-count">
        <ShoppingBag :size="23" />
        <div>
          <strong>{{ totalCount }} 件餐点</strong
          ><span>来自 {{ groups.length }} 个小摊</span>
        </div>
      </div>
    </header>

    <section v-if="recoveries.length" class="card cart-recovery" aria-label="待确认的订单">
      <h2>先确认上一笔订单</h2>
      <p>提交结果还未确认。餐袋里的新选择会保留，请先找回原订单。</p>
      <div v-for="recovery in recoveries" :key="recovery.stallId" class="cart-recovery-row">
        <span>{{ recovery.record.summary }}</span>
        <RouterLink :to="`/checkout/${recovery.stallId}`" class="btn btn-primary">确认原订单结果</RouterLink>
      </div>
    </section>
    <section
      v-if="cart.guestCount"
      class="card guest-cart-choice"
      aria-label="登录前的餐袋"
    >
      <h2>登录前还有 {{ cart.guestCount }} 份餐点</h2>
      <p>
        可由你确认后加入当前账号，再核对价格、库存与逐份备注。每个摊位合计最多 10 份，未加入的餐点会保留在登录前餐袋。
      </p>
      <button class="btn btn-primary" @click="cart.mergeGuest()">
        加入当前账号餐袋
      </button>
      <button class="btn btn-secondary" @click="cart.dismissGuest()">
        清除登录前餐袋
      </button>
    </section>
    <div v-if="undo" class="cart-undo" role="status">
      <span><Check :size="16" />{{ undo.message }}</span
      ><button type="button" @click="restore"><Undo2 :size="16" /> 撤销</button>
    </div>

    <section v-if="!groups.length" class="cart-empty card">
      <div class="cart-empty-illustration">
        <ShoppingBag :size="55" :stroke-width="1.25" /><span>先装一份喜欢</span>
      </div>
      <h2>还没决定吃什么？</h2>
      <p>
        从一份热乎的煎饼，或一杯清爽的茶开始。<br />挑好的餐点会留在这台设备的餐袋里。
      </p>
      <RouterLink to="/search?type=dishes" class="btn btn-primary"
        >去挑一份好味道 <ArrowUpRight :size="18"
      /></RouterLink>
    </section>

    <div v-else class="cart-layout">
      <div class="cart-groups" role="region" aria-label="按摊位分开的餐袋">
        <div class="cart-refresh-line">
          <span>{{
            timeChecked ? `${timeChecked} 已核对` : "以摊位最新状态为准"
          }}</span
          ><button type="button" :disabled="refreshing" @click="refresh">
            <RefreshCw
              :size="14"
              :class="{ 'cart-refreshing': refreshing }"
            />{{ refreshing ? "正在核对" : "刷新餐袋" }}
          </button>
        </div>
        <section
          v-for="group in groups"
          :key="group.id"
          class="cart-stall card"
          :data-stall-id="group.id"
          :aria-label="`${states[group.id]?.stall?.name || '摊位'}的餐袋`"
        >
          <header class="cart-stall-heading">
            <RouterLink
              v-if="!states[group.id]?.missing"
              :to="`/stalls/${group.id}`"
              class="cart-stall-link"
              ><img
                v-if="states[group.id]?.stall?.image"
                :src="states[group.id]?.stall?.image"
                alt="" /><span v-else class="cart-stall-placeholder"
                ><Store :size="22" /></span
              ><span
                ><h2>
                  {{
                    states[group.id]?.stall?.name ||
                    (states[group.id]?.error
                      ? "暂时无法读取摊位"
                      : "正在查找小摊…")
                  }}
                </h2>
                <span
                  v-if="states[group.id]?.stall"
                  class="cart-stall-status"
                  :class="{
                    open:
                      states[group.id]?.stall?.can_order &&
                      !states[group.id]?.error,
                  }"
                  ><i></i
                  >{{
                    states[group.id]?.error
                      ? "状态待核对"
                      : statusText(states[group.id]!.stall!.status)
                  }}<span class="cart-status-divider">·</span
                  >{{ cart.count(group.id) }} 件</span
                ></span
              ><ChevronRight :size="17"
            /></RouterLink>
            <div v-else class="cart-missing-stall">
              <Store :size="23" />
              <h2>摊位已不可用</h2>
            </div>
            <button
              type="button"
              class="cart-clear"
              :aria-label="`清空${states[group.id]?.stall?.name || '这个摊位'}的餐袋`"
              @click="clear(group.id)"
            >
              <Trash2 :size="15" /><span>清空</span>
            </button>
          </header>
          <p
            v-if="stallIssue(group.id)"
            class="cart-stall-alert"
            :class="{ 'is-loading': states[group.id]?.loading }"
          >
            <Info :size="15" />{{ stallIssue(group.id) }}
          </p>
          <div class="cart-items">
            <article
              v-for="item in group.rows"
              :key="item.product.id"
              class="cart-item"
              :data-product-id="item.product.id"
            >
              <RouterLink
                :to="`/stalls/${group.id}/products/${item.product.id}`"
                class="cart-food-photo"
                :aria-label="`查看${item.product.name}图片与详情`"
                ><img
                  v-if="current(group.id, item)?.image || item.product.image"
                  :src="current(group.id, item)?.image || item.product.image"
                  :alt="item.product.name"
                  loading="lazy" /><ShoppingBag v-else :size="30"
              /></RouterLink>
              <div class="cart-food-content">
                <h3>
                  <RouterLink
                    :to="`/stalls/${group.id}/products/${item.product.id}`"
                    :aria-label="`查看${item.product.name}详情`"
                    >{{ current(group.id, item)?.name || item.product.name
                    }}<ChevronRight :size="13"
                  /></RouterLink>
                </h3>
                <p class="cart-food-description">
                  {{
                    current(group.id, item)?.description ||
                    item.product.description ||
                    "口味与制作信息可向摊主确认"
                  }}
                </p>
                <div class="cart-food-price">
                  <strong>¥{{ money(item.product.price_cents) }}</strong
                  ><span>/ 份</span>
                </div>
              </div>
              <div class="cart-item-actions">
                <div class="cart-quantity">
                  <button
                    type="button"
                    :aria-label="`减少${item.product.name}`"
                    @click="setQuantity(group.id, item, item.quantity - 1)"
                  >
                    <Minus :size="15" /></button
                  ><output :aria-label="`${item.product.name}份数`">{{
                    item.quantity
                  }}</output
                  ><button
                    type="button"
                    :aria-label="`增加${item.product.name}`"
                    :disabled="increaseDisabled(group.id, item)"
                    @click="setQuantity(group.id, item, item.quantity + 1)"
                  >
                    <Plus :size="15" />
                  </button>
                </div>
                <button
                  type="button"
                  class="cart-remove"
                  :aria-label="`移除${item.product.name}`"
                  @click="remove(group.id, item)"
                >
                  移除
                </button>
              </div>
              <PortionEditor
                :product="current(group.id, item) || item.product"
                :quantity="item.quantity"
                :model-value="item.portions"
                :disabled="
                  !current(group.id, item) ||
                  !!states[group.id]?.error ||
                  states[group.id]?.loading
                "
                @update:model-value="
                  cart.setQuantity(
                    group.id,
                    item.product,
                    item.quantity,
                    $event,
                  )
                "
              />
              <div
                v-if="
                  productIssue(group.id, item) || priceChanged(group.id, item)
                "
                class="cart-item-notices"
              >
                <p v-if="productIssue(group.id, item)" class="cart-item-alert">
                  <Info :size="14" />{{ productIssue(group.id, item) }}
                </p>
                <div
                  v-if="priceChanged(group.id, item)"
                  class="cart-price-change"
                >
                  <span
                    >价格有变化：¥{{ money(item.product.price_cents) }} →
                    <strong
                      >¥{{
                        money(current(group.id, item)!.price_cents)
                      }}</strong
                    ></span
                  ><button
                    type="button"
                    :aria-label="`确认${item.product.name}新价格`"
                    @click="acceptPrice(group.id, item)"
                  >
                    确认新价格 <Check :size="13" />
                  </button>
                </div>
              </div>
            </article>
          </div>
          <div v-if="states[group.id]?.stall" class="cart-pickup">
            <MapPin :size="15" /><span
              >{{ states[group.id]!.stall!.address
              }}<small>{{
                confirmedText(states[group.id]!.stall!.last_confirmed_at)
              }}</small></span
            ><RouterLink :to="`/stalls/${group.id}`"
              >查看小摊<ChevronRight :size="13"
            /></RouterLink>
          </div>
          <footer class="cart-stall-footer">
            <div>
              <span>本摊餐袋小计</span
              ><strong>¥{{ money(cart.total(group.id)) }}</strong
              ><small
                v-if="group.rows.some((item) => priceChanged(group.id, item))"
                >含待确认的原选价格</small
              >
            </div>
            <RouterLink
              v-if="canCheckout(group.id)"
              :to="`/checkout/${group.id}`"
              class="btn btn-primary"
              :aria-label="`去结算${states[group.id]!.stall!.name}的餐点`"
              >去结算<ArrowUpRight :size="17" /></RouterLink
            ><button v-else type="button" class="btn btn-primary" disabled>
              {{
                hasRecovery(group.id)
                  ? "请先确认原订单"
                  : states[group.id]?.loading
                  ? "核对中…"
                  : group.rows.some((item) => priceChanged(group.id, item))
                    ? "请先确认价格"
                    : "暂不可结算"
              }}
            </button>
          </footer>
        </section>
      </div>
      <aside class="cart-guide">
        <div class="cart-guide-card">
          <span class="eyebrow">GOOD FOOD, YOUR PACE</span>
          <h2>慢慢挑，<br />好好吃一餐。</h2>
          <p>每个小摊都有自己的烟火，<br />每份餐袋也会单独结算。</p>
          <div class="cart-guide-separator"></div>
          <div class="cart-guide-fact">
            <Store :size="19" />
            <div>
              <strong>一摊一单</strong>
              <p>结算一个小摊后，其他餐袋继续保留。</p>
            </div>
          </div>
          <div class="cart-guide-fact">
            <Wallet :size="19" />
            <div>
              <strong>结算时选择取餐方式</strong>
              <p>自取在出餐后付款；已开放的商家配送须先完成微信付款。</p>
            </div>
          </div>
          <div class="cart-guide-fact">
            <Clock3 :size="19" />
            <div>
              <strong>餐袋不会预留库存</strong>
              <p>商家可能更新价格与库存，提交时会再次核对。</p>
            </div>
          </div>
        </div>
        <p class="cart-local-note">
          <Info :size="14" />餐袋保存在当前浏览器；订单提交后保存到账号。
        </p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.cart-recovery { margin-bottom: 20px; padding: 20px; background: #fff5e7; border-color: #e6ca9e; }
.cart-recovery h2 { margin: 0 0 8px; font-size: 18px; }
.cart-recovery p { color: #745c45; font-size: 13px; line-height: 1.7; }
.cart-recovery-row { display: flex; gap: 16px; align-items: center; justify-content: space-between; margin-top: 14px; }
.cart-recovery-row span { min-width: 0; overflow-wrap: anywhere; font-size: 13px; }
.cart-recovery-row .btn { flex-shrink: 0; }
@media (max-width: 600px) { .cart-recovery { padding: 16px; } .cart-recovery-row { align-items: stretch; flex-direction: column; gap: 10px; } }
.guest-cart-choice {
  padding: 24px;
  margin-bottom: 24px;
}
.guest-cart-choice h2 {
  font-size: 20px;
  margin: 0 0 12px;
}
.guest-cart-choice p {
  margin: 0 0 16px;
  line-height: 1.7;
}
.guest-cart-choice .btn {
  margin: 0 12px 8px 0;
}
.cart-page {
  padding-top: 24px;
  padding-bottom: 46px;
}
.cart-back {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  font-size: 13px;
  color: #746858;
}
.cart-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 20px 0 32px;
}
.cart-heading h1 {
  margin: 7px 0 9px;
  font-size: 40px;
  letter-spacing: -1.5px;
}
.cart-title-dot {
  color: #df6228;
}
.cart-heading p {
  font-size: 14px;
  color: #80715f;
}
.cart-heading-count {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff3e1;
  border-radius: 18px;
  padding: 20px 26px;
  color: #9e4e25;
}
.cart-heading-count strong,
.cart-heading-count span {
  display: block;
}
.cart-heading-count strong {
  font-size: 16px;
}
.cart-heading-count span {
  font-size: 12px;
  margin-top: 4px;
  color: #89705c;
}
.cart-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 290px;
  gap: 32px;
  align-items: start;
}
.cart-groups {
  min-width: 0;
}
.cart-refresh-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #867765;
  font-size: 12px;
  margin: -5px 0 12px;
}
.cart-refresh-line button {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 0 0 12px;
  color: #ad542a;
}
.cart-stall {
  overflow: hidden;
  margin-bottom: 20px;
}
.cart-stall-heading {
  padding: 23px 24px 19px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.cart-stall-link {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}
.cart-stall-link > img,
.cart-stall-placeholder {
  width: 48px;
  height: 48px;
  object-fit: cover;
  border-radius: 13px;
  flex-shrink: 0;
}
.cart-stall-placeholder {
  display: grid;
  place-items: center;
  color: #bb733f;
  background: #fff1df;
}
.cart-stall-link > span {
  min-width: 0;
}
.cart-stall-heading h2 {
  font-size: 18px;
  overflow-wrap: anywhere;
}
.cart-stall-status {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #8b735e;
  margin-top: 4px;
}
.cart-stall-status i {
  width: 5px;
  height: 5px;
  background: currentColor;
  border-radius: 50%;
}
.cart-stall-status.open {
  color: #50775a;
}
.cart-status-divider {
  margin: 0 3px;
}
.cart-clear {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 44px;
  min-height: 44px;
  color: #91816e;
  font-size: 12px;
  flex-shrink: 0;
}
.cart-missing-stall {
  display: flex;
  gap: 12px;
  align-items: center;
  color: #756756;
}
.cart-stall-alert {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 24px;
  font-size: 12px;
  line-height: 1.7;
  color: #985024;
  background: #fff5e9;
}
.cart-stall-alert svg {
  margin-top: 3px;
}
.cart-stall-alert.is-loading {
  color: #807361;
  background: #faf7f2;
}
.cart-items {
  padding: 0 24px;
}
.cart-item {
  display: grid;
  grid-template-columns: 98px minmax(0, 1fr) auto;
  column-gap: 18px;
  row-gap: 12px;
  padding: 22px 0;
  border-bottom: 1px solid #f0e9df;
}
.cart-item:last-child {
  border-bottom: 0;
}
.cart-food-photo {
  width: 98px;
  height: 104px;
  border-radius: 14px;
  background: #fbefdf;
  overflow: hidden;
  display: grid;
  place-items: center;
  color: #b88e64;
}
.cart-food-photo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 200ms ease;
}
.cart-food-photo:hover img {
  transform: scale(1.04);
}
.cart-food-content {
  align-self: center;
  min-width: 0;
}
.cart-food-content h3 {
  font-size: 16px;
  line-height: 1.5;
}
.cart-food-content h3 a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 44px;
  overflow-wrap: anywhere;
}
.cart-food-content h3 svg {
  color: #a99883;
}
.cart-food-description {
  font-size: 11px;
  color: #8a7b68;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.cart-food-price {
  display: flex;
  gap: 7px;
  align-items: baseline;
  margin-top: 7px;
}
.cart-food-price strong {
  color: #c05521;
  font-size: 18px;
  font-weight: 600;
}
.cart-food-price > span {
  color: #8b7d6b;
  font-size: 10px;
}
.cart-item-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  align-self: center;
  gap: 3px;
}
.cart-quantity {
  display: flex;
  align-items: center;
  border: 1px solid #efdfca;
  border-radius: 12px;
  overflow: hidden;
}
.cart-quantity button {
  height: 44px;
  width: 44px;
  display: grid;
  place-items: center;
  color: #a6542b;
}
.cart-quantity button:hover:not(:disabled) {
  background: #fff0df;
}
.cart-quantity output {
  text-align: center;
  min-width: 20px;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}
.cart-remove {
  color: #8d7e6d;
  font-size: 11px;
  min-height: 44px;
  min-width: 44px;
}
.cart-item-notices {
  grid-column: 1 / -1;
}
.cart-item-alert {
  color: #a5462c;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
}
.cart-price-change {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 5px;
  background: #fff5e8;
  border-radius: 9px;
  padding: 2px 12px;
  color: #94562c;
  font-size: 12px;
  flex-wrap: wrap;
}
.cart-price-change button {
  display: flex;
  align-items: center;
  gap: 5px;
  min-height: 44px;
  font-size: 12px;
  color: #af471c;
  font-weight: 600;
}
.cart-pickup {
  background: #faf8f4;
  margin: 0 24px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 13px 14px;
  font-size: 12px;
  color: #76624e;
}
.cart-pickup > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.cart-pickup small {
  display: block;
  font-size: 10px;
  color: #887969;
  margin-top: 4px;
}
.cart-pickup > a {
  display: flex;
  align-items: center;
  min-height: 44px;
  margin-left: auto;
  gap: 3px;
  flex-shrink: 0;
  color: #a86a3e;
  font-size: 11px;
}
.cart-stall-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 21px 24px 23px;
}
.cart-stall-footer > div {
  display: flex;
  align-items: baseline;
  gap: 9px;
  flex-wrap: wrap;
}
.cart-stall-footer > div > span {
  color: #796b5b;
  font-size: 12px;
}
.cart-stall-footer strong {
  font-size: 23px;
  color: #bd5422;
  font-weight: 600;
}
.cart-stall-footer small {
  flex-basis: 100%;
  color: #967555;
  font-size: 11px;
}
.cart-stall-footer .btn {
  min-height: 48px;
  min-width: 128px;
  border-radius: 13px;
  font-size: 13px;
  gap: 14px;
  white-space: nowrap;
}
.cart-guide {
  position: sticky;
  top: 24px;
  padding-top: 52px;
}
.cart-guide-card {
  padding: 30px 25px;
  background: #f6eddf;
  border-radius: 20px;
  position: relative;
  overflow: hidden;
}
.cart-guide-card:after {
  content: "";
  position: absolute;
  width: 130px;
  height: 130px;
  border: 1px solid #e1c7a570;
  border-radius: 50%;
  right: -55px;
  top: -55px;
  pointer-events: none;
}
.cart-guide-card .eyebrow {
  font-size: 9px;
  color: #986844;
}
.cart-guide-card h2 {
  font-family: Georgia, "Songti SC", SimSun, serif;
  font-weight: 500;
  font-size: 29px;
  line-height: 1.65;
  margin: 17px 0 13px;
}
.cart-guide-card > p {
  color: #8b745d;
  font-size: 12px;
}
.cart-guide-separator {
  height: 1px;
  background: #e7d9c6;
  margin: 25px 0;
}
.cart-guide-fact {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  margin-top: 22px;
  color: #a66b3e;
}
.cart-guide-fact strong {
  font-size: 12px;
  font-weight: 600;
  color: #65513c;
}
.cart-guide-fact p {
  font-size: 11px;
  color: #84715d;
  line-height: 1.8;
  margin-top: 4px;
}
.cart-local-note {
  display: flex;
  gap: 7px;
  padding: 17px 8px;
  color: #897a66;
  font-size: 11px;
  line-height: 1.8;
}
.cart-local-note svg {
  margin-top: 3px;
}
.cart-undo {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 17px;
  margin-bottom: 15px;
  border: 1px solid #dce7d8;
  background: #f1f6ed;
  color: #4b6b41;
  border-radius: 12px;
  font-size: 12px;
}
.cart-undo > span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  overflow-wrap: anywhere;
}
.cart-undo button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 44px;
  flex-shrink: 0;
  font-weight: 600;
}
.cart-empty {
  min-height: 470px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px 20px;
}
.cart-empty-illustration {
  width: 137px;
  height: 137px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  background: #fff1de;
  color: #c88243;
  border-radius: 44% 55% 49% 51%;
  margin-bottom: 26px;
  transform: rotate(-5deg);
}
.cart-empty-illustration span {
  font-size: 10px;
  letter-spacing: 2px;
}
.cart-empty h2 {
  font-size: 23px;
  margin-bottom: 10px;
}
.cart-empty p {
  color: #897861;
  font-size: 13px;
}
.cart-empty .btn {
  margin-top: 25px;
  gap: 14px;
}
.cart-refreshing {
  animation: cart-spin 1s linear infinite;
}
@keyframes cart-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 1050px) {
  .cart-layout {
    grid-template-columns: minmax(0, 1fr) 240px;
    gap: 22px;
  }
  .cart-guide-card {
    padding: 25px 20px;
  }
  .cart-item {
    grid-template-columns: 80px minmax(0, 1fr) auto;
    column-gap: 12px;
  }
  .cart-food-photo {
    width: 80px;
    height: 93px;
  }
}
@media (max-width: 800px) {
  .cart-page { padding-bottom: calc(104px + env(safe-area-inset-bottom)); }
  .cart-stall-footer .btn { scroll-margin-block: 100px; }
  .cart-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: 7px;
  }
  .cart-guide {
    position: static;
    padding-top: 0;
  }
  .cart-guide-card {
    display: none;
  }
  .cart-local-note {
    padding: 6px 4px 15px;
  }
  .cart-heading {
    padding: 10px 0 23px;
  }
  .cart-heading h1 {
    font-size: 34px;
  }
  .cart-heading-count {
    padding: 14px 18px;
  }
}
@media (max-width: 520px) {
  .cart-page {
    padding-top: 14px;
  }
  .cart-heading {
    gap: 12px;
    padding: 8px 0 14px;
  }
  .cart-heading .eyebrow {
    display: none;
  }
  .cart-heading h1 {
    font-size: 26px;
    margin: 0;
  }
  .cart-heading p {
    display: none;
  }
  .cart-heading-count {
    padding: 11px;
    gap: 7px;
    border-radius: 13px;
  }
  .cart-heading-count > svg {
    display: none;
  }
  .cart-heading-count strong {
    font-size: 12px;
    white-space: nowrap;
  }
  .cart-heading-count span {
    font-size: 10px;
    white-space: nowrap;
  }
  .cart-stall-heading {
    padding: 17px 15px 15px;
  }
  .cart-stall-heading h2 {
    font-size: 16px;
  }
  .cart-stall-link {
    gap: 9px;
  }
  .cart-stall-link > img,
  .cart-stall-placeholder {
    width: 41px;
    height: 41px;
    border-radius: 11px;
  }
  .cart-clear {
    gap: 3px;
    font-size: 11px;
  }
  .cart-clear > svg {
    display: none;
  }
  .cart-stall-alert {
    padding: 10px 15px;
    font-size: 11px;
  }
  .cart-items {
    padding: 0 15px;
  }
  .cart-item {
    grid-template-columns: 72px minmax(0, 1fr);
    column-gap: 12px;
    row-gap: 0;
    padding: 17px 0;
  }
  .cart-food-photo {
    width: 72px;
    height: 72px;
    grid-row: 1 / 3;
    border-radius: 11px;
  }
  .cart-food-content h3 {
    font-size: 14px;
  }
  .cart-food-content h3 a {
    min-height: 44px;
    padding-bottom: 3px;
  }
  .cart-food-description {
    font-size: 12px;
  }
  .cart-food-price {
    margin-top: 4px;
  }
  .cart-food-price strong {
    font-size: 17px;
  }
  .cart-item-actions {
    grid-column: 2;
    flex-direction: row-reverse;
    justify-content: space-between;
    align-items: center;
    margin-top: 10px;
    gap: 5px;
  }
  .cart-quantity {
    border-radius: 10px;
  }
  .cart-quantity output {
    min-width: 18px;
  }
  .cart-remove {
    text-align: left;
    font-size: 12px;
  }
  .cart-item-notices {
    padding-top: 12px;
  }
  .cart-pickup {
    margin: 0 15px;
    padding: 10px;
    gap: 6px;
    font-size: 11px;
  }
  .cart-pickup > a {
    font-size: 10px;
  }
  .cart-stall-footer {
    padding: 18px 15px;
    gap: 10px;
  }
  .cart-stall-footer > div {
    gap: 5px;
  }
  .cart-stall-footer > div > span {
    font-size: 11px;
  }
  .cart-stall-footer strong {
    font-size: 21px;
  }
  .cart-stall-footer .btn {
    min-width: 115px;
    min-height: 46px;
    font-size: 12px;
    gap: 9px;
  }
  .cart-undo {
    padding: 4px 12px;
    font-size: 11px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .cart-food-photo img {
    transition: none;
  }
  .cart-refreshing {
    animation: none;
  }
}
</style>
