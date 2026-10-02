<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  MapPin,
  RefreshCw,
  ShoppingBag,
  Store,
  X,
} from "lucide-vue-next";
import { api, money } from "../lib/api";
import { notify } from "../lib/notify";
import {
  reorderRows,
  reorderSignature,
  reorderUnavailableReason,
} from "../lib/reorder";
import type { Order, Stall } from "../lib/types";
import { useCart } from "../stores/cart";
import { normalizePortions } from "../lib/portions";
import PortionSummary from "./PortionSummary.vue";

const props = defineProps<{ order: Order }>();
const emit = defineEmits<{ close: [] }>();
const router = useRouter();
const cart = useCart();
const dialog = ref<HTMLDialogElement>();
const stall = ref<Stall | null>(null);
const loading = ref(true);
const checking = ref(false);
const error = ref("");
const changed = ref(false);
let disposed = false;
const rows = computed(() =>
  stall.value
    ? reorderRows(props.order, stall.value, cart.items(props.order.stall_id))
    : [],
);
const count = computed(() => rows.value.reduce((sum, row) => sum + row.add, 0));
const total = computed(() =>
  rows.value.reduce(
    (sum, row) => sum + row.add * (row.product?.price_cents || 0),
    0,
  ),
);
const merged = computed(() =>
  rows.value.some((row) => row.add && row.existing),
);
const draftPriceChanged = computed(() =>
  rows.value.some((row) => row.add && row.draftPriceChanged),
);
const eligible = computed(() =>
  ["completed", "cancelled", "rejected"].includes(props.order.status),
);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const current = await api<Stall>(`/stalls/${props.order.stall_id}`);
    if (!disposed) stall.value = current;
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}
function close() {
  dialog.value?.close();
}
async function confirm() {
  if (
    checking.value ||
    !eligible.value ||
    !stall.value?.can_order ||
    !count.value
  )
    return;
  checking.value = true;
  error.value = "";
  const reviewed = reorderSignature(stall.value, rows.value);
  try {
    // A merchant may change their menu while the customer is reviewing it.
    const current = await api<Stall>(`/stalls/${props.order.stall_id}`);
    if (disposed || !dialog.value?.open) return;
    const nextRows = reorderRows(
      props.order,
      current,
      cart.items(props.order.stall_id),
    );
    stall.value = current;
    if (reviewed !== reorderSignature(current, nextRows)) {
      changed.value = true;
      return;
    }
    if (!current.can_order) return;
    const addedCount = nextRows.reduce((sum, row) => sum + row.add, 0);
    for (const row of nextRows) {
      if (row.product && row.add) {
        const existing = cart
          .items(current.id)
          .find((item) => item.product.id === row.product!.id);
        cart.setQuantity(current.id, row.product, row.existing + row.add, [
          ...normalizePortions(existing?.portions, row.existing),
          ...row.portions,
        ]);
      }
    }
    notify(`已将 ${addedCount} 份餐点加入餐袋，请确认后分别结算`, "success");
    close();
    await router.push("/cart");
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    checking.value = false;
  }
}
onMounted(() => {
  dialog.value?.showModal();
  void load();
});
onUnmounted(() => {
  disposed = true;
});
</script>

