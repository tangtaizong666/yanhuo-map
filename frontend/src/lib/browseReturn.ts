import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import type {
  NavigationFailure,
  RouteLocationNormalized,
  RouteLocationRaw,
  Router,
} from "vue-router";
import { useRoute, useRouter } from "vue-router";
import { useSession } from "../stores/session";
import { useDiscovery } from "../stores/discovery";
import { notify } from "./notify";

type ViewState = {
  pages?: { stalls: number; meals: number };
  map?: { center: [number, number]; zoom: number; selectedId: number | null };
  selectedId?: number | null;
};
type Snapshot = {
  anchor: string;
  region: string;
  anchorIndex: number;
  offset: number;
  container: boolean;
  windowY: number;
  listY: number;
  filters: string;
  view: ViewState;
};
type Entry = {
  key: string;
  path: string;
  position: number;
  scope: string;
  origin?: string;
  snapshot?: Snapshot;
};
type RouteTier =
  | "consumer-root"
  | "consumer-hub"
  | "consumer-context"
  | "consumer-leaf"
  | "merchant-root"
  | "merchant-section"
  | "ops-root"
  | "ops-section"
  | "ops-login"
  | "other";
export type ReturnControl = {
  signal: AbortSignal;
  active: () => boolean;
  wait: (ready: () => boolean) => Promise<boolean>;
};
type Adapter = {
  capture: () => ViewState;
  restore: (snapshot: Snapshot, control: ReturnControl) => Promise<boolean>;
};
const entries = new Map<string, Entry>();
const stackByPosition = new Map<number, string>();
const adapters = new Map<string, Adapter>();
const currentKey = ref("");
const revision = ref(0);
let pending: Entry | null = null;
let cancelRestore: (() => void) | undefined;
let initialized = false;
let serial = 0;
let clickedLink: { element: HTMLAnchorElement; source: string } | null = null;
const pageSession = Math.random().toString(36).slice(2);

