<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import {
  AlertCircle,
  Check,
  Clock3,
  Copy,
  Flag,
  MapPin,
  Navigation,
  Phone,
  X,
} from "lucide-vue-next";
import {
  ApiError,
  api,
  confirmedText,
  formatClock,
  routeUrl,
  statusText,
} from "../lib/api";
import { copyText, newRequestKey, trackEngagement } from "../lib/engagement";
import { useSession } from "../stores/session";
import type { Stall } from "../lib/types";

const props = defineProps<{ stall: Stall; compact?: boolean }>();
const session = useSession();
const dialog = ref<HTMLDialogElement>();
const kind = ref("not_found");
const content = ref("");
const contact = ref("");
const error = ref("");
const busy = ref(false);
const sent = ref(false);
const uncertain = ref(false);
const copyMessage = ref("");
const addressField = ref<HTMLInputElement>();
const showAddressField = ref(false);
const validLocation = computed(
  () =>
    Number.isFinite(props.stall.latitude) &&
    Number.isFinite(props.stall.longitude),
);
const addressText = computed(() =>
  [props.stall.address, props.stall.arrival_note].filter(Boolean).join(" · "),
);
const phone = computed(() =>
  (props.stall.contact_phone || "").replace(/[^\d+]/g, ""),
);
let requestKey = "";
let locationSnapshot: Record<string, unknown> = {};
let pendingBody: Record<string, unknown> | null = null;
let requestController: AbortController | null = null;
let revision = 0;

function directions() {
  trackEngagement(
    "route_click",
    props.stall.id,
    props.compact ? "map" : "stall_detail",
  );
}
async function copyAddress() {
  if (await copyText(addressText.value)) copyMessage.value = "地址已复制";
  else {
    copyMessage.value = "请长按下方地址复制，或选中后使用复制快捷键。";
    showAddressField.value = true;
    await nextTick();
    addressField.value?.focus();
    addressField.value?.select();
  }
}
function openReport() {
  // Freeze what the visitor actually saw; a later merchant edit must not silently
  // replace the context of their report.
  if (!uncertain.value) {
    requestKey = newRequestKey("visit");
    locationSnapshot = {
      address: props.stall.address,
      latitude: props.stall.latitude,
      longitude: props.stall.longitude,
      last_confirmed_at: props.stall.last_confirmed_at,
    };
    pendingBody = null;
    kind.value = "not_found";
    content.value = "";
    contact.value = "";
    sent.value = false;
    error.value = "";
  }
  dialog.value?.showModal();
}
function close() {
  if (!busy.value) dialog.value?.close();
}
async function submit() {
  if (busy.value || sent.value) return;
  const wasUncertain = uncertain.value;
  const current = ++revision;
  requestController?.abort();
  requestController = new AbortController();
  busy.value = true;
  error.value = "";
  if (!pendingBody)
    pendingBody = {
      stall_id: props.stall.id,
      kind: kind.value,
      content: content.value.trim(),
      contact: contact.value.trim(),
      location_snapshot: locationSnapshot,
      idempotency_key: requestKey,
    };
  try {
    await api("/feedback", {
      method: "POST",
      body: pendingBody,
      signal: requestController.signal,
    });
    if (current !== revision) return;
    sent.value = true;
    uncertain.value = false;
    pendingBody = null;
  } catch (failure) {
    if (current !== revision) return;
    const knownRejection =
      failure instanceof ApiError &&
      failure.status >= 400 &&
      failure.status < 500 &&
      ![408, 429].includes(failure.status);
    // A failed security check on this retry says nothing about the earlier write.
    const retryCannotResolve =
      wasUncertain &&
      failure instanceof ApiError &&
      (failure.data?.submitted === false ||
        [401, 403, 408, 429].includes(failure.status));
    if (
      !retryCannotResolve &&
      (knownRejection ||
        (failure instanceof ApiError && failure.data?.submitted === false))
    ) {
      uncertain.value = false;
      pendingBody = null;
    } else uncertain.value = true;
    error.value = uncertain.value
      ? "提交结果尚未确认。请使用下方按钮确认原反馈，系统不会重复记录。"
      : (failure as Error).message;
  } finally {
    if (current === revision) busy.value = false;
  }
}
function reset() {
  revision++;
  requestController?.abort();
  busy.value = false;
  uncertain.value = false;
  pendingBody = null;
  sent.value = false;
  dialog.value?.close();
  showAddressField.value = false;
  copyMessage.value = "";
}
watch(() => [props.stall.id, session.user?.id], reset, { flush: "sync" });
onUnmounted(reset);
</script>

