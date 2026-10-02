<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { MessageSquare, X, CheckCircle2 } from "lucide-vue-next";
import { api, ApiError } from "../lib/api";
import { newRequestKey } from "../lib/engagement";
import { useSession } from "../stores/session";
import type { Order } from "../lib/types";
const props = defineProps<{ order: Order }>();
const session = useSession();
const dialog = ref<HTMLDialogElement>();
const content = ref("");
const contact = ref("");
const error = ref("");
const busy = ref(false);
const received = ref(false);
type Request = {
  order_id: string;
  content: string;
  contact: string;
  idempotency_key: string;
};
const pending = ref<Request | null>(null);
let revision = 0;
let controller: AbortController | undefined;
const storageKey = () =>
  `yanhuo-order-help-${session.user?.id}-${props.order.id}`;
function persist() {
  try {
    if (pending.value)
      sessionStorage.setItem(storageKey(), JSON.stringify(pending.value));
    else sessionStorage.removeItem(storageKey());
  } catch {
    /* In-app recovery still retains the original submission. */
  }
}
function open() {
  error.value = "";
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey()) || "null");
    if (
      saved?.order_id === props.order.id &&
      typeof saved.content === "string" &&
      typeof saved.contact === "string" &&
      typeof saved.idempotency_key === "string"
    ) {
      pending.value = saved;
      content.value = saved.content;
      contact.value = saved.contact;
    }
  } catch {
    /* Ignore invalid optional storage. */
  }
  dialog.value?.showModal();
}
async function submit() {
  if (
    busy.value ||
    received.value ||
    !session.user ||
    (!pending.value && !content.value.trim())
  )
    return;
  const current = ++revision;
  controller?.abort();
  controller = new AbortController();
  const recovering = !!pending.value;
  if (!pending.value) {
    pending.value = {
      order_id: props.order.id,
      content: content.value.trim(),
      contact: contact.value.trim(),
      idempotency_key: newRequestKey("order-help"),
    };
    persist();
  }
  busy.value = true;
  error.value = "";
  try {
    const result = await api<{ id: number }>("/feedback", {
      method: "POST",
      body: pending.value,
      signal: controller.signal,
    });
    if (current !== revision) return;
    if (!result?.id)
      throw new ApiError("反馈返回结果不完整，请确认原反馈结果。", 502, {
        code: "invalid_response",
      });
    pending.value = null;
    persist();
    received.value = true;
  } catch (cause) {
    if (current !== revision) return;
    error.value = (cause as Error).message;
    if (
      !recovering &&
      cause instanceof ApiError &&
      (cause.data?.submitted === false ||
        (cause.status === 400 && cause.data?.errors))
    ) {
      pending.value = null;
      persist();
    }
  } finally {
    if (current === revision) busy.value = false;
  }
}
watch(
  () => [props.order.id, session.user?.id],
  () => {
    revision++;
    controller?.abort();
    busy.value = false;
    pending.value = null;
    content.value = "";
    contact.value = "";
    received.value = false;
    error.value = "";
    dialog.value?.close();
  },
  { flush: "sync" },
);
onUnmounted(() => {
  revision++;
  controller?.abort();
});
</script>
<template>
  <button type="button" class="order-help-open" @click="open">
    <MessageSquare :size="16" />联系不上商家？反馈订单问题
  </button>
  <dialog
    ref="dialog"
    class="order-help-dialog"
    aria-labelledby="order-help-title"
    @click.self="dialog?.close()"
  >
    <header>
      <div>
        <span class="eyebrow">订单帮助</span>
        <h2 id="order-help-title">
          {{ received ? "反馈已收到" : "把这笔订单的问题告诉我们" }}
        </h2>
      </div>
      <button type="button" aria-label="关闭订单反馈" @click="dialog?.close()">
        <X :size="21" />
      </button>
    </header>
    <div v-if="received" class="order-help-received" role="status">
      <CheckCircle2 :size="34" /><strong>已关联订单 {{ order.number }}</strong>
      <p>
        运营会人工查看。这里不是即时客服，提交反馈不会自动取消订单或办理退款。
      </p>
      <button type="button" class="btn btn-secondary" @click="dialog?.close()">
        返回订单
      </button>
    </div>
    <form v-else @submit.prevent="submit">
      <p class="order-help-note">
        {{ order.stall_name }} · 订单 {{ order.number
        }}<br />仅提交给运营人工处理；如需立即取餐，请优先联系或到摊确认。
      </p>
      <label
        >遇到了什么问题<textarea
          v-model="content"
          maxlength="1000"
          rows="4"
          required
          :disabled="busy || !!pending"
          placeholder="例如：到了下单时的取餐位置，没有找到商家"
        />
      </label>
      <label
        >便于回复的联系方式 <small>选填</small
        ><input
          v-model="contact"
          maxlength="100"
          :disabled="busy || !!pending"
          placeholder="可填写电话号码或其他联系方式"
      /></label>
      <p v-if="error" class="error-message" role="alert">{{ error }}</p>
      <p v-if="pending && !busy" class="order-help-note">
        上次结果尚未确认，将用原内容查询提交结果。
      </p>
      <button
        type="submit"
        class="btn btn-primary"
        :disabled="busy || (!pending && !content.trim())"
      >
        {{
          busy ? "正在提交…" : pending ? "确认原反馈结果" : "提交给运营人工处理"
        }}
      </button>
    </form>
  </dialog>
</template>
<style scoped>
.order-help-open {
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  color: #8b5b30;
  font-size: 12px;
  text-align: left;
}
.order-help-dialog {
  padding: 0;
  border: 1px solid #eadbc8;
  border-radius: 20px;
  background: #fffaf4;
  color: #382e25;
  max-width: calc(100% - 24px);
  width: 500px;
  max-height: calc(100dvh - 24px);
  margin: auto;
  overflow: auto;
}
.order-help-dialog::backdrop {
  background: #37241266;
}
header {
  padding: 22px 22px 12px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  justify-content: space-between;
}
h2 {
  font-size: 20px;
  line-height: 1.5;
  margin: 6px 0 0;
}
header button {
  min-width: 44px;
  min-height: 44px;
  display: grid;
  place-items: center;
}
form {
  padding: 0 22px 22px;
}
.order-help-note {
  font-size: 12px;
  line-height: 1.8;
  color: #78634f;
  margin: 0 0 17px;
}
label {
  display: block;
  font-size: 13px;
  margin: 16px 0;
}
small {
  color: #887660;
  font-size: 11px;
}
textarea,
input {
  width: 100%;
  min-height: 44px;
  box-sizing: border-box;
  display: block;
  margin-top: 8px;
  border: 1px solid #dfcfbb;
  border-radius: 10px;
  padding: 11px;
  background: #fff;
  font: inherit;
  color: #382e25;
}
textarea {
  resize: vertical;
}
form > .btn {
  width: 100%;
  min-height: 46px;
}
.order-help-received {
  padding: 10px 24px 25px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  color: #47653c;
}
.order-help-received p {
  font-size: 13px;
  line-height: 1.8;
  color: #796551;
}
@media (max-width: 400px) {
  header {
    padding: 18px 16px 10px;
  }
  form {
    padding: 0 16px 18px;
  }
  h2 {
    font-size: 18px;
  }
}
</style>