<template>
  <dialog
    ref="dialog"
    class="reorder-dialog"
    aria-labelledby="reorder-title"
    aria-describedby="reorder-description"
    @close="emit('close')"
    @click.self="close"
  >
    <div class="reorder-shell">
      <header class="reorder-header">
        <span class="reorder-symbol"><RefreshCw :size="23" /></span>
        <button
          class="reorder-close"
          aria-label="关闭再来一单"
          @click="close"
          autofocus
        >
          <X :size="21" />
        </button>
        <span class="eyebrow">A TASTE WORTH RETURNING TO</span>
        <h2 id="reorder-title">再来一单</h2>
        <p id="reorder-description">把想念的味道放回餐袋，先看看今天的菜单。</p>
      </header>
      <div class="reorder-body">
        <div v-if="loading" class="reorder-loading" role="status">
          <span class="spinner"></span>
          <p>正在核对菜单、价格与库存…</p>
        </div>
        <div v-if="error" class="reorder-alert reorder-error" role="alert">
          <AlertCircle :size="19" />
          <div>
            <strong>暂时无法核对餐点</strong>
            <p>{{ error }}</p>
            <button
              class="btn btn-secondary"
              :disabled="loading || checking"
              @click="load"
            >
              重新加载
            </button>
          </div>
        </div>
        <template v-if="stall && !loading">
          <div class="reorder-stall">
            <Store :size="18" /><strong>{{ stall.name }}</strong
            ><span>当前菜单</span>
          </div>
          <p v-if="!eligible" class="reorder-alert" role="status">
            这份订单还在进行中，结束后可以再来一单。
          </p>
          <div v-else-if="!stall.can_order" class="reorder-alert" role="status">
            <AlertCircle :size="19" />
            <p>{{ reorderUnavailableReason(stall) }}</p>
          </div>
          <div v-if="changed" class="reorder-alert" role="status">
            <RefreshCw :size="18" />
            <p>菜单、库存或餐袋发生变化，已更新下方明细，请重新确认。</p>
          </div>
          <ul class="reorder-items">
            <li
              v-for="row in rows"
              :key="row.previous.product_id"
              class="reorder-item"
              :class="{ 'reorder-skipped': !row.add }"
              :data-product-id="row.previous.product_id"
            >
              <img
                :src="row.product?.image || row.previous.image"
                :alt="row.product?.name || row.previous.name"
              />
              <div class="reorder-item-content">
                <h3>
                  <RouterLink
                    v-if="row.product"
                    :to="`/stalls/${stall.id}/products/${row.product.id}`"
                    @click="close"
                    >{{ row.product.name }}</RouterLink
                  ><span v-else>{{ row.previous.name }}</span>
                </h3>
                <div class="reorder-price">
                  <strong v-if="row.product"
                    >现价 ¥{{ money(row.product.price_cents) }}</strong
                  ><span v-if="row.priceChanged" class="reorder-price-change"
                    >上次 ¥{{ money(row.previous.unit_price_cents) }}</span
                  ><span v-else-if="!row.product"
                    >上次 ¥{{ money(row.previous.unit_price_cents) }}</span
                  >
                </div>
                <p class="reorder-quantity">
                  上次 {{ row.previous.quantity }} 份<span v-if="row.existing">
                    · 餐袋已有 {{ row.existing }} 份</span
                  >
                </p>
                <p v-if="row.reason" class="reorder-row-reason">
                  {{ row.reason }}
                </p>
                <PortionSummary :portions="row.portions" />
                <p
                  v-if="row.tastesChanged"
                  class="reorder-row-reason"
                  role="alert"
                >
                  原选口味已变更。加入后请在餐袋重新选择，确认前不能结算。
                </p>
                <p v-if="row.add && row.existing" class="reorder-merge">
                  合并后 {{ row.existing + row.add }} 份
                </p>
              </div>
              <span class="reorder-add-count" :class="{ skipped: !row.add }">{{
                row.add ? `+${row.add} 份` : "跳过"
              }}</span>
            </li>
          </ul>
          <div v-if="!count && stall.can_order" class="reorder-empty">
            <ShoppingBag :size="26" /><strong>这次暂时没有可添加的餐点</strong>
            <p>
              可能已经售罄、下架，或餐袋数量达到当前上限。去菜单看看其他好味道吧。
            </p>
          </div>
          <div v-if="count && stall.can_order" class="reorder-summary">
            <Check :size="17" />
            <p>
              {{
                merged
                  ? "将与餐袋中的同款合并，其他餐点继续保留。"
                  : "仅添加上方可售餐点，餐袋中的其他餐点继续保留。"
              }}<span v-if="draftPriceChanged"
                >餐袋中同款的价格也会同步为上述现价。</span
              >
            </p>
          </div>
          <p class="reorder-location">
            <MapPin :size="16" /><span
              >当前取餐位置：{{ stall.address
              }}<small
                >正式下单时会再次确认价格、库存和取餐方式，配送交接点需重新选择。</small
              ></span
            >
          </p>
        </template>
      </div>
      <footer class="reorder-footer">
        <template v-if="stall?.can_order && count && eligible && !loading">
          <div class="reorder-total">
            <span>本次加 {{ count }} 份 <small>按现价</small></span
            ><strong>¥{{ money(total) }}</strong>
          </div>
          <p>现在只加入餐袋，在结算页提交后才会生成订单。</p>
          <button
            class="btn btn-primary reorder-confirm"
            :disabled="checking || !!error"
            @click="confirm"
          >
            <ShoppingBag :size="18" />{{
              checking ? "正在复核…" : "确认加入餐袋"
            }}<ArrowUpRight :size="17" />
          </button>
        </template>
        <RouterLink
          v-else-if="!loading"
          :to="`/stalls/${order.stall_id}`"
          class="btn btn-primary reorder-confirm"
          @click="close"
          >去看看当前菜单 <ArrowUpRight :size="17"
        /></RouterLink>
      </footer>
    </div>
  </dialog>
