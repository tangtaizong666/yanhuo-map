<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, ApiError } from "../../lib/api";
import { newRequestKey } from "../../lib/engagement";
import { useSession } from "../../stores/session";
const props = defineProps<{ product: any; stallId: number }>();
const emit = defineEmits<{ refresh: [] }>();
const session = useSession(),
  owner = session.user?.id;
type Correction = {
  stock: number;
  expected_stock_version: number;
  reason: string;
  idempotency_key: string;
};
const storageKey = `merchant-stock-correction:${owner}:${props.stallId}:${props.product.id}`;
const quantity = ref(""),
  expanded = ref(false),
  reason = ref(""),
  pending = ref<Correction | null>(null),
  conflict = ref<any>(null),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
const baselineVersion = ref(props.product.stock_version ?? 0);
let disposed = false,
  controller: AbortController | undefined;
const validOwner = () => !disposed && session.user?.id === owner;
watch(
  () => session.user?.id,
  () => controller?.abort(),
  { flush: "sync" },
);
onBeforeUnmount(() => {
  disposed = true;
  controller?.abort();
});
watch(
  () => props.product.stock_version,
  (value) => {
    if (!quantity.value && !pending.value && !conflict.value)
      baselineVersion.value = value ?? 0;
  },
);
try {
  const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
  if (
    saved &&
    Number.isInteger(saved.stock) &&
    saved.stock >= 0 &&
    saved.stock <= 100000 &&
    Number.isInteger(saved.expected_stock_version) &&
    saved.expected_stock_version >= 0 &&
    typeof saved.reason === "string" &&
    saved.reason.length > 0 &&
    saved.reason.length <= 200 &&
    typeof saved.idempotency_key === "string" &&
    saved.idempotency_key.length >= 8 &&
    saved.idempotency_key.length <= 128
  ) {
    pending.value = saved;
    expanded.value = true;
    quantity.value = String(saved.stock);
    reason.value = saved.reason;
    notice.value = "上次更正结果待确认，请确认原操作结果，不要另建一笔。";
  }
} catch {
  error.value = "无法读取库存更正记录，请允许本站保存数据后刷新。";
}
const remaining = computed(() => conflict.value?.stock ?? props.product.stock);
function resetAfterConflict() {
  baselineVersion.value = conflict.value.stock_version;
  conflict.value = null;
  quantity.value = "";
  error.value = "";
  notice.value = "已读取最新线上可卖份数，请重新盘点填写。";
}
async function submit() {
  if (busy.value || conflict.value || !validOwner()) return;
  expanded.value = true;
  error.value = "";
  notice.value = "";
  if (!pending.value) {
    if (
      !/^\d+$/.test(quantity.value) ||
      Number(quantity.value) > 100000 ||
      !reason.value.trim() ||
      reason.value.trim().length > 200
    ) {
      error.value = "请填写 0 至 100,000 的线上剩余可卖份数，并说明更正原因。";
      return;
    }
    const value: Correction = {
      stock: Number(quantity.value),
      expected_stock_version: baselineVersion.value,
      reason: reason.value.trim(),
      idempotency_key: newRequestKey("stock"),
    };
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      error.value = "无法保存安全重试记录，暂未提交，请允许本站保存数据。";
      return;
    }
    pending.value = value;
  }
  busy.value = true;
  controller = new AbortController();
  try {
    const result = await api<any>(
      `/merchant/products/${props.product.id}/stock-correction`,
      { method: "POST", body: pending.value, signal: controller.signal },
    );
    if (!validOwner()) return;
    if (
      result?.product?.id !== props.product.id ||
      !Number.isInteger(result.product.stock_version) ||
      result.product.stock_version < 0 ||
      !Number.isInteger(result.product.stock) ||
      result.product.stock < 0 ||
      result.product.stock > 100000 ||
      typeof result.replayed !== "boolean"
    )
      throw new ApiError("更正结果尚未确认，请重试原操作。", 502, {
        code: "invalid_response",
      });
    sessionStorage.removeItem(storageKey);
    pending.value = null;
    quantity.value = "";
    reason.value = "";
    baselineVersion.value = result.product.stock_version;
    notice.value = `线上剩余可卖份数已确认为 ${result.product.stock} 份。暂停供应与上下架状态未改变。`;
    emit("refresh");
  } catch (cause) {
    if (!validOwner()) return;
    if (
      cause instanceof ApiError &&
      cause.data?.submitted !== false &&
      cause.code === "stock_version_conflict" &&
      cause.data?.product?.id === props.product.id &&
      Number.isInteger(cause.data.product.stock_version) &&
      cause.data.product.stock_version >= 0 &&
      Number.isInteger(cause.data.product.stock) &&
      cause.data.product.stock >= 0
    ) {
      sessionStorage.removeItem(storageKey);
      pending.value = null;
      conflict.value = cause.data.product;
      error.value =
        "期间发生了下单、取消或补货，原数量已过时；本次未更正，请按最新数量重新核对。";
      emit("refresh");
    } else
      error.value =
        "本次更正结果尚未确认。保留原份数和原因重试，重复请求不会再次更正。";
  } finally {
    if (validOwner()) busy.value = false;
  }
}
</script>
<template>
  <details
    class="inventory-correction"
    :open="expanded"
    @toggle="expanded = ($event.target as HTMLDetailsElement).open"
  >
    <summary>更多：更正线上可卖份数 <span>盘点后再操作</span></summary>
    <p>
      当前线上剩余可卖
      {{ remaining }}
      份。只填写未被订单占用、还能卖给下一位同学的份数；不包含已预留订单，也不是线下实物总数。
    </p>
    <p v-if="product.sale_paused" class="inventory-note">
      当前暂停供应。更正或补货不会自动恢复供应。
    </p>
    <form @submit.prevent="submit">
      <fieldset :disabled="busy || !!pending || !!conflict">
        <label
          >更正后的线上剩余可卖份数<input
            v-model="quantity"
            type="number"
            min="0"
            max="100000"
            inputmode="numeric"
            required
            :aria-label="`${product.name}更正后线上可卖份数`"
        /></label>
        <label
          >更正原因<textarea
            v-model="reason"
            rows="2"
            maxlength="200"
            required
            :aria-label="`${product.name}库存更正原因`"
            placeholder="例如：盘点后发现线上可卖数量填多了"
          />
        </label>
      </fieldset>
      <p v-if="error" role="alert" class="inventory-error">{{ error }}</p>
      <p v-if="notice" role="status" class="inventory-note">{{ notice }}</p>
      <button v-if="conflict" type="button" @click="resetAfterConflict">
        按最新数量重新填写
      </button>
      <button v-else type="submit" :disabled="busy">
        {{
          busy
            ? "正在确认…"
            : pending
              ? "确认原更正结果"
              : "确认更正线上可卖份数"
        }}
      </button>
    </form>
  </details>
