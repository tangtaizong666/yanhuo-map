<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { PackagePlus, RefreshCw } from "lucide-vue-next";
import { api, ApiError } from "../../lib/api";
import { useSession } from "../../stores/session";
const props = defineProps<{ stall: any }>(),
  emit = defineEmits<{ refresh: [] }>(),
  session = useSession();
type Batch = {
  idempotency_key: string;
  items: { product_id: number; quantity: number }[];
};
const quantities = reactive<Record<number, string>>({}),
  pending = ref<Batch | null>(null),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
const key = `merchant-restock:${session.user?.id}:${props.stall.id}`;
let disposed = false,
  uncertain = false,
  controller: AbortController | undefined;
const owner = session.user?.id;
watch(
  () => session.user?.id,
  (id) => {
    if (id !== owner) controller?.abort();
  },
  { flush: "sync" },
);
onBeforeUnmount(() => {
  disposed = true;
  controller?.abort();
});
try {
  const saved = sessionStorage.getItem(key);
  if (saved) {
    const value = JSON.parse(saved);
    if (
      typeof value.idempotency_key === "string" &&
      Array.isArray(value.items) &&
      value.items.every(
        (item: any) =>
          Number.isInteger(item.product_id) &&
          Number.isInteger(item.quantity) &&
          item.quantity > 0,
      )
    ) {
      pending.value = value;
      uncertain = true;
      for (const item of value.items)
        quantities[item.product_id] = String(item.quantity);
      notice.value =
        "上一次补货结果尚未确认。请重试原批次，服务器不会重复增加库存。";
    }
  }
} catch {
  error.value = "暂时无法读取补货草稿，请允许浏览器保存本站数据后刷新。";
}
const expanded = ref(!!pending.value);
const total = computed(() =>
  pending.value
    ? pending.value.items.reduce((sum, item) => sum + item.quantity, 0)
    : Object.values(quantities).reduce(
        (sum, value) => sum + (Number(value) || 0),
        0,
      ),
);
function name(id: number) {
  return (
    props.stall.products?.find((p: any) => p.id === id)?.name || `商品 ${id}`
  );
}
async function submit() {
  if (busy.value) return;
  error.value = "";
  notice.value = "";
  if (!pending.value) {
    const entries = Object.entries(quantities).filter(
      ([, value]) => String(value).trim() !== "" && Number(value) !== 0,
    );
    if (!entries.length) {
      error.value = "请填写这次实际增加的份数。";
      return;
    }
    if (
      entries.some(
        ([, value]) =>
          !/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 100000,
      )
    ) {
      error.value = "每道菜的补货份数须为 1 至 100,000 的整数。";
      return;
    }
    const batch = {
      idempotency_key:
        globalThis.crypto?.randomUUID?.() ||
        `restock-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      items: entries.map(([id, value]) => ({
        product_id: Number(id),
        quantity: Number(value),
      })),
    };
    try {
      sessionStorage.setItem(key, JSON.stringify(batch));
    } catch {
      error.value =
        "浏览器无法保存重试记录，暂未提交。请允许本站保存数据后重试。";
      return;
    }
    pending.value = batch;
  }
  busy.value = true;
  controller = new AbortController();
  try {
    await api(`/merchant/stalls/${props.stall.id}/restock`, {
      method: "POST",
      body: pending.value,
      signal: controller.signal,
    });
    // The captured storage key belongs to this account/stall even if the view was left.
    sessionStorage.removeItem(key);
    if (disposed) return;
    pending.value = null;
    uncertain = false;
    for (const id of Object.keys(quantities)) delete quantities[Number(id)];
    notice.value = "本次补货已确认，线上可卖份数已按新增数量更新。";
    emit("refresh");
  } catch (e) {
    // A later CSRF/auth/network failure says nothing about an earlier unknown write.
    // Only release a fresh batch that is conclusively rejected before mutation.
    const definitive =
      !uncertain &&
      e instanceof ApiError &&
      (e.data?.submitted === false || [400, 403, 404, 422].includes(e.status));
    if (definitive) {
      try {
        sessionStorage.removeItem(key);
      } catch {
        /* Optional cleanup. */
      }
    }
    if (!definitive) uncertain = true;
    if (disposed || session.user?.id !== owner) return;
    if (definitive) pending.value = null;
    error.value = definitive
      ? (e as Error).message
      : "本次补货结果尚未确认。保留原份数重试，不会重复补货；确认前不能修改这批数量。";
  } finally {
    if (!disposed) busy.value = false;
  }
}
</script>
<template>
  <details
    class="restock"
    :open="expanded"
    @toggle="expanded = ($event.target as HTMLDetailsElement).open"
  >
    <summary>
      <PackagePlus :size="22" /><span
        ><strong>今天补货</strong
        ><small>填这次新增到线上的份数，不用重新计算剩余数量</small></span
      >
    </summary>
    <div class="restock-body">
      <p>
        只增加本次分配给线上新单的份数，不包含已预留订单，也不是线下实物总数。取消订单按原规则释放；下架或暂停供应的商品补货后仍保持原状态。
      </p>
      <p v-if="error" class="m-alert" role="alert">{{ error }}</p>
      <p v-if="notice" class="m-info-banner" role="status">{{ notice }}</p>
      <form @submit.prevent="submit">
        <fieldset :disabled="busy || !!pending">
          <div
            v-for="product in stall.products"
            :key="product.id"
            class="restock-row"
          >
            <label :for="`restock-${product.id}`"
              ><strong>{{ product.name }}</strong
              ><small
                >线上剩余可卖 {{ product.stock }} 份{{
                  product.sale_paused ? " · 暂停供应" : ""
                }}{{ product.is_active ? "" : " · 已下架" }}</small
              ></label
            ><span>＋</span
            ><input
              :id="`restock-${product.id}`"
              v-model="quantities[product.id]"
              type="number"
              min="0"
              max="100000"
              inputmode="numeric"
              placeholder="0"
              :aria-label="`${product.name}本次新增份数`"
            /><span>份</span>
            <div class="quick-restock">
              <button type="button" :disabled="busy || !!pending" @click="quantities[product.id] = String(Math.min(100000, (Number(quantities[product.id]) || 0) + 5))" :aria-label="`${product.name}本批加5份`">+5</button>
              <button type="button" :disabled="busy || !!pending" @click="quantities[product.id] = String(Math.min(100000, (Number(quantities[product.id]) || 0) + 10))" :aria-label="`${product.name}本批加10份`">+10</button>
            </div>
          </div>
        </fieldset>
        <ul v-if="pending" class="batch-summary">
          <li v-for="item in pending.items" :key="item.product_id">
            {{ name(item.product_id) }}：本批增加 {{ item.quantity }} 份
          </li>
        </ul>
        <div class="restock-footer">
          <strong>本次共增加 {{ total }} 份</strong
          ><button class="btn btn-primary" :disabled="busy">
            <RefreshCw v-if="pending" :size="17" />{{
              busy ? "正在确认…" : pending ? "重试确认这批补货" : "确认本次补货"
            }}
          </button>
        </div>
      </form>
    </div>
  </details>
</template>
<style scoped>
.restock {
  background: #fffaf2;
  border: 1px solid #ebdccb;
  border-radius: 18px;
  margin: 20px 0;
}
.restock summary {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px;
  cursor: pointer;
  min-height: 44px;
  color: #734721;
}
.restock summary small {
  display: block;
  font-weight: 400;
  color: #7d6752;
  margin-top: 6px;
  font-size: 12px;
}
.restock-body {
  padding: 0 18px 18px;
}
.restock-body > p {
  font-size: 13px;
  line-height: 1.8;
  color: #79624b;
}
fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.restock-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
  border-top: 1px solid #ecdfcf;
}
.restock-row label {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.restock-row small {
  display: block;
  font-size: 12px;
  color: #7c6652;
  margin-top: 6px;
}
.restock-row input {
  width: 88px;
  min-height: 44px;
  box-sizing: border-box;
  border: 1px solid #dcc6b1;
  background: white;
  border-radius: 10px;
  padding: 10px;
  color: #493421;
}
.restock-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
  flex-wrap: wrap;
}
.restock-footer strong {
  font-size: 14px;
}
.batch-summary {
  font-size: 13px;
  line-height: 1.8;
  padding-left: 20px;
}
@media (max-width: 600px) {
  .restock { margin: 12px 0; border-radius: 12px; }
  .restock summary { padding: 12px; min-height: 54px; }
  .restock:not([open]) summary small { display: none; }
  .restock summary::after { content: '展开补货'; margin-left: auto; font-size: 13px; font-weight: 400; }
  .restock[open] summary::after { content: '收起'; }

  .restock-footer button {
    width: 100%;
  }
  .restock-row input {
    width: 72px;
  }
}
</style>
