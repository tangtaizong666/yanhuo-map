import { ref, watch, onUnmounted } from "vue";
import { useDiscovery } from "../stores/discovery";
import { useSession } from "../stores/session";
import { useRouter, useRoute } from "vue-router";
import { api } from "./api";
import { notify } from "./notify";
import type { Stall } from "./types";

const pendingFollowKey = "yanhuo-pending-follow-v1";
type PendingFollow = {
  stallId: number;
  returnTo: string;
  token: string;
  createdAt: number;
};
let pendingFollowMemory: PendingFollow | null = null;
function savePendingFollow(record: PendingFollow | null) {
  pendingFollowMemory = record;
  try {
    if (record)
      sessionStorage.setItem(pendingFollowKey, JSON.stringify(record));
    else sessionStorage.removeItem(pendingFollowKey);
  } catch {
    /* The intent still survives route changes in this tab. */
  }
}
export async function completePendingFollow(
  userId: number,
  returnTo: unknown,
  intentToken: unknown,
): Promise<"completed" | "none" | "expired" | "failed"> {
  let record = pendingFollowMemory;
  try {
    const stored = sessionStorage.getItem(pendingFollowKey);
    if (stored) record = JSON.parse(stored);
  } catch {
    /* Ignore malformed or unavailable storage. */
  }
  // Consume before any asynchronous work: another account must never inherit it.
  savePendingFollow(null);
  if (
    !record ||
    !Number.isInteger(record.stallId) ||
    record.stallId < 1 ||
    !intentToken ||
    record.token !== intentToken ||
    record.returnTo !== returnTo
  )
    return "none";
  if (
    !Number.isFinite(record.createdAt) ||
    Date.now() - record.createdAt > 600_000 ||
    record.createdAt > Date.now()
  ) {
    notify("之前的关注操作已过期，请回到摊位重新点关注。", "info");
    return "expired";
  }
  const session = useSession();
  if (session.user?.id !== userId || session.isMerchant) return "none";
  const controller = new AbortController();
  const stopIdentityWatch = watch(
    () => [session.user?.id, session.isMerchant],
    () => {
      if (session.user?.id !== userId || session.isMerchant) controller.abort();
    },
    { flush: "sync" },
  );
  try {
    await api<Stall>(`/stalls/${record.stallId}/follow`, {
      method: "POST",
      signal: controller.signal,
    });
    if (
      session.user?.id !== userId ||
      session.isMerchant ||
      controller.signal.aborted
    )
      return "none";
    notify("已关注这家小摊，下次从首页「我的关注」就能找到。", "success");
    return "completed";
  } catch (error) {
    if (
      session.user?.id === userId &&
      !session.isMerchant &&
      !controller.signal.aborted
    )
      notify(
        `关注未确认：${(error as Error).message}。请在摊位重新确认关注状态。`,
        "error",
      );
    return "failed";
  } finally {
    stopIdentityWatch();
  }
}