<template>
  <section :class="['stall-visit', { compact }]" aria-label="到摊指引">
    <div class="visit-heading">
      <span class="visit-symbol"><MapPin :size="20" /></span>
      <div>
        <h3>到这里，找到好味道</h3>
        <p>
          {{ statusText(stall.status)
          }}<template v-if="stall.transaction_enabled && stall.accepting_orders === false">
            · 暂停线上接单</template
          >
        </p>
      </div>
    </div>
    <strong class="visit-address">{{ stall.address }}</strong>
    <p class="visit-note">
      {{ stall.arrival_note || "商家尚未补充认摊说明，出发前可先联系确认。" }}
    </p>
    <p v-if="stall.usual_hours" class="visit-note">
      通常出摊：{{ stall.usual_hours }}（商家计划，以当日确认状态为准）
    </p>
    <figure v-if="stall.arrival_image" class="visit-photo">
      <img
        :src="stall.arrival_image"
        :alt="`${stall.name}的认摊现场照片`"
        loading="lazy"
        width="640"
        height="360"
      />
      <figcaption>商家提供的认摊照片 · 非实时画面</figcaption>
    </figure>
    <div class="visit-facts">
      <span
        ><Clock3 :size="14" />{{
          stall.last_confirmed_at
            ? `商家${confirmedText(stall.last_confirmed_at)}`
            : "商家尚未确认位置"
        }}</span
      ><span v-if="stall.closes_at"
        >预计 {{ formatClock(stall.closes_at) }} 收摊</span
      >
    </div>
    <p v-if="stall.status === 'stale'" class="visit-warning">
      <AlertCircle :size="16" />位置已过期，请先联系商家确认再出发。
    </p>
    <p
      v-else-if="stall.status === 'closed' || stall.status === 'paused'"
      class="visit-warning"
    >
      <AlertCircle :size="16" />{{
        stall.status === "closed"
          ? "商家已收摊，下次出摊后再来。"
          : "商家正在暂歇，出发前请先确认。"
      }}
    </p>
    <div class="visit-actions">
      <a
        v-if="validLocation"
        :href="routeUrl(stall.latitude, stall.longitude, stall.name)"
        target="_blank"
        rel="noopener"
        @click="directions"
        ><Navigation :size="16" />查看路线</a
      ><button type="button" @click="copyAddress">
        <Copy :size="16" />复制地址</button
      ><a v-if="phone" :href="`tel:${phone}`"><Phone :size="16" />联系商家</a>
    </div>
    <p v-if="copyMessage" class="visit-copy-message" role="status">
      {{ copyMessage }}
    </p>
    <input
      v-if="showAddressField"
      ref="addressField"
      class="visit-copy-field"
      :value="addressText"
      readonly
      aria-label="可复制的摊位地址"
    />
    <button type="button" class="visit-report" @click="openReport">
      <Flag :size="14" />没找到摊位？告诉我们<span>反馈位置问题</span>
    </button>
  </section>
  <dialog
    ref="dialog"
    class="visit-report-dialog"
    aria-labelledby="visit-report-title"
    @click.self="close"
    @cancel="busy && $event.preventDefault()"
  >
    <header>
      <span class="visit-symbol"><Flag :size="22" /></span
      ><button
        type="button"
        class="visit-close"
        aria-label="关闭位置反馈"
        :disabled="busy"
        @click="close"
      >
        <X :size="20" />
      </button>
    </header>
    <template v-if="sent"
      ><h2 id="visit-report-title"><Check :size="24" />反馈已收到</h2>
      <p>
        运营会核实这条位置反馈。反馈不会直接改变摊位状态；有急事时可先联系商家。
      </p>
      <button type="button" class="btn btn-primary" @click="close">
        知道了
      </button></template
    >
    <form v-else @submit.prevent="submit">
      <span class="eyebrow">HELP US KEEP IT ACCURATE</span>
      <h2 id="visit-report-title">没找到 {{ stall.name }}？</h2>
      <p>告诉我们现场的情况，帮助下一位同学少跑空。无需登录。</p>
      <p class="visit-snapshot">你看到的位置：{{ locationSnapshot.address }}</p>
      <fieldset :disabled="busy || uncertain">
        <legend>遇到了什么情况</legend>
        <label
          v-for="option in [
            { value: 'not_found', label: '这里没人' },
            { value: 'wrong_location', label: '位置不对' },
            { value: 'mismatch', label: '信息不符' },
          ]"
          :key="option.value"
          class="visit-choice"
          ><input
            v-model="kind"
            type="radio"
            name="visit-issue"
            :value="option.value"
          />{{ option.label }}</label
        ><label class="visit-field"
          >补充说明（选填）<textarea
            v-model="content"
            rows="3"
            maxlength="1000"
            placeholder="例如：南门外已走了一圈，没有看到这个摊位。"
          ></textarea></label
        ><label class="visit-field"
          >联系方式（选填）<input
            v-model="contact"
            maxlength="100"
            placeholder="仅供运营核实，不公开给其他用户"
        /></label>
      </fieldset>
      <p v-if="error" class="visit-error" role="alert">{{ error }}</p>
      <button type="submit" class="btn btn-primary" :disabled="busy">
        {{
          busy ? "正在提交…" : uncertain ? "确认原反馈结果" : "提交位置反馈"
        }}</button
      ><small class="visit-privacy"
        >仅用于核实本次位置问题，请勿填写学号、证件号等无关信息。</small
      >
    </form>
  </dialog>
