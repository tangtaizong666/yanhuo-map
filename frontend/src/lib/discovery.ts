import { ref, watch, onUnmounted } from "vue";
import { useDiscovery } from "../stores/discovery";
import { useSession } from "../stores/session";
import { useRouter, useRoute } from "vue-router";
import { api } from "./api";
import { notify } from "./notify";
import type {
  Stall,
  StallSummary,
  StallMap,
  DiscoveryPage,
  DiscoveredMeal,
} from "./types";
import { mergeDiscoveryRows } from "./orderQuantity";
import type { Ref } from "vue";

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
  function reconcile<T extends { id: number; is_followed: boolean }>(
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
  async function follow(
    stall: { id: number; is_followed: boolean },
    target = !stall.is_followed,
  ) {
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
      const result = await api<{ id: number; is_followed: boolean }>(
        `/stalls/${id}/follow`,
        {
          method: target ? "POST" : "DELETE",
          signal: controller.signal,
        },
      );
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

export function useStalls<T extends StallSummary | StallMap = StallSummary>(
  kind: "list" | "map" = "list",
  bounds?: Ref<string>,
) {
  const filters = useDiscovery(),
    session = useSession(),
    route = useRoute();
  const stalls = ref<T[]>([]),
    loading = ref(true),
    error = ref("");
  const next = ref<string | null>(null),
    truncated = ref(false);
  const lastSyncedAt = ref<Date | null>(null);
  const loadedPages = ref(0);
  const loadedBounds = ref("");
  const followState = useFollowState((id, followed) => {
    const current = stalls.value.find((stall) => stall.id === id);
    if (current) current.is_followed = followed;
    if (!followed && route.query.follow === "1")
      stalls.value = stalls.value.filter((stall) => stall.id !== id);
  });
  let seq = 0,
    controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  async function load(append = false) {
    if (append && (!next.value || loading.value)) return;
    controller?.abort();
    controller = new AbortController();
    const current = ++seq,
      read = followState.snapshot();
    loading.value = true;
    error.value = "";
    const q = discoveryParams(filters);
    if (route.query.follow === "1") q.set("follow", "1");
    if (bounds?.value && kind === "map") q.set("bounds", bounds.value);
    if (append && next.value) q.set("cursor", next.value);
    try {
      const result = await api<DiscoveryPage<T> & { truncated?: boolean }>(
        `${kind === "map" ? "/stalls/map" : "/stalls"}?${q}`,
        { signal: controller.signal },
      );
      if (current === seq && followState.isCurrent(read)) {
        const rows = result.results.map((stall) =>
          followState.reconcile(stall, read),
        );
        // ref unwraps generic values; the public records contain no refs.
        stalls.value = (
          append ? mergeDiscoveryRows(stalls.value as T[], rows) : rows
        ) as typeof stalls.value;
        next.value = result.next || null;
        loadedPages.value = append ? loadedPages.value + 1 : 1;
        truncated.value = !!result.truncated;
        lastSyncedAt.value = new Date();
        loadedBounds.value = q.get("bounds") || "";
      }
    } catch (cause) {
      if (current === seq) error.value = (cause as Error).message;
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
      route.query.follow,
      bounds?.value,
    ],
    (current, previous) => {
      if (
        previous &&
        current.every((value, index) => Object.is(value, previous[index]))
      )
        return;
      seq++;
      controller?.abort();
      clearTimeout(timer);
      next.value = null;
      loadedPages.value = 0;
      if (kind === "list") stalls.value = [];
      timer = setTimeout(() => load(), 180);
    },
    { immediate: true },
  );
  watch(
    () => session.user?.id,
    () => {
      stalls.value = [];
    },
    { flush: "sync" },
  );
  const polling = window.setInterval(() => {
    if (!document.hidden && !loading.value) void load();
  }, 60000);
  function resume() {
    if (!document.hidden) {
      clearTimeout(timer);
      timer = setTimeout(() => load(), 50);
    }
  }
  window.addEventListener("focus", resume);
  document.addEventListener("visibilitychange", resume);
  onUnmounted(() => {
    seq++;
    controller?.abort();
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
    next,
    truncated,
    loadedPages,
    loadedBounds,
    load: () => load(),
    loadMore: () => load(true),
    follow: followState.follow,
    pendingFollows: followState.pendingFollows,
    filters,
  };
}

function discoveryParams(filters: ReturnType<typeof useDiscovery>) {
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
  return q;
}

export function useMeals(budget: Ref<number>, sort: Ref<string>) {
  const filters = useDiscovery(),
    session = useSession(),
    route = useRoute();
  const meals = ref<DiscoveredMeal[]>([]),
    loading = ref(true),
    error = ref(""),
    next = ref<string | null>(null);
  const loadedPages = ref(0);
  let sequence = 0,
    controller: AbortController | undefined,
    timer: ReturnType<typeof setTimeout> | undefined;
  async function load(append = false) {
    if (append && (!next.value || loading.value)) return;
    controller?.abort();
    controller = new AbortController();
    const current = ++sequence;
    loading.value = true;
    error.value = "";
    const q = discoveryParams(filters);
    if (route.query.follow === "1") q.set("follow", "1");
    if (budget.value) q.set("budget", String(budget.value));
    q.set("meal_sort", sort.value);
    if (append && next.value) q.set("cursor", next.value);
    try {
      const result = await api<DiscoveryPage<DiscoveredMeal>>(
        `/products?${q}`,
        { signal: controller.signal },
      );
      if (current === sequence) {
        meals.value = append
          ? [
              ...new Map(
                [...meals.value, ...result.results].map((row) => [
                  row.product.id,
                  row,
                ]),
              ).values(),
            ]
          : result.results;
        next.value = result.next;
        loadedPages.value = append ? loadedPages.value + 1 : 1;
      }
    } catch (cause) {
      if (current === sequence) error.value = (cause as Error).message;
    } finally {
      if (current === sequence) loading.value = false;
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
      route.query.follow,
      budget.value,
      sort.value,
    ],
    (current, previous) => {
      if (
        previous &&
        current.every((value, index) => Object.is(value, previous[index]))
      )
        return;
      sequence++;
      controller?.abort();
      clearTimeout(timer);
      meals.value = [];
      next.value = null;
      loadedPages.value = 0;
      timer = setTimeout(() => load(), 180);
    },
    { immediate: true },
  );
  const polling = window.setInterval(() => {
    if (!document.hidden && !loading.value) void load();
  }, 60000);
  onUnmounted(() => {
    sequence++;
    controller?.abort();
    clearTimeout(timer);
    clearInterval(polling);
  });
  return {
    meals,
    loading,
    error,
    next,
    loadedPages,
    load: () => load(),
    loadMore: () => load(true),
  };
}