// Follow writes are idempotent on the server. Keep the chosen target explicit,
// and let their confirmed result win over reads started before they finished.
export function useFollowState(apply: (id: number, followed: boolean) => void) {
  const session = useSession(),
    router = useRouter(),
    route = useRoute();
  const pendingFollows = ref(new Set<number>());
  const controllers = new Map<number, AbortController>();
  const confirmed = new Map<number, { revision: number; followed: boolean }>();
  let revision = 0,
    identity = 0,
    disposed = false;
  function abortPending() {
    controllers.forEach((controller) => controller.abort());
    controllers.clear();
  }
  function snapshot() {
    return { revision, identity, userId: session.user?.id };
  }
  function isCurrent(read: ReturnType<typeof snapshot>) {
    return (
      !disposed &&
      read.identity === identity &&
      read.userId === session.user?.id
    );
  }
  function reconcile<T extends Stall>(
    stall: T,
    read: ReturnType<typeof snapshot>,
  ): T {
    const change = confirmed.get(stall.id);
    return change && change.revision > read.revision
      ? { ...stall, is_followed: change.followed }
      : stall;
  }
  watch(
    () => session.user?.id,
    () => {
      identity++;
      abortPending();
      confirmed.clear();
      pendingFollows.value.clear();
    },
    { flush: "sync" },
  );
  onUnmounted(() => {
    disposed = true;
    abortPending();
  });
  async function follow(stall: Stall, target = !stall.is_followed) {
    if (!session.user) {
      const token =
        globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
      if (target)
        savePendingFollow({
          stallId: stall.id,
          returnTo: route.fullPath,
          token,
          createdAt: Date.now(),
        });
      await router.push({
        path: "/login",
        query: {
          returnTo: route.fullPath,
          ...(target ? { followIntent: token } : {}),
        },
      });
      return;
    }
    const id = stall.id;
    if (disposed || pendingFollows.value.has(id)) return;
    const read = snapshot();
    const origin = route.fullPath;
    const controller = new AbortController();
    controllers.set(id, controller);
    pendingFollows.value.add(id);
    try {
      const result = await api<Stall>(`/stalls/${id}/follow`, {
        method: target ? "POST" : "DELETE",
        signal: controller.signal,
      });
      if (!isCurrent(read)) return;
      confirmed.set(id, { revision: ++revision, followed: result.is_followed });
      apply(id, result.is_followed);
      if (route.fullPath === origin)
        notify(
          result.is_followed ? "已关注，下次找摊更方便。" : "已取消关注",
          "success",
        );
    } catch (e) {
      if (isCurrent(read) && route.fullPath === origin)
        notify((e as Error).message, "error");
    } finally {
      if (controllers.get(id) === controller) controllers.delete(id);
      if (isCurrent(read)) pendingFollows.value.delete(id);
    }
  }
  return { pendingFollows, follow, snapshot, isCurrent, reconcile };
}

export function useStalls() {
  const filters = useDiscovery(),
    session = useSession(),
    stalls = ref<Stall[]>([]),
    loading = ref(true),
    error = ref("");
  const lastSyncedAt = ref<Date | null>(null);
  const followState = useFollowState((id, followed) => {
    const current = stalls.value.find((stall) => stall.id === id);
    if (current) current.is_followed = followed;
  });
  let seq = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function load() {
    const current = ++seq;
    const read = followState.snapshot();
    loading.value = true;
    error.value = "";
    const q = new URLSearchParams();
    if (filters.area) q.set("area", String(filters.area));
    if (filters.q) q.set("q", filters.q);
    if (filters.category) q.set("category", filters.category);
    if (filters.status) q.set("status", filters.status);
    q.set("sort", filters.sort);
    if (filters.position) {
      q.set("lat", String(filters.position.lat));
      q.set("lng", String(filters.position.lng));
    }
    try {
      const result = await api<Stall[]>("/stalls?" + q);
      if (current === seq && followState.isCurrent(read)) {
        stalls.value = result.map((stall) =>
          followState.reconcile(stall, read),
        );
        lastSyncedAt.value = new Date();
      }
    } catch (e) {
      if (current === seq) error.value = (e as Error).message;
    } finally {
      if (current === seq) loading.value = false;
    }
  }
  watch(
    () => [
      filters.area,
      filters.q,
      filters.category,
      filters.status,
      filters.sort,
      filters.position,
      session.user?.id,
    ],
    () => {
      seq++;
      clearTimeout(timer);
      timer = setTimeout(load, 180);
    },
    { immediate: true },
  );
  watch(
    () => session.user?.id,
    () => {
      stalls.value = [];
    },
  );
  const polling = window.setInterval(() => {
    if (!document.hidden) load();
  }, 60000);
  function resume() {
    if (document.hidden) return;
    clearTimeout(timer);
    // Focus and visibility often arrive together when returning from navigation.
    timer = setTimeout(load, 50);
  }
  window.addEventListener("focus", resume);
  document.addEventListener("visibilitychange", resume);
  onUnmounted(() => {
    seq++;
    clearTimeout(timer);
    clearInterval(polling);
    window.removeEventListener("focus", resume);
    document.removeEventListener("visibilitychange", resume);
  });
  return {
    stalls,
    loading,
    error,
    lastSyncedAt,
    load,
    follow: followState.follow,
    pendingFollows: followState.pendingFollows,
    filters,
  };
}