</template>
<style scoped>
.inventory-correction {
  border-top: 1px solid #eadfce;
  margin-top: 12px;
  padding-top: 4px;
  font-size: 13px;
  color: #73563e;
}
.inventory-correction summary {
  min-height: 44px;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  flex-wrap: wrap;
}
.inventory-correction summary span {
  font-size: 11px;
  color: #806d58;
}
.inventory-correction p {
  line-height: 1.8;
  margin: 8px 0;
}
.inventory-correction fieldset {
  padding: 0;
  border: 0;
  min-width: 0;
  display: grid;
  gap: 12px;
}
.inventory-correction label {
  display: grid;
  gap: 7px;
}
.inventory-correction input,
.inventory-correction textarea {
  min-height: 44px;
  box-sizing: border-box;
  border: 1px solid #dfcdb8;
  border-radius: 10px;
  padding: 10px 12px;
  width: 100%;
  background: #fffdfa;
}
.inventory-correction button {
  min-height: 44px;
  padding: 10px 14px;
  border: 1px solid #e2c8a9;
  border-radius: 10px;
  background: #fff0df;
  color: #8f461e;
  margin-top: 10px;
  cursor: pointer;
}
.inventory-error {
  color: #9d3e27;
}
.inventory-note {
  color: #536d40;
}
.inventory-correction button:disabled {
  opacity: 0.6;
}
.inventory-correction :is(button, input, textarea):focus-visible {
  outline: 3px solid #d38648;
  outline-offset: 2px;
}
</style>