</template>

<style scoped>
.stall-visit {
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 24px;
  background: #fffdf8;
}
.visit-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.visit-symbol {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 13px;
  background: #fff0df;
  color: #ac581d;
  flex: none;
}
.visit-heading h3 {
  margin: 0;
  font-size: 17px;
}
.visit-heading p {
  font-size: 12px;
  color: #806c5b;
  margin: 5px 0 0;
}
.visit-address {
  display: block;
  font-size: 18px;
  line-height: 1.6;
  word-break: break-word;
}
.visit-note {
  color: #735f4e;
  font-size: 14px;
  line-height: 1.8;
  margin: 9px 0 16px;
  white-space: pre-line;
  word-break: break-word;
}
.visit-photo {
  margin: 16px 0;
}
.visit-photo img {
  width: 100%;
  height: auto;
  max-height: 300px;
  object-fit: cover;
  border-radius: 12px;
}
.visit-photo figcaption {
  font-size: 11px;
  color: #786655;
  margin-top: 7px;
}
.visit-facts {
  display: flex;
  gap: 10px 16px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #786655;
}
.visit-facts span {
  display: flex;
  align-items: center;
  gap: 6px;
}
.visit-warning {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  line-height: 1.7;
  background: #fff2df;
  color: #88571c;
  padding: 12px;
  border-radius: 10px;
}
.visit-warning svg {
  flex: none;
  margin-top: 3px;
}
.visit-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 18px;
}
.visit-actions a,
.visit-actions button {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  border: 1px solid #eadbca;
  border-radius: 10px;
  background: white;
  padding: 9px 13px;
  font: inherit;
  font-size: 13px;
  color: #784819;
  cursor: pointer;
  text-decoration: none;
}
.visit-report {
  margin-top: 18px;
  border: 0;
  border-top: 1px solid var(--line);
  background: transparent;
  padding: 16px 0 0;
  min-height: 44px;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  text-align: left;
  font: inherit;
  font-size: 12px;
  color: #786655;
  cursor: pointer;
}
.visit-report span {
  margin-left: auto;
  font-size: 11px;
  color: #ac581d;
}
.visit-copy-message {
  font-size: 12px;
  color: #875225;
  line-height: 1.7;
}
.visit-copy-field {
  width: 100%;
  min-height: 44px;
  box-sizing: border-box;
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 8px;
  color: inherit;
  background: white;
}
.compact {
  padding: 18px;
}
.compact .visit-heading {
  margin-bottom: 13px;
}
.compact .visit-address {
  font-size: 16px;
}
.compact .visit-photo img {
  max-height: 180px;
}
.compact .visit-report span {
  display: none;
}
.visit-report-dialog {
  width: min(460px, calc(100vw - 28px));
  max-height: calc(100dvh - 32px);
  overflow: auto;
  margin: auto;
  border: 1px solid var(--line);
  border-radius: 22px;
  padding: 26px;
  background: #fffdf9;
  color: #493226;
  box-shadow: 0 22px 80px #38251f30;
}
.visit-report-dialog::backdrop {
  background: #2b211966;
  backdrop-filter: blur(4px);
}
.visit-report-dialog header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}
.visit-close {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.visit-report-dialog h2 {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 23px;
  line-height: 1.4;
  margin: 8px 0 12px;
  word-break: break-word;
}
.visit-report-dialog p {
  font-size: 14px;
  line-height: 1.8;
  color: #73604f;
}
.visit-report-dialog .visit-snapshot {
  font-size: 12px;
  background: #f5eee2;
  padding: 12px;
  border-radius: 10px;
}
.visit-report-dialog fieldset {
  margin: 20px 0;
  padding: 0;
  border: 0;
}
.visit-report-dialog legend {
  font-size: 14px;
  margin-bottom: 10px;
}
.visit-choice {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  cursor: pointer;
  font-size: 14px;
}
.visit-choice input {
  accent-color: #ec752b;
}
.visit-field {
  display: block;
  font-size: 13px;
  margin-top: 16px;
}
.visit-field input,
.visit-field textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  margin-top: 8px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 12px;
  font: inherit;
  background: white;
  color: inherit;
  min-height: 44px;
}
.visit-field textarea {
  resize: vertical;
}
.visit-report-dialog .btn {
  width: 100%;
  min-height: 44px;
}
.visit-report-dialog .visit-error {
  color: #a33e24;
  background: #fff0e7;
  border-radius: 10px;
  padding: 12px;
}
.visit-privacy {
  display: block;
  font-size: 11px;
  line-height: 1.7;
  color: #7b6858;
  margin-top: 14px;
}
@media (max-width: 430px) {
  .stall-visit {
    padding: 18px;
  }
  .visit-actions > * {
    flex: 1;
    white-space: nowrap;
  }
  .visit-report span {
    display: none;
  }
  .visit-report-dialog {
    padding: 21px;
  }
}
</style>
