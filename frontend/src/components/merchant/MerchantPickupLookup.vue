<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { KeyRound, X } from "lucide-vue-next";
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
  error = ref(""),
  codeInput = ref<HTMLInputElement | null>(null);
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

function clearCode() {
  code.value = "";
  error.value = "";
  void nextTick(() => codeInput.value?.focus({ preventScroll: true }));
}

function onCodeInput(event: Event) {
  const input = event.target as HTMLInputElement;
  const digits = input.value.replace(/\D+/g, "").slice(0, 4);
  if (input.value !== digits) input.value = digits;
  code.value = digits;
}

watch(() => `${session.user?.id}:${props.stallId}`, clear, { flush: "sync" });
watch(code, () => {
  ambiguous.value = false;
  number.value = "";
});
onMounted(() => {
  void nextTick(() => codeInput.value?.focus({ preventScroll: true }));
});
onBeforeUnmount(clear);

async function lookup() {
  if (busy.value || session.user?.id !== owner) return;
  if (!/^\d{4}$/.test(code.value.trim())) {
    error.value = "请输入同学出示的 4 位数字取餐码。";
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
      <p id="pickup-lookup-hint">
        输入同学出示的 4 位取餐码查找本摊待取订单。查单不会收款，也不会直接核销。
      </p>
    </div>
    <form @submit.prevent="lookup">
      <div class="pickup-field">
        <label for="merchant-pickup-lookup-code">4 位取餐码</label>
        <div class="pickup-input-wrap">
          <input
            id="merchant-pickup-lookup-code"
            ref="codeInput"
            :value="code"
            inputmode="numeric"
            pattern="[0-9]{4}"
            minlength="4"
            maxlength="4"
            autocomplete="off"
            aria-describedby="pickup-lookup-hint"
            required
            :disabled="busy"
            placeholder="请同学出示 4 位码"
            @input="onCodeInput"
          />
          <button
            v-if="code && !busy"
            type="button"
            class="pickup-clear-btn"
            aria-label="清空取餐码"
            @click="clearCode"
          >
            <X :size="16" />
          </button>
        </div>
      </div>
      <label v-if="ambiguous" class="pickup-field"
        >完整订单号<input
          v-model="number"
          autocomplete="off"
          maxlength="50"
          required
          :disabled="busy"
      /></label>
      <button type="submit" class="pickup-submit-btn" :disabled="busy">
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
.pickup-field {
  display: grid;
  gap: 7px;
  font-size: 13px;
  font-weight: 600;
  flex: 1;
  min-width: 180px;
}
.pickup-input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.pickup-lookup input {
  min-height: 46px;
  border: 1px solid #dfc9af;
  background: #fffdfa;
  border-radius: 10px;
  padding: 10px 44px 10px 12px;
  box-sizing: border-box;
  width: 100%;
  font-size: 16px;
  font-variant-numeric: tabular-nums;
}
.pickup-lookup input:not(:placeholder-shown) {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 5px;
  color: #3d2817;
}
.pickup-clear-btn {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: 38px;
  min-height: 38px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #8a6c50;
  display: grid;
  place-items: center;
  cursor: pointer;
}
.pickup-submit-btn {
  min-height: 46px;
  padding: 10px 18px;
  border: 0;
  border-radius: 10px;
  background: #ae551f;
  color: white;
  font-weight: 700;
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
  .pickup-submit-btn {
    width: 100%;
  }
}
</style>