</template>

<style scoped>
.reorder-dialog {
  width: min(560px, calc(100vw - 32px));
  max-height: min(880px, calc(100dvh - 48px));
  padding: 0;
  border: 1px solid #e9dfd0;
  border-radius: 24px;
  background: #fffcf6;
  color: #3d3429;
  box-shadow: 0 24px 90px #30241635;
  overflow: hidden;
}
.reorder-dialog::backdrop {
  background: #2e261c80;
  backdrop-filter: blur(5px);
}
.reorder-shell {
  display: flex;
  flex-direction: column;
  max-height: min(880px, calc(100dvh - 48px));
}
.reorder-header {
  padding: 27px 29px 21px;
  border-bottom: 1px solid #eee2d3;
  background: linear-gradient(135deg, #fff5e5, #fffaf2);
  position: relative;
  flex-shrink: 0;
}
.reorder-symbol {
  display: grid;
  place-items: center;
  width: 45px;
  height: 45px;
  border-radius: 15px;
  color: #ba622e;
  background: #fbe2c9;
  margin-bottom: 17px;
}
.reorder-close {
  position: absolute;
  right: 15px;
  top: 15px;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border: 0;
  border-radius: 50%;
  background: #ffffff8c;
  color: #766651;
}
.reorder-header .eyebrow {
  font-size: 9px;
  letter-spacing: 1.6px;
  color: #96694b;
}
.reorder-header h2 {
  font-size: 25px;
  margin: 9px 0;
}
.reorder-header p {
  font-size: 13px;
  line-height: 1.7;
  color: #7d6e5c;
  margin: 0;
}
.reorder-body {
  padding: 20px 29px;
  overflow: auto;
  min-height: 0;
}
.reorder-loading {
  text-align: center;
  padding: 34px 0;
  color: #80715f;
  font-size: 13px;
}
.reorder-stall {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  margin-bottom: 16px;
}
.reorder-stall svg {
  color: #b87641;
}
.reorder-stall span {
  margin-left: auto;
  font-size: 11px;
  color: #83735f;
}
.reorder-alert {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 13px;
  border-radius: 12px;
  background: #fff0d9;
  color: #8f5e22;
  font-size: 12px;
  line-height: 1.7;
  margin: 0 0 15px;
}
.reorder-alert svg {
  flex: none;
  margin-top: 2px;
}
.reorder-alert p {
  margin: 0;
}
.reorder-alert strong {
  font-size: 13px;
}
.reorder-error {
  background: #fff0e8;
  color: #9e472d;
}
.reorder-error .btn {
  margin-top: 10px;
  min-height: 44px;
}
.reorder-items {
  list-style: none;
  padding: 0;
  margin: 0;
}
.reorder-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 17px 0;
  border-bottom: 1px solid #eee5d9;
}
.reorder-item:first-child {
  padding-top: 5px;
}
.reorder-item img {
  height: 66px;
  width: 66px;
  flex: none;
  border-radius: 12px;
  object-fit: cover;
}
.reorder-item-content {
  flex: 1;
  min-width: 0;
}
.reorder-item h3 {
  font-size: 14px;
  line-height: 1.55;
  margin: 0 0 5px;
  overflow-wrap: anywhere;
}
.reorder-item h3 a {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  margin-top: -11px;
  margin-bottom: -7px;
}
.reorder-price {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.reorder-price strong {
  color: #ba632e;
  font-weight: 600;
}
.reorder-price span {
  font-size: 11px;
  color: #8b7b68;
}
.reorder-price-change {
  text-decoration: line-through;
  text-decoration-color: #b4a28e;
}
.reorder-quantity {
  font-size: 11px;
  color: #857560;
  line-height: 1.7;
  margin: 5px 0 0;
}
.reorder-add-count {
  flex: none;
  background: #fbead6;
  padding: 5px 8px;
  border-radius: 7px;
  color: #a75c2c;
  font-weight: 600;
  font-size: 11px;
  white-space: nowrap;
  margin-top: 2px;
}
.reorder-add-count.skipped {
  background: #eee9e1;
  color: #7f776d;
}
.reorder-skipped img {
  filter: saturate(0.5);
}
.reorder-row-reason {
  font-size: 11px;
  line-height: 1.7;
  color: #986a33;
  margin: 5px 0 0;
}
.reorder-merge {
  color: #6a7753;
  font-size: 11px;
  margin: 5px 0 0;
}
.reorder-summary {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  background: #f1f3e8;
  color: #667247;
  padding: 12px;
  border-radius: 11px;
  margin-top: 17px;
  font-size: 11px;
  line-height: 1.8;
}
.reorder-summary svg {
  flex: none;
  margin-top: 2px;
}
.reorder-summary p {
  margin: 0;
}
.reorder-summary span {
  display: block;
}
.reorder-location {
  display: flex;
  gap: 8px;
  font-size: 11px;
  color: #83715a;
  line-height: 1.8;
  margin: 18px 0 0;
}
.reorder-location svg {
  flex: none;
  margin-top: 2px;
}
.reorder-location small {
  display: block;
  font-size: 10px;
  color: #918471;
}
.reorder-empty {
  display: flex;
  align-items: center;
  flex-direction: column;
  text-align: center;
  padding: 24px 8px 3px;
  color: #958065;
  gap: 9px;
}
.reorder-empty strong {
  font-size: 14px;
}
.reorder-empty p {
  font-size: 12px;
  line-height: 1.8;
  margin: 0;
}
.reorder-footer {
  padding: 18px 29px 24px;
  border-top: 1px solid #e9dfd0;
  background: #fffaf3;
  flex-shrink: 0;
}
.reorder-total {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 14px;
}
.reorder-total small {
  font-size: 11px;
  margin-left: 5px;
  color: #8c7b64;
}
.reorder-total strong {
  font-size: 25px;
  color: #bf6631;
}
.reorder-footer p {
  font-size: 11px;
  color: #897a65;
  line-height: 1.7;
  margin: 7px 0 14px;
}
.reorder-confirm {
  width: 100%;
  min-height: 48px;
  font-size: 13px;
}
.reorder-footer:empty {
  display: none;
}
@media (max-width: 600px) {
  .reorder-dialog {
    width: calc(100vw - 24px);
    border-radius: 20px;
    max-height: calc(100dvh - 24px);
  }
  .reorder-shell {
    max-height: calc(100dvh - 24px);
  }
  .reorder-header {
    padding: 22px 20px 18px;
  }
  .reorder-symbol {
    width: 39px;
    height: 39px;
    margin-bottom: 13px;
  }
  .reorder-header h2 {
    font-size: 23px;
  }
  .reorder-header p {
    font-size: 12px;
  }
  .reorder-body {
    padding: 17px 20px;
  }
  .reorder-item {
    gap: 10px;
  }
  .reorder-item img {
    width: 55px;
    height: 55px;
  }
  .reorder-item h3 {
    font-size: 13px;
  }
  .reorder-add-count {
    padding: 5px 6px;
    font-size: 10px;
  }
  .reorder-footer {
    padding: 15px 20px max(18px, env(safe-area-inset-bottom));
  }
}
</style>
