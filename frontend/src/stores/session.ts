import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";
import { advanceSessionEpoch, sessionEpoch } from "../lib/sessionEpoch";
import { api } from "../lib/api";
import type { User, Config } from "../lib/types";
export const useSession = defineStore("session", () => {
  const user = ref<User | null>(null),
    config = ref<Config | null>(null),
    loaded = ref(false),
    consumerPreview = ref(false);
  const isMerchant = computed(
    () => !!user.value && (user.value.is_merchant || user.value.is_staff),
  );
  function setConsumerPreview(value: boolean) {
    consumerPreview.value = value && isMerchant.value;
    try {
      if (consumerPreview.value)
        sessionStorage.setItem(
          "yanhuo-consumer-preview",
          String(user.value!.id),
        );
      else sessionStorage.removeItem("yanhuo-consumer-preview");
    } catch {
      /* In-memory preview still works when browser storage is disabled. */
    }
  }
  let pending: Promise<void> | null = null;
  let identityRequest = 0;
  function beginIdentityChange() {
    identityRequest++;
    advanceSessionEpoch();
  }
  watch(
    () => user.value?.id ?? null,
    () => beginIdentityChange(),
    { flush: "sync" },
  );
  function expire(epoch: number) {
    if (epoch !== sessionEpoch()) return false;
    beginIdentityChange();
    user.value = null;
    setConsumerPreview(false);
    return true;
  }
  async function load() {
    if (loaded.value) return;
    if (pending) return pending;
    pending = (async () => {
      config.value = await api<Config>("/config");
      user.value = config.value.user;
      try {
        consumerPreview.value =
          isMerchant.value &&
          sessionStorage.getItem("yanhuo-consumer-preview") ===
            String(user.value?.id);
      } catch {
        consumerPreview.value = false;
      }
      loaded.value = true;
    })().finally(() => {
      pending = null;
    });
    return pending;
  }
  async function refreshUser() {
    const request = ++identityRequest;
    const epoch = sessionEpoch();
    const next = await api<User | null>("/auth/me");
    if (request !== identityRequest || epoch !== sessionEpoch()) return;
    if (next?.id !== user.value?.id) setConsumerPreview(false);
    user.value = next;
  }
  async function logout() {
    beginIdentityChange();
    await api("/auth/logout", { method: "POST" });
    identityRequest++;
    user.value = null;
    setConsumerPreview(false);
  }
  return {
    user,
    config,
    loaded,
    isMerchant,
    consumerPreview,
    setConsumerPreview,
    load,
    refreshUser,
    logout,
    expire,
    beginIdentityChange,
  };
});