function scope() {
  const session = useSession();
  return `${session.user?.id ?? "guest"}:${session.isMerchant || session.isAdmin ? session.consumerPreview : "student"}:${useDiscovery().area}`;
}
function filterFingerprint() {
  const f = useDiscovery();
  return JSON.stringify([
    f.area,
    f.q,
    f.category,
    f.status,
    f.sort,
    f.position,
  ]);
}
function routePath(fullPath: string) {
  return fullPath.split(/[?#]/, 1)[0] || "/";
}
function routeTier(path: string): RouteTier {
  if (path === "/") return "consumer-root";
  if (["/search", "/map", "/cart", "/orders", "/me"].includes(path))
    return "consumer-hub";
  if (
    /^\/(stalls|checkout)\/\d+$/.test(path) ||
    /^\/orders\/[^/]+$/.test(path)
  )
    return "consumer-context";
  if (
    /^\/stalls\/\d+\/products\/\d+$/.test(path) ||
    ["/login", "/recover", "/account-security"].includes(path)
  )
    return "consumer-leaf";
  if (path === "/merchant") return "merchant-root";
  if (path.startsWith("/merchant/")) return "merchant-section";
  if (path === "/ops/login") return "ops-login";
  if (path === "/ops") return "ops-root";
  if (path.startsWith("/ops/")) return "ops-section";
  return "other";
}
function sourcePath(path: string) {
  return (
    ["/", "/search", "/map", "/cart", "/orders", "/me"].includes(path) ||
    /^\/(stalls|checkout)\/\d+$/.test(path) ||
    /^\/orders\/[^/]+$/.test(path)
  );
}
function detailPath(path: string) {
  return /^\/stalls\/\d+(\/products\/\d+)?$/.test(path);
}
function returnablePath(path: string) {
  return (
    detailPath(path) ||
    ["/search", "/map", "/cart", "/orders", "/me"].includes(path) ||
    /^\/checkout\/\d+$/.test(path) ||
    /^\/orders\/[^/]+$/.test(path)
  );
}
function linksFor(path: string, region = "") {
  return [
    ...document.querySelectorAll<HTMLAnchorElement>("main a[href]"),
  ].filter(
    (link) =>
      link.getClientRects().length &&
      new URL(link.href).pathname === path &&
      (!region || link.closest(region)),
  );
}
function linkFor(path: string, region = "", index?: number) {
  const links = linksFor(path, region);
  if (index !== undefined) return links[index];
  const active = document.activeElement;
  return (
    links.find((link) => link === active || link.contains(active)) || links[0]
  );
}
function capture(path: string, destination: string): Snapshot {
  const clicked =
    clickedLink?.source === currentKey.value ? clickedLink.element : undefined;
  const anchor =
    clicked?.isConnected && new URL(clicked.href).pathname === destination
      ? clicked
      : linkFor(destination);
  const list = anchor?.closest<HTMLElement>(".map-results");
  const region =
    [
      ".nearby-section",
      ".meal-search-section",
      ".meal-inspiration",
      ".map-results",
      ".selected-stall",
    ].find((selector) => anchor?.closest(selector)) || "";
  return {
    anchor: destination,
    region,
    anchorIndex: anchor ? linksFor(destination, region).indexOf(anchor) : 0,
    offset: anchor
      ? anchor.getBoundingClientRect().top -
        (list?.getBoundingClientRect().top || 0)
      : 0,
    container: !!list,
    windowY: window.scrollY,
    listY: document.querySelector(".map-results")?.scrollTop || 0,
    filters: filterFingerprint(),
    view: adapters.get(path)?.capture() || {},
  };
}
function label(path: string) {
  if (path === "/search") return "返回搜索结果";
  if (path === "/map") return "返回地图找摊";
  if (path === "/cart") return "返回餐袋";
  if (path === "/orders") return "返回我的订单";
  if (path === "/me") return "返回个人中心";
  if (path.startsWith("/checkout/")) return "返回订单确认";
  if (path.startsWith("/orders/")) return "返回订单详情";
  if (path.startsWith("/stalls/")) return "返回菜单";
  return "返回附近摊位";
}
function validOrigin() {
  // Records only exist in this tab's memory. A copied URL or forged history state
  // cannot manufacture a trusted source entry after refresh or account change.
  revision.value;
  const current = entries.get(currentKey.value);
  const origin = current?.origin && entries.get(current.origin);
  return origin &&
    origin.scope === scope() &&
    current?.scope === scope() &&
    history.state?.back === origin.path &&
    history.state?.position === origin.position + 1
    ? origin
    : null;
}

export function useDetailReturn(fallback: () => string, fallbackLabel: string) {
  const router = useRouter();
  const origin = computed(validOrigin);
  return {
    target: computed(() => origin.value?.path || fallback()),
    label: computed(() =>
      origin.value
        ? label(origin.value.path.split(/[?#]/, 1)[0]!)
        : fallbackLabel,
    ),
    go(event: MouseEvent) {
      if (
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      event.preventDefault();
      if (validOrigin()) router.back();
      else void router.push(fallback());
    },
  };
}

export function useBrowseReturn(adapter: Adapter) {
  const route = useRoute();
  const path = route.path;
  adapters.set(path, adapter);
  onUnmounted(() => {
    if (adapters.get(path) === adapter) adapters.delete(path);
  });
}
export function returnViewState(path: string) {
  return pending?.path.split(/[?#]/, 1)[0] === path
    ? pending.snapshot?.view
    : undefined;
}
export function hasPendingBrowseReturn() {
  return !!pending;
}

async function restore(entry: Entry, router: Router) {
  const snapshot = entry.snapshot!;
  const controller = new AbortController();
  let userCancelled = false;
  const cancel = () => {
    userCancelled = true;
    controller.abort();
  };
  cancelRestore = cancel;
  const eventNames = ["wheel", "touchstart", "pointerdown", "keydown"] as const;
  eventNames.forEach((name) =>
    window.addEventListener(name, cancel, { capture: true, passive: true }),
  );
  const timeout = window.setTimeout(() => controller.abort(), 5000);
  const control: ReturnControl = {
    signal: controller.signal,
    active: () =>
      !controller.signal.aborted &&
      currentKey.value === entry.key &&
      entry.scope === scope() &&
      router.currentRoute.value.fullPath === entry.path &&
      snapshot.filters === filterFingerprint(),
    async wait(ready) {
      while (this.active()) {
        if (ready()) return true;
        await new Promise((resolve) => window.setTimeout(resolve, 25));
      }
      return false;
    },
  };
  let restored = false;
  try {
    await nextTick();
    // Mounted views register before the first animation frame. The adapter reads
    // new results/cursors, never a cached business record or expired cursor.
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const adapter = adapters.get(router.currentRoute.value.path);
    const aborted = new Promise<boolean>((resolve) => {
      if (controller.signal.aborted) resolve(false);
      else
        controller.signal.addEventListener("abort", () => resolve(false), {
          once: true,
        });
    });
    const ready = await Promise.race([
      adapter
        ? adapter.restore(snapshot, control)
        : control.wait(
            () =>
              !!linkFor(snapshot.anchor, snapshot.region, snapshot.anchorIndex),
          ),
      aborted,
    ]);
    if (!ready || !control.active()) return;
    await nextTick();
    const anchor = linkFor(
      snapshot.anchor,
      snapshot.region,
      snapshot.anchorIndex,
    );
    if (!anchor || !control.active()) return;
    const list = document.querySelector<HTMLElement>(".map-results");
    if (list) list.scrollTop = snapshot.listY;
    if (snapshot.container && list) {
      window.scrollTo({ top: snapshot.windowY, behavior: "instant" });
      list.scrollTop +=
        anchor.getBoundingClientRect().top -
        list.getBoundingClientRect().top -
        snapshot.offset;
    } else {
      window.scrollTo({
        top: Math.max(
          0,
          window.scrollY + anchor.getBoundingClientRect().top - snapshot.offset,
        ),
        behavior: "instant",
      });
    }
    anchor.focus({ preventScroll: true });
    restored = true;
  } finally {
    clearTimeout(timeout);
    eventNames.forEach((name) =>
      window.removeEventListener(name, cancel, true),
    );
    if (cancelRestore === cancel) cancelRestore = undefined;
    if (pending === entry) pending = null;
    if (
      !restored &&
      !userCancelled &&
      currentKey.value === entry.key &&
      entry.scope === scope()
    ) {
      window.scrollTo({ top: 0, behavior: "instant" });
      notify("列表已更新，暂时无法回到原位置，请查看当前结果。", "info");
    }
  }
}

function hasAncestorContext(curPos: number, currentScope: string) {
  for (let pos = curPos - 1; pos >= 0; pos--) {
    const anc = entries.get(stackByPosition.get(pos) || "");
    if (
      anc &&
      anc.scope === currentScope &&
      anc.position === pos &&
      routeTier(routePath(anc.path)) === "consumer-context"
    ) {
      return true;
    }
  }
  return false;
}

let activeGo: Promise<NavigationFailure | void | undefined> | null = null;
function goDelta(
  router: Router,
  delta: number,
): Promise<NavigationFailure | void | undefined> {
  if (activeGo) return activeGo;
  const promise = new Promise<NavigationFailure | void | undefined>(
    (resolve) => {
      let settled = false;
      const done = (failure?: NavigationFailure | void) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        stopAfter();
        stopError();
        if (activeGo === promise) activeGo = null;
        resolve(failure);
      };
      const stopAfter = router.afterEach((_to, _from, failure) => {
        done(failure);
      });
      const stopError = router.onError(() => {
        done();
      });
      const timer = window.setTimeout(() => done(), 400);
      router.go(delta);
    },
  );
  activeGo = promise;
  return promise;
}

export function installBrowseReturn(router: Router) {
  let nextOrigin: string | undefined;
  let popKey: string | undefined;
  let prevEntry: Entry | undefined;

  router.beforeEach((to, from) => {
    if (!initialized) {
      initialized = true;
      document.addEventListener(
        "click",
        (event) => {
          clickedLink = null;
          if (
            event.button !== 0 ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey
          )
            return;
          const element =
            event.target instanceof Element
              ? event.target.closest<HTMLAnchorElement>("main a[href]")
              : null;
          if (element) clickedLink = { element, source: currentKey.value };
        },
        true,
      );
      watch(
        scope,
        () => {
          cancelRestore?.();
          pending = null;
          entries.clear();
          stackByPosition.clear();
          currentKey.value = "";
          clickedLink = null;
          revision.value++;
        },
        { flush: "sync" },
      );
    }
    cancelRestore?.();
    pending = null;
    nextOrigin = undefined;
    const historyKey = history.state?.yanhuoBrowse;
    const destination =
      typeof historyKey === "string" && entries.get(historyKey);
    popKey =
      destination &&
      historyKey !== currentKey.value &&
      destination.path === to.fullPath
        ? historyKey
        : undefined;
    let current = entries.get(currentKey.value);
    if (
      !current &&
      from.matched.length &&
      history.state?.current === from.fullPath
    ) {
      current = {
        key: `${pageSession}:${++serial}`,
        path: from.fullPath,
        position: Number(history.state.position),
        scope: scope(),
      };
      entries.set(current.key, current);
      stackByPosition.set(current.position, current.key);
      currentKey.value = current.key;
      history.replaceState({ ...history.state, yanhuoBrowse: current.key }, "");
    }
    prevEntry = current;
    if (
      popKey &&
      destination &&
      current?.origin === popKey &&
      destination.snapshot &&
      destination.scope === scope() &&
      destination.snapshot.filters === filterFingerprint()
    ) {
      pending = destination;
    } else if (
      !popKey &&
      current &&
      to.path !== from.path &&
      current.scope === scope()
    ) {
      if (sourcePath(from.path) && detailPath(to.path)) {
        current.snapshot = capture(from.path, to.path);
        nextOrigin = current.key;
      } else if (sourcePath(from.path) && returnablePath(to.path)) {
        current.snapshot = undefined;
        nextOrigin = current.key;
      }
    }
    clickedLink = null;
  });

  router.afterEach((to: RouteLocationNormalized, _from, failure) => {
    if (failure) {
      pending = null;
      return;
    }
    const position = Number(history.state?.position ?? 0);
    const currentScope = scope();
    const existing = popKey ? entries.get(popKey) : undefined;
    const wasReplaced =
      !existing &&
      !!prevEntry &&
      prevEntry.scope === currentScope &&
      prevEntry.position === position;
    const inheritedOrigin = wasReplaced
      ? returnablePath(to.path)
        ? prevEntry?.origin
        : undefined
      : nextOrigin;
    if (existing) existing.position = position;
    const entry: Entry = existing || {
      key: `${pageSession}:${++serial}`,
      path: to.fullPath,
      position,
      scope: currentScope,
      origin: inheritedOrigin,
    };
    history.replaceState({ ...history.state, yanhuoBrowse: entry.key }, "");
    currentKey.value = entry.key;
    entries.set(entry.key, entry);
    stackByPosition.set(position, entry.key);
    if (!existing && !wasReplaced) {
      for (const pos of [...stackByPosition.keys()]) {
        if (pos > position) stackByPosition.delete(pos);
      }
    }
    // Bound metadata to recent navigation, not the lifetime of an open tab.
    if (entries.size > 60) entries.delete(entries.keys().next().value!);
    revision.value++;
    if (pending === entry) void restore(entry, router).catch(() => {});
  });

  const rawPush = router.push.bind(router);
  router.push = (to: RouteLocationRaw) => {
    const current = router.currentRoute.value;
    if (!current.matched.length) return rawPush(to);
    const target = router.resolve(to);
    if (target.fullPath === current.fullPath) return Promise.resolve();

    const currentScope = scope();
    const curPos = Number(history.state?.position ?? -1);
    const curEntry = entries.get(currentKey.value);

    // 1. If the target route is already an ancestor on the active tab stack,
    // unwind directly back to that ancestor instead of stacking a cycle.
    if (
      curEntry &&
      curEntry.scope === currentScope &&
      curEntry.position === curPos &&
      curPos > 0
    ) {
      for (let pos = curPos - 1; pos >= 0; pos--) {
        const anc = entries.get(stackByPosition.get(pos) || "");
        if (
          anc &&
          anc.scope === currentScope &&
          anc.position === pos &&
          anc.path === target.fullPath
        ) {
          return goDelta(router, pos - curPos);
        }
      }
    }

    // 2. Same-pathname query/hash refinements replace in-place (e.g. /search?q=a -> /search?q=b).
    if (target.path === current.path) {
      return router.replace(to);
    }

    const fromTier = routeTier(current.path);
    const toTier = routeTier(target.path);

    // 3. Merchant & Ops console section tabs stay at max depth 1 above their console home.
    if (toTier === "merchant-root" || toTier === "ops-root") {
      return router.replace(to);
    }
    if (
      (fromTier === "merchant-section" && toTier === "merchant-section") ||
      (fromTier === "ops-section" && toTier === "ops-section")
    ) {
      return router.replace(to);
    }

    // 4. Consumer hierarchy: prevent unbounded nesting across hubs, stalls, carts, checkouts, and dishes.
    if (toTier === "consumer-root" && fromTier.startsWith("consumer-")) {
      return router.replace(to);
    }

    if (toTier === "consumer-hub") {
      if (fromTier === "consumer-hub" || fromTier === "consumer-leaf") {
        return router.replace(to);
      }
      if (fromTier === "consumer-context") {
        if (
          target.path === "/cart" &&
          !hasAncestorContext(curPos, currentScope)
        ) {
          return rawPush(to);
        }
        return router.replace(to);
      }
    }

    if (toTier === "consumer-context") {
      if (fromTier === "consumer-leaf") {
        return router.replace(to);
      }
      if (fromTier === "consumer-context") {
        const forwardToCheckout =
          /^\/stalls\/\d+$/.test(current.path) &&
          /^\/checkout\/\d+$/.test(target.path) &&
          !hasAncestorContext(curPos, currentScope);
        const orderToStall =
          /^\/orders\/[^/]+$/.test(current.path) &&
          /^\/stalls\/\d+$/.test(target.path) &&
          !hasAncestorContext(curPos, currentScope);
        if (!forwardToCheckout && !orderToStall) {
          return router.replace(to);
        }
      }
    }

    if (toTier === "consumer-leaf" && fromTier === "consumer-leaf") {
      const loginToRecover =
        current.path === "/login" && target.path === "/recover";
      if (!loginToRecover) {
        return router.replace(to);
      }
    }

    return rawPush(to);
  };
}
