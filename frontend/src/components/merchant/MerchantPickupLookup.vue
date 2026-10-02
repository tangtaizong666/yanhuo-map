<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { KeyRound } from "lucide-vue-next";
import { api, ApiError } from "../../lib/api";
import { useSession } from "../../stores/session";
const props = defineProps<{ stallId: number }>();
const emit = defineEmits<{ found: [order: any, code: string] }>();
const session = useSession(),
  owner = session.user?.id;
const code = ref(""),
  number = ref(""),
  ambiguous = ref(false),
  busy = ref(false),
  error = ref("");
let generation = 0,
  controller: AbortController | undefined;
function clear() {
  generation++;
  controller?.abort();
  code.value = "";
  number.value = "";
  ambiguous.value = false;
  error.value = "";
  busy.value = false;
}
watch(() => `${session.user?.id}:${props.stallId}`, clear, { flush: "sync" });
watch(code, () => {
  ambiguous.value = false;
  number.value = "";
});
onBeforeUnmount(clear);
async function lookup() {
  if (busy.value || session.user?.id !== owner) return;
  if (!/^\d{8}$/.test(code.value.trim())) {
    error.value = "请输入同学出示的完整 8 位取餐码。";
    return;
  }
  if (ambiguous.value && !number.value.trim()) {
    error.value = "请补充同学订单页显示的完整订单号。";
    return;
  }
  const current = generation,
    enteredCode = code.value.trim();
  controller = new AbortController();
  busy.value = true;
  error.value = "";
  try {
    const order = await api<any>(
      `/merchant/stalls/${props.stallId}/pickup-lookup`,
      {
        method: "POST",
        body: {
          pickup_code: enteredCode,
          ...(number.value.trim() ? { number: number.value.trim() } : {}),
        },
        signal: controller.signal,
      },
    );
    if (current !== generation) return;
    if (
      !order?.id ||
      order.stall_id !== props.stallId ||
      order.status !== "ready" ||
      order.fulfillment_type !== "pickup" ||
      !Array.isArray(order.items)
    )
      throw new Error("查单结果未确认，请重试。");
    emit("found", order, enteredCode);
  } catch (cause) {
    if (current !== generation) return;
    if (cause instanceof ApiError && cause.code === "pickup_code_ambiguous") {
      ambiguous.value = true;
      error.value = "本摊有重复取餐码，请补充完整订单号再查找。";
    } else error.value = (cause as Error).message;
  } finally {
    if (current === generation) busy.value = false;
  }
}
</script>
<template>
  <section class="pickup-lookup" aria-label="取餐码查单">
    <div>
      <h3><KeyRound :size="20" />同学来取餐了</h3>
      <p>输入取餐码查找本摊已出餐的自取订单。查单不会收款，也不会直接核销。</p>
    </div>
    <form @submit.prevent="lookup">
      <label
        >8 位取餐码<input
          v-model="code"
          inputmode="numeric"
          pattern="[0-9]{8}"
          minlength="8"
          maxlength="8"
          autocomplete="off"
          required
          :disabled="busy"
          placeholder="请同学出示取餐码" /></label
      ><label v-if="ambiguous"
        >完整订单号<input
          v-model="number"
          autocomplete="off"
          maxlength="50"
          required
          :disabled="busy" /></label
      ><button type="submit" :disabled="busy">
        {{ busy ? "查找中…" : "查找待取餐订单" }}
      </button>
    </form>
    <p v-if="error" role="alert" class="lookup-error">{{ error }}</p>
  </section>
</template>
<style scoped>
.pickup-lookup {
  background: #fff8ec;
  border: 1px solid #ecd6bb;
  border-radius: 16px;
  padding: 18px;
  margin: 18px 0;
  color: #654b34;
}
.pickup-lookup h3 {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 17px;
  margin: 0;
}
.pickup-lookup p {
  font-size: 12px;
  line-height: 1.8;
  margin: 8px 0 14px;
}
.pickup-lookup form {
  display: flex;
  align-items: end;
  gap: 12px;
  flex-wrap: wrap;
}
.pickup-lookup label {
  display: grid;
  gap: 7px;
  font-size: 13px;
  flex: 1;
  min-width: 180px;
}
.pickup-lookup input {
  min-height: 44px;
  border: 1px solid #dfc9af;
  background: #fffdfa;
  border-radius: 10px;
  padding: 10px 12px;
  box-sizing: border-box;
  width: 100%;
  font-size: 16px;
}
.pickup-lookup button {
  min-height: 44px;
  padding: 10px 18px;
  border: 0;
  border-radius: 10px;
  background: #ae551f;
  color: white;
  cursor: pointer;
}
.lookup-error {
  color: #9e3f26;
}
.pickup-lookup :is(button, input):focus-visible {
  outline: 3px solid #d88740;
  outline-offset: 2px;
}
@media (max-width: 600px) {
  .pickup-lookup button {
    width: 100%;
  }
}
</style>
