<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { Bell, BellOff } from "lucide-vue-next";
const props = defineProps<{
  userId: number;
  events: { id: string; title: string; body: string; url: string }[];
  ready: boolean;
}>();
const supported =
  typeof Notification !== "undefined" &&
  "serviceWorker" in navigator &&
  window.isSecureContext;
const enabled = ref(false),
  busy = ref(false),
  error = ref(""),
  permission = ref(supported ? Notification.permission : "default");
let baseline = false,
  generation = 0,
  registration: ServiceWorkerRegistration | undefined;
const seen = new Set<string>();
const label = computed(() =>
  !supported
    ? "当前浏览器或连接不支持系统提醒"
    : permission.value === "denied"
      ? "系统提醒已被浏览器阻止"
      : enabled.value
        ? "已开启本次会话系统提醒"
        : "开启本次会话系统提醒",
);
async function show(title: string, body: string, url: string, id: string) {
  if (!registration || !enabled.value || Notification.permission !== "granted")
    return;
  const target = new URL(url, window.location.origin);
  if (target.origin !== window.location.origin) return;
  await registration.showNotification(title, {
    body,
    tag: `yanhuo-${props.userId}-${id}`,
    icon: "/favicon.svg",
    data: { url: target.href },
  });
}
async function enable() {
  if (!supported || busy.value) return;
  busy.value = true;
  error.value = "";
  const current = generation;
  try {
    permission.value = await Notification.requestPermission();
    if (current !== generation || permission.value !== "granted") return;
    const reg = await navigator.serviceWorker.register(
      "/notification-worker.js",
    );
    if (current !== generation) return;
    registration = reg;
    const active = reg.active || reg.installing || reg.waiting;
    if (active && active.state !== "activated")
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          active.removeEventListener("statechange", changed);
          reject(new Error("系统提醒尚未就绪，请重试。"));
        }, 8000);
        function changed() {
          if (active!.state === "activated") {
            clearTimeout(timer);
            active!.removeEventListener("statechange", changed);
            resolve();
          }
        }
        active.addEventListener("statechange", changed);
        changed();
      });
    if (current !== generation) return;
    enabled.value = true;
    await show(
      "烟火地图 · 提醒测试",
      "这是一条试提醒。网页关闭或手机锁屏后不保证继续接收。",
      window.location.pathname,
      "test",
    );
  } catch (e) {
    if (current === generation) {
      enabled.value = false;
      error.value = (e as Error).message;
    }
  } finally {
    if (current === generation) busy.value = false;
  }
}
async function disable(ownerId = props.userId) {
  generation++;
  enabled.value = false;
  busy.value = false;
  if (registration)
    try {
      for (const n of await registration.getNotifications())
        if (n.tag.startsWith(`yanhuo-${ownerId}-`)) n.close();
    } catch {
      /* A stopped worker has nothing to deliver. */
    }
}
watch(
  () => [props.events, props.ready] as const,
  ([events]) => {
    if (!props.ready) return;
    for (const event of events) {
      if (baseline && !seen.has(event.id) && enabled.value)
        void show(event.title, event.body, event.url, event.id).catch(() => {
          error.value = "系统提醒未送达，请直接查看页面订单。";
        });
      seen.add(event.id);
    }
    baseline = true;
    if (seen.size > 300)
      [...seen].slice(0, seen.size - 300).forEach((id) => seen.delete(id));
  },
  { deep: true, immediate: true },
);
watch(
  () => props.userId,
  (_id, oldId) => {
    void disable(oldId);
    baseline = false;
    seen.clear();
  },
  { flush: "sync" },
);
onUnmounted(() => {
  void disable();
});
</script>
<template>
  <details class="session-notifications">
    <summary>
      <Bell :size="16" />系统提醒<span>{{
        enabled ? "本次会话已开启" : "查看提醒设置"
      }}</span>
    </summary>
    <p>{{ label }}</p>
    <p class="notification-note">
      页面收到新订单状态时发送提醒。关闭网页、切到后台或锁屏后不保证送达，请保持订单页在前台；这不是后台推送服务。
    </p>
    <p v-if="!supported" class="notification-note">
      普通局域网 HTTP 不支持此能力；当前仍可使用页面提醒，商家可开启声音提醒。
    </p>
    <p v-if="permission === 'denied'" class="notification-note">
      如需开启，请在浏览器的网站权限中允许通知，再刷新页面。
    </p>
    <button
      v-if="!enabled"
      type="button"
      :disabled="!supported || busy || permission === 'denied'"
      @click="enable"
    >
      <Bell :size="15" />{{ busy ? "正在准备…" : "开启并发送试提醒" }}
    </button>
    <button v-else type="button" @click="disable()">
      <BellOff :size="15" />关闭本次会话提醒
    </button>
    <p v-if="error" role="alert">{{ error }}</p>
  </details>
</template>
<style scoped>
.session-notifications {
  border: 1px solid #eddfcb;
  border-radius: 14px;
  padding: 0 14px;
  margin: 12px 0;
  background: #fffaf3;
  font-size: 13px;
  color: #6e5c47;
}
.session-notifications summary {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  min-height: 46px;
}
.session-notifications summary span {
  margin-left: auto;
  font-size: 12px;
  color: #8c552b;
}
.session-notifications p {
  line-height: 1.7;
  margin: 10px 0;
}
.notification-note {
  font-size: 12px;
}
.session-notifications button {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  border: 1px solid #e9cba7;
  border-radius: 10px;
  padding: 8px 12px;
  color: #9c4210;
  margin: 12px 0;
}
.session-notifications button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
