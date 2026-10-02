<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { Copy, Download, QrCode, Share2, X } from "lucide-vue-next";
import QRCode from "qrcode";
import type { Stall } from "../lib/types";
import { copyText, trackEngagement } from "../lib/engagement";
import { useSession } from "../stores/session";

const props = defineProps<{ stall: Stall; compact?: boolean }>();
const session = useSession();
const dialog = ref<HTMLDialogElement>();
const qr = ref("");
const message = ref("");
const generating = ref(false);
const linkField = ref<HTMLInputElement>();
const configuredOrigin = computed(() => {
  try {
    const value = session.config?.public_base_url;
    if (!value) return "";
    const url = new URL(value);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password
    )
      return "";
    return url.origin;
  } catch {
    return "";
  }
});
const origin = computed(() => configuredOrigin.value || location.origin);
const localOnly = computed(() => !configuredOrigin.value);
const loopback = computed(() =>
  ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname),
);
const shareUrl = computed(
  () => `${origin.value}/stalls/${props.stall.id}?src=share_link`,
);
const qrUrl = computed(
  () => `${origin.value}/stalls/${props.stall.id}?src=stall_qr`,
);
const hasSystemShare = typeof navigator.share === "function";
let revision = 0;
async function open() {
  message.value = "";
  qr.value = "";
  dialog.value?.showModal();
  const current = ++revision;
  generating.value = true;
  try {
    const data = await QRCode.toDataURL(qrUrl.value, {
      width: 320,
      margin: 2,
      color: { dark: "#493226", light: "#ffffff" },
    });
    if (current === revision) qr.value = data;
  } catch {
    if (current === revision)
      message.value = "二维码生成失败，仍可复制下方链接。";
  } finally {
    if (current === revision) generating.value = false;
  }
}
async function copy() {
  if (await copyText(shareUrl.value)) {
    message.value = "链接已复制";
    trackEngagement("share_click", props.stall.id, "share_link");
  } else {
    message.value = "请长按下方链接复制，或选中后使用复制快捷键。";
    await nextTick();
    linkField.value?.focus();
    linkField.value?.select();
  }
}
async function systemShare() {
  try {
    await navigator.share({
      title: `${props.stall.name} · 烟火地图`,
      text: "出发前看看摊位的最新出摊信息",
      url: shareUrl.value,
    });
    trackEngagement("share_click", props.stall.id, "share_link");
  } catch (error) {
    if ((error as Error).name !== "AbortError")
      message.value = "暂时无法打开系统分享，请复制链接。";
  }
}
function close() {
  revision++;
  generating.value = false;
  dialog.value?.close();
}
watch(() => props.stall.id, close);
onUnmounted(() => {
  revision++;
});
onMounted(() => {
  if (location.pathname !== `/stalls/${props.stall.id}`) return;
  const source = new URLSearchParams(location.search).get("src");
  if (source === "stall_qr")
    trackEngagement("qr_open", props.stall.id, "stall_qr");
  if (source === "share_link")
    trackEngagement("share_open", props.stall.id, "share_link");
});
</script>

<template>
  <button
    type="button"
    :class="['stall-share-trigger', { compact }]"
    @click="open"
  >
    <Share2 :size="16" />分享小摊
  </button>
  <dialog
    ref="dialog"
    class="stall-share-dialog"
    aria-labelledby="stall-share-title"
    @click.self="close"
    @cancel="close"
  >
    <header>
      <span class="share-icon"><QrCode :size="25" /></span
      ><button
        type="button"
        class="icon-button"
        aria-label="关闭分享"
        @click="close"
      >
        <X :size="20" />
      </button>
    </header>
    <span class="eyebrow">GOOD TASTE, PASSED ALONG</span>
    <h2 id="stall-share-title">把好味道分享出去</h2>
    <p>
      {{ stall.name }}<br /><small
        >扫码查看最新出摊信息，出发前再确认一次。</small
      >
    </p>
    <div class="share-qr">
      <img
        v-if="qr"
        :src="qr"
        width="240"
        height="240"
        :alt="`${stall.name}的公开摊位二维码`"
      /><span v-else role="status">{{
        generating ? "正在生成二维码…" : "可使用下方摊位链接"
      }}</span>
    </div>
    <p v-if="localOnly" class="share-local" role="note">
      {{
        loopback
          ? "本机预览链接，仅当前电脑可打开。手机分享请从同网络地址打开本页后生成。"
          : "仅同网络体验。手机需与电脑连接同一可互访网络，电脑保持开机。"
      }}
    </p>
    <label class="share-link-label" for="public-stall-link">公开摊位链接</label
    ><input
      id="public-stall-link"
      ref="linkField"
      class="share-link"
      :value="shareUrl"
      readonly
      @focus="($event.target as HTMLInputElement).select()"
    />
    <p v-if="message" class="share-message" role="status">{{ message }}</p>
    <div class="share-actions">
      <button type="button" class="btn btn-primary" @click="copy">
        <Copy :size="16" />复制链接</button
      ><button
        v-if="hasSystemShare"
        type="button"
        class="btn btn-secondary"
        @click="systemShare"
      >
        <Share2 :size="16" />系统分享</button
      ><a
        v-if="qr"
        :href="qr"
        :download="`烟火地图-摊位${stall.id}.png`"
        class="btn btn-secondary"
        ><Download :size="16" />保存二维码</a
      >
    </div>
  </dialog>
</template>

<style scoped>
.stall-share-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 44px;
  padding: 10px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface, #fffaf4);
  color: var(--ink, #493226);
  font: inherit;
  cursor: pointer;
}
.stall-share-trigger:hover {
  border-color: var(--orange, #ef762d);
}
.stall-share-dialog {
  width: min(440px, calc(100vw - 28px));
  max-height: calc(100dvh - 32px);
  overflow: auto;
  margin: auto;
  border: 1px solid var(--line);
  border-radius: 24px;
  padding: 26px;
  color: var(--ink, #493226);
  background: #fffdf9;
  box-shadow: 0 22px 80px #38251f30;
}
.stall-share-dialog::backdrop {
  background: #2b211966;
  backdrop-filter: blur(4px);
}
.stall-share-dialog header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}
.share-icon {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  background: #fff0de;
  color: #bc5b1d;
}
.icon-button {
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
.stall-share-dialog h2 {
  margin: 8px 0 12px;
  font-size: 25px;
}
.stall-share-dialog p {
  font-size: 14px;
  line-height: 1.75;
  margin: 10px 0;
}
.stall-share-dialog small {
  color: #7b6858;
}
.share-qr {
  min-height: 240px;
  display: grid;
  place-items: center;
  margin: 14px auto;
}
.share-qr img {
  max-width: 100%;
  height: auto;
  border-radius: 14px;
}
.share-local {
  background: #fff0de;
  color: #855226;
  padding: 12px;
  border-radius: 12px;
}
.share-link-label {
  display: block;
  font-size: 13px;
  margin-bottom: 8px;
}
.share-link {
  width: 100%;
  box-sizing: border-box;
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px;
  font: inherit;
  font-size: 13px;
  background: white;
  color: inherit;
}
.share-message {
  color: #8c481b;
}
.share-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}
.share-actions .btn {
  flex: 1;
  white-space: nowrap;
  min-height: 44px;
}
@media (max-width: 430px) {
  .stall-share-dialog {
    padding: 22px;
  }
  .share-actions .btn {
    flex-basis: 40%;
  }
}
</style>
