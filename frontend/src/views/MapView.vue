<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Search,
  MapPin,
  Navigation,
  Compass,
  ArrowUpRight,
  LocateFixed,
  RefreshCw,
  X,
  Clock3,
  Copy,
  Check,
  ChevronDown,
  Map as MapIcon,
  Store,
} from "lucide-vue-next";
import { useStalls } from "../lib/discovery";
import { useSession } from "../stores/session";
import { loadAMap, locate } from "../lib/amap";
import { api, confirmedText, statusText } from "../lib/api";
import { copyText } from "../lib/engagement";
import { notify } from "../lib/notify";
import type { Stall, StallMap } from "../lib/types";
import StallCard from "../components/StallCard.vue";
import DiscoveryFilters from "../components/DiscoveryFilters.vue";
import StallVisitInfo from "../components/StallVisitInfo.vue";
import StallShare from "../components/StallShare.vue";
import { returnViewState, useBrowseReturn } from "../lib/browseReturn";
const route = useRoute(), router = useRouter();
const bounds = ref("");
const {
    stalls: allStalls,
    loading,
    error,
    load,
    follow,
    pendingFollows,
    filters,
    truncated,
    lastSyncedAt,
    loadedBounds,
  } = useStalls<StallMap>("map", bounds),
  session = useSession(),
  mapNode = ref<HTMLDivElement | null>(null),
  selectedPanel = ref<HTMLDivElement | null>(null),
  selectedVisit = ref<HTMLDivElement | null>(null),
  visitLoadingHeight = ref(0),
  selected = ref<Stall | null>(null),
  selectedId = ref<number | null>(null),
  selectionLoading = ref(false),
  selectionError = ref(""),
  mapError = ref(""),
  mapLoading = ref(false),
  locating = ref(false);
const followOnly = computed(() => route.query.follow === "1");
const stalls = computed(() =>
  followOnly.value
    ? allStalls.value.filter((s) => s.is_followed)
    : allStalls.value,
);
let savedStatusBeforeFollow = "";
watch(
  followOnly,
  (only) => {
    if (only) {
      savedStatusBeforeFollow = filters.status;
      filters.status = "";
    } else if (savedStatusBeforeFollow && !filters.status) {
      filters.status = savedStatusBeforeFollow;
      savedStatusBeforeFollow = "";
    } else {
      savedStatusBeforeFollow = "";
    }
  },
  { immediate: true },
);
let map: any = null,
  markers: any[] = [],
  routeOverlays: any[] = [],
  unmounted = false,
  userMarker: any = null;
const pickingOrigin = ref(false),
  pickingMyPosition = ref(false),
  customOrigin = ref<{ lat: number; lng: number; label: string } | null>(null),
  selectedOriginKey = ref(""),
  routeLoading = ref(false),
  routeCopied = ref(false),
  routePlan = ref<{
    distanceMeters: number;
    durationMinutes: number;
    mode: "walking" | "straight";
    bearing: string;
    path: [number, number][];
    steps: { instruction: string; distance: number }[];
  } | null>(null);
let routeController: AbortController | null = null;
const area = computed(() =>
  session.config?.areas.find((a) => a.id === filters.area),
);
const routeTarget = computed(() => {
  if (route.query.route !== "1" || typeof route.query.to !== "string") return null;
  const [lngRaw, latRaw] = route.query.to.split(",");
  const lng = Number(lngRaw),
    lat = Number(latRaw);
  if (
    !Number.isFinite(lat) ||
    !Number.isFinite(lng) ||
    lat < -90 ||
    lat > 90 ||
    lng < -180 ||
    lng > 180
  )
    return null;
  const stallRaw = route.query.stall ? Number(route.query.stall) : null;
  return {
    lat,
    lng,
    name:
      typeof route.query.name === "string" && route.query.name.trim()
        ? route.query.name.trim()
        : "目标取餐点",
    address:
      typeof route.query.addr === "string" ? route.query.addr.trim() : "",
    stallId:
      stallRaw != null && Number.isInteger(stallRaw) && stallRaw > 0
        ? stallRaw
        : null,
  };
});
const originPresets = computed(() => {
  const list: { key: string; label: string; lat: number; lng: number }[] = [];
  if (filters.position) {
    list.push({
      key: "user",
      label: "我的定位",
      lat: filters.position.lat,
      lng: filters.position.lng,
    });
  }
  if (customOrigin.value) {
    list.push({
      key: "custom",
      label: customOrigin.value.label,
      lat: customOrigin.value.lat,
      lng: customOrigin.value.lng,
    });
  }
  const areas = session.config?.areas?.length
    ? session.config.areas
    : [
        {
          id: -1,
          name: "征仪路正门",
          subtitle: "哈理工南区",
          latitude: 45.711192,
          longitude: 126.634053,
        },
      ];
  for (const item of areas) {
    if (Number.isFinite(item.latitude) && Number.isFinite(item.longitude)) {
      list.push({
        key: `area:${item.id}`,
        label: item.name.replace(/^哈尔滨理工大学南区[·\s]*/, ""),
        lat: item.latitude,
        lng: item.longitude,
      });
    }
  }
  return list;
});
function haversineMeters(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
) {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  return Math.max(1, Math.round(6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))));
}
function compassBearing(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
) {
  const dy = lat2 - lat1;
  const dx = (lng2 - lng1) * Math.cos((lat1 * Math.PI) / 180);
  if (Math.hypot(dx, dy) < 0.00005) return "就在附近";
  const angle = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
  const dirs = ["正北", "东北", "正东", "东南", "正南", "西南", "正西", "西北"];
  return `向${dirs[Math.round(angle / 45) % 8]}`;
}
const activeOrigin = computed(() => {
  const presets = originPresets.value;
  if (!presets.length) return null;
  const matched = presets.find((p) => p.key === selectedOriginKey.value);
  if (matched) return matched;
  if (filters.position) return presets.find((p) => p.key === "user") || presets[0];
  if (area.value) {
    const areaPreset = presets.find((p) => p.key === `area:${area.value!.id}`);
    if (areaPreset) return areaPreset;
  }
  if (routeTarget.value) {
    const t = routeTarget.value;
    return [...presets].sort(
      (a, b) =>
        haversineMeters(a.lat, a.lng, t.lat, t.lng) -
        haversineMeters(b.lat, b.lng, t.lat, t.lng),
    )[0];
  }
  return presets[0];
});
function clearRouteOverlays() {
  if (map && routeOverlays.length) {
    map.remove(routeOverlays);
  }
  routeOverlays = [];
}
function drawRouteOnMap() {
  clearRouteOverlays();
  if (!map || !window.AMap || !routeTarget.value || !activeOrigin.value || !routePlan.value)
    return;
  const AMap = window.AMap;
  const polyline = new AMap.Polyline({
    path: routePlan.value.path,
    strokeColor: "#d95f18",
    strokeWeight: 6,
    strokeOpacity: 0.92,
    lineJoin: "round",
    lineCap: "round",
    showDir: true,
    zIndex: 60,
  });
  const startEl = document.createElement("span");
  startEl.className = "route-endpoint-pin route-start-pin";
  startEl.textContent = "起";
  const endEl = document.createElement("span");
  endEl.className = "route-endpoint-pin route-end-pin";
  endEl.textContent = "摊";
  const startMarker = new AMap.Marker({
    position: [activeOrigin.value.lng, activeOrigin.value.lat],
    content: startEl,
    anchor: "center",
    zIndex: 120,
  });
  const endMarker = new AMap.Marker({
    position: [routeTarget.value.lng, routeTarget.value.lat],
    content: endEl,
    anchor: "center",
    zIndex: 121,
  });
  routeOverlays = [polyline, startMarker, endMarker];
  map.add(routeOverlays);
  map.setFitView?.(routeOverlays, false, [84, 44, 72, 44], 18);
}
async function updateWalkingRoute() {
  routeController?.abort();
  const target = routeTarget.value;
  const origin = activeOrigin.value;
  if (!target || !origin) {
    routeLoading.value = false;
    routePlan.value = null;
    clearRouteOverlays();
    return;
  }
  const straight = haversineMeters(origin.lat, origin.lng, target.lat, target.lng);
  const bearing = compassBearing(origin.lat, origin.lng, target.lat, target.lng);
  const fallbackPlan = {
    distanceMeters: straight,
    durationMinutes: Math.max(1, Math.round(straight / 72)),
    mode: "straight" as const,
    bearing,
    path: [
      [origin.lng, origin.lat],
      [target.lng, target.lat],
    ] as [number, number][],
    steps: [],
  };
  if (!session.config?.amap_key || !session.config?.amap_proxy) {
    routePlan.value = fallbackPlan;
    routeLoading.value = false;
    drawRouteOnMap();
    return;
  }
  const controller = new AbortController();
  routeController = controller;
  routeLoading.value = true;
  try {
    const query = new URLSearchParams({
      origin: `${origin.lng.toFixed(6)},${origin.lat.toFixed(6)}`,
      destination: `${target.lng.toFixed(6)},${target.lat.toFixed(6)}`,
      s: "rsv3",
    });
    const proxyBase = session.config.amap_proxy.replace(/\/+$/, "");
    const serviceBase = proxyBase.endsWith("/_AMapService")
      ? proxyBase
      : `${proxyBase}/_AMapService`;
    const response = await fetch(
      `${serviceBase}/v3/direction/walking?${query.toString()}`,
      { credentials: "include", signal: controller.signal },
    );
    const data = response.ok ? await response.json() : null;
    if (controller.signal.aborted || unmounted) return;
    const firstPath = data?.status === "1" ? data?.route?.paths?.[0] : null;
    if (firstPath) {
      const coords: [number, number][] = [[origin.lng, origin.lat]];
      const steps: { instruction: string; distance: number }[] = [];
      for (const rawStep of Array.isArray(firstPath.steps) ? firstPath.steps : []) {
        const text = String(rawStep?.instruction || "").replace(/<[^>]+>/g, "").trim();
        if (text) {
          steps.push({
            instruction: text,
            distance: Number(rawStep?.distance) || 0,
          });
        }
        for (const pair of String(rawStep?.polyline || "").split(";")) {
          const [pLng, pLat] = pair.split(",").map(Number);
          if (Number.isFinite(pLng) && Number.isFinite(pLat)) {
            coords.push([pLng, pLat]);
          }
        }
      }
      coords.push([target.lng, target.lat]);
      const dist = Number(firstPath.distance) || straight;
      const durSec = Number(firstPath.duration) || Math.round(dist / 1.2);
      routePlan.value = {
        distanceMeters: dist,
        durationMinutes: Math.max(1, Math.round(durSec / 60)),
        mode: "walking",
        bearing,
        path: coords,
        steps,
      };
    } else {
      routePlan.value = fallbackPlan;
    }
    drawRouteOnMap();
  } catch {
    if (controller.signal.aborted || unmounted) return;
    routePlan.value = fallbackPlan;
    drawRouteOnMap();
  } finally {
    if (routeController === controller) routeLoading.value = false;
  }
}
function selectOrigin(key: string) {
  pickingOrigin.value = false;
  selectedOriginKey.value = key;
}
function togglePickOrigin() {
  if (mapError.value || !map) {
    notify("当前地图画布未开启，可直接点选校区大门作为起点。", "info");
    return;
  }
  pickingMyPosition.value = false;
  pickingOrigin.value = !pickingOrigin.value;
  if (pickingOrigin.value) {
    notify("请在地图上轻点你当前所在的位置作为步行起点", "info");
  }
}
function closeRoute() {
  pickingOrigin.value = false;
  routeController?.abort();
  routePlan.value = null;
  clearRouteOverlays();
  const nextQuery: Record<string, string> = {};
  if (followOnly.value) nextQuery.follow = "1";
  void router.replace({ path: "/map", query: nextQuery });
}
async function copyRouteDestination() {
  if (!routeTarget.value) return;
  const text = [routeTarget.value.name, routeTarget.value.address]
    .filter(Boolean)
    .join(" · ");
  if (await copyText(text)) {
    routeCopied.value = true;
    notify("摊位位置已复制", "success");
    window.setTimeout(() => {
      routeCopied.value = false;
    }, 2200);
  } else {
    notify(`位置：${text}`, "info");
  }
}
function applyPickedUserPosition(lat: number, lng: number, message: string) {
  filters.position = { lat, lng };
  if (map && window.AMap) {
    if (userMarker) map.remove(userMarker);
    userMarker = new window.AMap.Marker({
      position: [lng, lat],
      content: '<span class="user-position-dot"></span>',
      anchor: "center",
    });
    map.add(userMarker);
  }
  notify(message, "success");
}
async function init() {
  if (!session.config?.amap_key) {
    mapError.value = "地图服务暂未开放";
    return;
  }
  mapLoading.value = true;
  mapError.value = "";
  try {
    const AMap = await loadAMap(session.config);
    await nextTick();
    if (unmounted || !mapNode.value) return;
    map?.destroy();
    const restoredMap = returnViewState("/map")?.map;
    map = new AMap.Map(mapNode.value, {
      zoom: restoredMap?.zoom || 16,
      center: restoredMap?.center || (routeTarget.value
        ? [routeTarget.value.lng, routeTarget.value.lat]
        : area.value
          ? [area.value.longitude, area.value.latitude]
          : [126.634053, 45.711192]),
      viewMode: "2D",
      mapStyle: "amap://styles/whitesmoke",
    });
    map.addControl?.(new AMap.Scale());
    map.on("moveend", syncBounds);
    map.on?.("click", (event: any) => {
      const lat = event?.lnglat?.getLat?.() ?? event?.lnglat?.lat;
      const lng = event?.lnglat?.getLng?.() ?? event?.lnglat?.lng;
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      if (pickingOrigin.value) {
        pickingOrigin.value = false;
        customOrigin.value = { lat, lng, label: "地图自选起点" };
        selectedOriginKey.value = "custom";
        notify("已按你点选的位置更新步行路线", "success");
        return;
      }
      if (pickingMyPosition.value) {
        pickingMyPosition.value = false;
        applyPickedUserPosition(lat, lng, "已按地图点选位置计算距离");
      }
    });
    syncBounds();
    renderMarkers();
    if (routePlan.value) drawRouteOnMap();
  } catch (e) {
    mapError.value = (e as Error).message;
  } finally {
    mapLoading.value = false;
  }
}
function renderMarkers() {
  if (!map || !window.AMap) return;
  map.remove(markers);
  markers = stalls.value.map((s, i) => {
    const content = document.createElement("button");
    content.className =
      "food-map-pin " +
      s.status +
      (s.id === selected.value?.id ? " selected" : "");
    content.type = "button";
    content.setAttribute("aria-label", s.name);
    if (s.image) {
      const image = document.createElement("img");
      image.src = s.image;
      image.alt = "";
      content.appendChild(image);
    } else {
      const initial = document.createElement("span");
      initial.className = "food-map-pin-initial";
      initial.textContent = s.name.slice(0, 1) || "摊";
      content.appendChild(initial);
    }
    const marker = new window.AMap.Marker({
      position: [s.longitude, s.latitude],
      content,
      anchor: "bottom-center",
    });
    marker.on("click", () => selectStall(s));
    return marker;
  });
  map.add(markers);
}
function syncBounds() {
  const extent = map?.getBounds();
  if (!extent) return;
  const sw = extent.getSouthWest(),
    ne = extent.getNorthEast();
  bounds.value = [sw.lng, sw.lat, ne.lng, ne.lat]
    .map((value: number) => value.toFixed(6))
    .join(",");
}
let selectionSequence = 0,
  selectionController: AbortController | undefined;
let selectionTrigger: HTMLElement | undefined;
let selectionNeedsReveal = false;
let selectionFocus: HTMLElement | null = null;
function clearSelection() {
  selectionNeedsReveal = false;
  selectionFocus = null;
  visitLoadingHeight.value = 0;
  selectionSequence++;
  selectionController?.abort();
  selectedId.value = null;
  selected.value = null;
  selectionLoading.value = false;
  selectionError.value = "";
  renderMarkers();
}
async function readSelectedStall(id: number) {
  selectionController?.abort();
  selectionController = new AbortController();
  const current = ++selectionSequence;
  const active = document.activeElement;
  if (active instanceof HTMLElement && selectedVisit.value?.contains(active)) {
    selectionFocus = active;
  } else if (active !== document.body) {
    selectionFocus = null;
  }
  if (!selectionLoading.value) visitLoadingHeight.value = selectedVisit.value?.getBoundingClientRect().height || 0;
  selectionLoading.value = true;
  selectionError.value = "";
  try {
    const detail = await api<Stall>(`/stalls/${id}`, {
      signal: selectionController.signal,
    });
    if (current !== selectionSequence || unmounted || selectedId.value !== id) return;
    if (!stalls.value.some(stall => stall.id === id)) {
      clearSelection();
      return;
    }
    selected.value = detail;
    renderMarkers();
  } catch (cause) {
    if (current === selectionSequence && !unmounted) {
      selectionError.value = (cause as Error).message;
      if (!selected.value) notify(selectionError.value, "error");
    }
  } finally {
    if (current === selectionSequence && !unmounted) selectionLoading.value = false;
  }
  // A refresh can replace the first request without consuming the user's choice.
  // Once shown, ordinary background updates keep reading position and focus.
  if (selectionNeedsReveal && mapError.value && !selectionError.value &&
      current === selectionSequence && !unmounted) {
    await nextTick();
    if (current !== selectionSequence || unmounted || !selectedPanel.value) return;
    selectionNeedsReveal = false;
    selectedPanel.value?.focus({ preventScroll: true });
    selectedPanel.value?.scrollIntoView({ block: "start" });
  } else if (selectionFocus && current === selectionSequence && !unmounted) {
    await nextTick();
    if (current !== selectionSequence || unmounted) return;
    const target = selectionFocus;
    selectionFocus = null;
    // Hiding stale guidance can drop keyboard focus to the document. Restore it
    // only if the user has not moved to another control while the read was pending.
    if (document.activeElement === document.body) {
      (target?.isConnected && target.getClientRects().length ? target : selectedPanel.value)?.focus({ preventScroll: true });
    }
  }
}
function selectStall(s: StallMap, event?: Event) {
  selectionTrigger = event?.currentTarget instanceof HTMLElement ? event.currentTarget : undefined;
  selectionNeedsReveal = true;
  if (selectedId.value !== s.id) selected.value = null;
  selectedId.value = s.id;
  if (map) map.panTo([s.longitude, s.latitude]);
  if (!mapError.value && window.matchMedia?.("(max-width: 767px)").matches) mapNode.value?.parentElement?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  void readSelectedStall(s.id);
}
function closeSelection() {
  clearSelection();
  if (selectionTrigger?.isConnected) selectionTrigger.focus();
}
function showAllStalls() {
  filters.q = "";
  filters.category = "";
  filters.status = "";
  void router.push("/map");
}
function refreshSelected() {
  if (selectedId.value !== null) void readSelectedStall(selectedId.value);
}
async function findMe() {
  if (!session.config) return;
  if (typeof window !== "undefined" && window.isSecureContext === false) {
    if (map && !mapError.value) {
      pickingOrigin.value = false;
      pickingMyPosition.value = !pickingMyPosition.value;
      notify(
        pickingMyPosition.value
          ? "局域网访问限制自动定位：请直接在地图上轻点你所在的位置。"
          : "已取消地图选点定位。",
        "info",
      );
    } else {
      notify(
        "局域网访问限制自动定位，可在上方选择校区浏览附近摊位。",
        "info",
      );
    }
    return;
  }
  locating.value = true;
  try {
    filters.position = await locate(session.config);
    map?.setZoomAndCenter(16, [filters.position.lng, filters.position.lat]);
    if (map) {
      if (userMarker) map.remove(userMarker);
      userMarker = new window.AMap.Marker({
        position: [filters.position.lng, filters.position.lat],
        content: '<span class="user-position-dot"></span>',
        anchor: "center",
      });
      map.add(userMarker);
    }
    notify("已定位，距离按当前位置计算。", "success");
  } catch (e) {
    notify((e as Error).message, "info");
  } finally {
    locating.value = false;
  }
}
watch(
  () => [
    routeTarget.value?.lat,
    routeTarget.value?.lng,
    routeTarget.value?.name,
    activeOrigin.value?.key,
    activeOrigin.value?.lat,
    activeOrigin.value?.lng,
  ],
  () => {
    if (routeTarget.value && selectedId.value !== null) {
      clearSelection();
    }
    if (routeTarget.value && window.matchMedia?.("(max-width: 767px)").matches) {
      void nextTick(() => {
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    }
    void updateWalkingRoute();
  },
  { immediate: true },
);
watch(stalls, () => {
  // A summary cannot update arrival photos, notes or closing time. Refresh the
  // complete selected record instead of combining different location snapshots.
  if (selectedId.value !== null) {
    if (stalls.value.some(s => s.id === selectedId.value)) refreshSelected();
    else clearSelection();
  }
  renderMarkers();
});
watch(
  () => [filters.area, filters.q, filters.category, filters.status, filters.sort,
    session.user?.id, route.query.follow],
  (current, previous) => {
    if (previous && current.every((value, index) => Object.is(value, previous[index]))) return;
    clearSelection();
  },
  { flush: "sync" },
);
watch(area, (a) => {
  if (a && map) map.setZoomAndCenter(16, [a.longitude, a.latitude]);
});
useBrowseReturn({
  capture() {
    const center = map?.getCenter();
    return {
      selectedId: selectedId.value,
      ...(center ? { map: { center: [center.lng, center.lat] as [number, number], zoom: map.getZoom(), selectedId: selectedId.value } } : {}),
    };
  },
  async restore(snapshot, control) {
    if (!await control.wait(() => !mapLoading.value && !loading.value &&
      (!!error.value || (!!lastSyncedAt.value && loadedBounds.value === bounds.value)))) return false;
    if (error.value) return false;
    const id = snapshot.view.selectedId;
    if (id == null) return true;
    if (!stalls.value.some((stall) => stall.id === id)) return false;
    const abortSelection = () => clearSelection();
    control.signal.addEventListener("abort", abortSelection, { once: true });
    try {
      selectedId.value = id;
      // Restore an identity, not the old directions or the pan/reveal side effects
      // of a new user click. The details must pass the normal fresh-read gate.
      selectionNeedsReveal = false;
      await readSelectedStall(id);
      return control.active() && !selectionError.value && selected.value?.id === id;
    } finally {
      control.signal.removeEventListener("abort", abortSelection);
    }
  },
});
onMounted(init);
onUnmounted(() => {
  unmounted = true;
  selectionController?.abort();
  routeController?.abort();
  map?.destroy();
});
</script>
<template>
  <div class="page map-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">循味找摊</p>
        <h1>循着烟火，找到好味。</h1>
      </div>
      <button
        v-if="session.config?.amap_key"
        class="btn btn-ghost locate-top"
        :disabled="locating"
        @click="findMe"
      >
        <LocateFixed :size="16" />{{ locating ? "正在定位" : "定位我的位置" }}
      </button>
    </div>
    <div class="map-layout" :class="{ 'map-fallback': !!mapError }">
      <aside class="map-sidebar">
        <div class="map-list-controls">
          <div class="search-box">
            <Search :size="17" /><input
              v-model="filters.q"
              placeholder="想在地图上找什么？"
              aria-label="地图搜索"
            /><button
              v-if="filters.q"
              @click="filters.q = ''"
              aria-label="清空"
            >
              <X :size="14" />
            </button>
          </div>
          <DiscoveryFilters />
          <div class="filter-chips">
            <select v-model="filters.category" aria-label="地图品类">
              <option value="">全部品类</option>
              <option
                v-for="c in ['小吃', '烧烤', '饮品', '正餐', '甜品']"
                :value="c"
              >
                {{ c }}
              </option>
            </select>
          </div>
          <div class="map-result-count">
            <span
              >{{ area?.name || "校园附近" }} · {{ stalls.length }} 个摊位</span
            ><button @click="load" aria-label="刷新摊位">
              <RefreshCw :size="13" />
            </button>
          </div>
        </div>
        <div class="map-results">
          <p v-if="truncated" class="error-message" role="status">
            当前范围摊位较多，仅展示前 200
            个。{{ mapError ? "请选择校园区域缩小范围。" : "请放大地图或选择校园区域缩小范围。" }}
          </p>
          <div v-if="error" class="error-message" role="status">
            暂未同步最新状态。{{
              stalls.length
                ? "下方为上次加载的摊位，出发前请刷新确认。"
                : error
            }}<button
              class="btn btn-secondary"
              @click="load"
              :disabled="loading"
            >
              刷新摊位
            </button>
          </div>
          <div v-else-if="followOnly && !session.user" class="empty-state">
            <p>登录后查看你关注的小摊。</p>
            <RouterLink
              :to="{ path: '/login', query: { returnTo: route.fullPath } }"
              class="btn btn-primary"
              >登录查看关注</RouterLink
            >
          </div>
          <div v-else-if="loading && !stalls.length" class="empty-state">
            <span class="spinner"></span>
          </div>
          <div v-else-if="!stalls.length" class="empty-state">
            <Search :size="26" />
            <p>换个条件，找找其他好味道。</p>
            <button v-if="filters.q || filters.category || filters.status || followOnly" class="btn btn-secondary" @click="showAllStalls">看看全部摊位</button>
          </div>
          <div
            v-for="s in stalls"
            :key="s.id"
            :class="['map-list-item', { selected: selected?.id === s.id }]"
          >
            <article class="stall-card map-summary-card">
              <RouterLink :to="`/stalls/${s.id}`"
                ><img
                  v-if="s.image"
                  :src="s.image"
                  :alt="s.name"
                  width="72"
                  height="56"
                  loading="lazy"
                /><strong>{{ s.name }}</strong></RouterLink
              >
              <span :class="['badge', s.status]">{{
                statusText(s.status)
              }}</span>
              <p>{{ s.address }}</p>
              <small>{{ confirmedText(s.last_confirmed_at) }}</small>
              <div class="map-summary-actions">
                <button
                  class="btn btn-secondary map-select"
                  :disabled="selectedId === s.id && selectionLoading"
                  @click="selectStall(s, $event)"
                >
                  {{ selectedId === s.id && selectionLoading ? "正在核对…" : selected?.id === s.id ? "已选中" : mapError ? "查看位置与路线" : "在地图中查看" }}
                </button>
                <button
                  class="btn btn-ghost"
                  :disabled="pendingFollows.has(s.id)"
                  :aria-label="`${s.is_followed ? '取消关注' : '关注'}${s.name}`"
                  @click="follow(s)"
                >
                  {{ s.is_followed ? "已关注" : "关注" }}
                </button>
              </div>
            </article>
          </div>
        </div>
      </aside>
      <section
        class="map-canvas"
        :class="{ 'has-active-route': !!routeTarget, 'is-picking-point': pickingOrigin || pickingMyPosition }"
        aria-label="摊位地图"
      >
        <div ref="mapNode" class="map-container"></div>
        <section
          v-if="routeTarget"
          class="route-planner-card"
          aria-label="站内步行路线"
        >
          <div class="route-planner-head">
            <div class="route-planner-title">
              <span class="route-badge"><Navigation :size="14" />站内步行路线</span>
              <h2>前往「{{ routeTarget.name }}」</h2>
              <p v-if="routeTarget.address" class="route-dest-address">
                {{ routeTarget.address }}
              </p>
            </div>
            <button
              type="button"
              class="route-close-btn"
              aria-label="关闭路线指引"
              @click="closeRoute"
            >
              <X :size="16" />
            </button>
          </div>

          <div class="route-origins" role="group" aria-label="选择步行起点">
            <span class="route-origins-label">起点：</span>
            <button
              v-for="preset in originPresets"
              :key="preset.key"
              type="button"
              :class="[
                'route-origin-chip',
                { active: activeOrigin?.key === preset.key && !pickingOrigin },
              ]"
              @click="selectOrigin(preset.key)"
            >
              {{ preset.label }}
            </button>
            <button
              v-if="!mapError"
              type="button"
              :class="['route-origin-chip', 'pick-chip', { active: pickingOrigin }]"
              @click="togglePickOrigin"
            >
              <Compass :size="13" />{{ pickingOrigin ? "轻点地图选起点…" : "点图选起点" }}
            </button>
          </div>

          <div v-if="routeLoading" class="route-metrics" role="status">
            正在计算校园步行路线…
          </div>
          <div v-else-if="routePlan" class="route-metrics">
            <strong>
              {{
                routePlan.mode === "walking"
                  ? `步行约 ${routePlan.distanceMeters} 米 · 约 ${routePlan.durationMinutes} 分钟`
                  : `直线约 ${routePlan.distanceMeters} 米 · 预计步行 ${routePlan.durationMinutes} 分钟（${routePlan.bearing}）`
              }}
            </strong>
            <small>
              从「{{ activeOrigin?.label || "校区起点" }}」出发 · 无需跳转外部 App
            </small>
          </div>

          <details
            v-if="routePlan?.steps.length"
            class="route-steps-details"
          >
            <summary>
              <span>查看分段步行指引（{{ routePlan.steps.length }} 段）</span>
              <ChevronDown :size="15" />
            </summary>
            <ol class="route-steps-list">
              <li v-for="(step, idx) in routePlan.steps" :key="idx">
                <span>{{ step.instruction }}</span>
                <small v-if="step.distance">{{ step.distance }}米</small>
              </li>
            </ol>
          </details>

          <div class="route-planner-actions">
            <RouterLink
              v-if="routeTarget.stallId"
              :to="`/stalls/${routeTarget.stallId}`"
              class="btn btn-secondary"
            >
              <Store :size="15" />返回摊位
            </RouterLink>
            <button
              type="button"
              class="btn btn-ghost"
              @click="copyRouteDestination"
            >
              <Check v-if="routeCopied" :size="15" />
              <Copy v-else :size="15" />
              {{ routeCopied ? "已复制位置" : "复制地址" }}
            </button>
          </div>
        </section>
        <p
          v-if="pickingMyPosition && !routeTarget"
          class="map-pick-banner"
          role="status"
        >
          <Compass :size="15" />请在地图上轻点你所在的位置计算距离
          <button type="button" @click="pickingMyPosition = false">取消</button>
        </p>
        <div v-if="mapError" class="map-unavailable">
          <MapIcon :size="23" aria-hidden="true" />
          <div>
            <h2>{{ mapError }}</h2>
            <p>可从列表查看摊位地址与到摊指引。</p>
          </div>
          <button
            v-if="session.config?.amap_key"
            class="btn btn-secondary"
            @click="init"
          >
            <RefreshCw :size="15" />重新加载地图</button
          >
        </div>
        <div v-if="mapLoading" class="map-loader">
          <span class="spinner"></span><span>正在铺开烟火地图…</span>
        </div>
        <div v-if="!mapError && !routeTarget" class="map-legend">
          <span class="legend-dot"></span>出摊中<span
            class="legend-dot muted-dot"
          ></span
          >暂歇 / 待确认
        </div>
        <button
          v-if="session.config?.amap_key"
          class="map-location icon-button"
          :disabled="locating"
          @click="findMe"
          aria-label="定位我的位置"
        >
          <LocateFixed :size="20" />
        </button>
        <div v-if="selected" ref="selectedPanel" class="selected-stall" tabindex="-1" role="region" :aria-label="`${selected.name}的位置与路线`">
          <img v-if="selected.image" :src="selected.image" :alt="selected.name" />
          <div v-else class="selected-stall-placeholder" aria-hidden="true">
            <Store :size="24" />
          </div>
          <div class="selected-info">
            <span v-if="!selectionLoading && !selectionError" :class="['badge', selected.status]">{{
              statusText(selected.status)
            }}</span>
            <h3>{{ selected.name }}</h3>
            <p v-if="!selectionLoading && !selectionError">
              {{
                selected.can_order
                  ? "可线上点单"
                  : selected.transaction_enabled && selected.accepting_orders === false
                    ? "线上接单暂停 · 到摊选购"
                    : "线下到访"
              }}
            </p>
          </div>
          <button
            class="close-selection"
            @click="closeSelection"
            aria-label="关闭选中摊位"
          >
            <X :size="15" />
          </button>
          <div ref="selectedVisit" class="selected-visit" :style="{ minHeight: selectionLoading ? `${visitLoadingHeight}px` : undefined }">
            <p v-if="selectionLoading" role="status">正在核对最新出摊信息…</p>
            <p v-else-if="selectionError" class="error-message" role="alert">
              暂未同步最新到摊指引，请重新核对后再出发。
              <button class="btn btn-secondary" @click="refreshSelected">重新核对</button>
            </p>
            <div v-show="!selectionLoading && !selectionError">
              <StallVisitInfo :key="selected.id" :stall="selected" compact />
            </div>
          </div>
          <div class="selected-actions">
            <RouterLink :to="`/stalls/${selected.id}`" class="btn btn-primary"
              >查看摊位<ArrowUpRight :size="14"
            /></RouterLink>
            <StallShare :stall="selected" compact />
          </div>
        </div>
      </section>
    </div>
    <details class="map-footnote-details">
      <summary>
        <Clock3 :size="13" />
        <span>位置更新与距离计算说明</span>
        <ChevronDown :size="14" />
      </summary>
      <p class="map-footnote">
        营业位置由商家每次出摊确认更新，出发前请留意最新状态；列表距离按直线估算，点击「查看路线」可在站内查看步行路线。
      </p>
    </details>
  </div>
</template>
<style scoped>
.map-summary-card {
  padding: 16px;
  border: 1px solid #eee1d3;
  border-radius: 14px;
  background: #fffaf3;
}
.map-summary-card > a {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  margin-bottom: 10px;
}
.map-summary-card img {
  object-fit: cover;
  border-radius: 10px;
}
.map-summary-card p {
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.map-summary-card small {
  color: #75624f;
}
.map-summary-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.selected-visit {
  grid-column: 1/-1;
  min-width: 0;
}
.map-list-controls :deep(.discovery-filters) {
  flex-wrap: wrap;
  margin-top: 10px;
  margin-bottom: 8px;
}
.map-list-controls :deep(.sort-control) {
  margin-left: auto;
}
.map-page .page-heading h1 {
  font-family: "Noto Serif SC", serif;
  font-size: 27px;
}
.map-layout {
  display: grid;
  grid-template-columns: 390px minmax(0, 1fr);
  height: 680px;
  border: 1px solid var(--line);
  border-radius: 20px;
  overflow: hidden;
  background: #fff;
}
.map-sidebar {
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.map-list-controls {
  padding: 20px 18px 10px;
}
.map-list-controls .filter-chips {
  margin-top: 13px;
}
.filter-chips select {
  border: 0;
  background: none;
  color: #8a7964;
  font-size: 12px;
  margin-left: auto;
  min-height: 44px;
  max-width: none;
  white-space: nowrap;
}
.map-result-count {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: #a08e78;
  margin-top: 15px;
}
.map-result-count button {
  width: 44px;
  height: 44px;
}
.map-results {
  overflow: auto;
  padding: 0 14px 20px;
  scrollbar-width: thin;
  scrollbar-color: #e8d9c6 transparent;
}
.map-list-item {
  margin-bottom: 10px;
  border-radius: 14px;
}
.map-list-item.selected {
  outline: 1px solid #d68b54;
}
.map-list-item :deep(.compact) {
  border: 0;
  box-shadow: none;
  padding-bottom: 3px;
}
.map-select {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: #a9794a;
  margin: 0 auto 0 0;
  min-height: 44px;
}
.map-canvas {
  position: relative;
  min-height: 400px;
  background: #f1f0e7;
  isolation: isolate;
}
.map-container {
  height: 100%;
  width: 100%;
  position: absolute;
  inset: 0;
}
.map-unavailable {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 20px;
  color: #75624f;
}
.map-unavailable > svg {
  flex-shrink: 0;
  margin-top: 3px;
}
.map-unavailable h2 {
  font-size: 17px;
  margin: 0;
}
.map-unavailable p {
  font-size: 14px;
  margin: 5px 0 0;
  line-height: 1.7;
}
.map-unavailable .btn {
  margin-left: auto;
}
.map-fallback {
  display: flex;
  flex-direction: column-reverse;
  height: auto;
  min-height: 0;
}
.map-fallback .map-sidebar {
  border: 0;
  padding: 0 6px;
}
.map-fallback .map-canvas {
  min-height: 0;
  height: auto;
  background: #fffaf1;
}
.map-fallback .map-container {
  display: none;
}
.map-fallback .map-results {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr));
  align-items: start;
  gap: 12px;
  max-height: none;
}
.map-fallback .map-results > .empty-state,
.map-fallback .map-results > .error-message {
  grid-column: 1 / -1;
}
.map-fallback .map-results > .empty-state {
  min-height: 0;
  padding: 32px 20px;
}
.map-fallback .selected-stall {
  position: relative;
  inset: auto;
  max-height: none;
  overflow: visible;
  width: calc(100% - 32px);
  max-width: 760px;
  margin: 0 auto 16px;
  scroll-margin-top: 90px;
}
.map-fallback .selected-stall :deep(.visit-photo img) {
  object-fit: contain;
  background: #f6efe4;
}
.map-location {
  position: absolute;
  right: 18px;
  bottom: 20px;
  z-index: 3;
  color: #9c7950;
  border-radius: 13px;
  box-shadow: 0 3px 10px #54301411;
}
.map-legend {
  position: absolute;
  left: 20px;
  top: 20px;
  background: #fffdf7ed;
  border-radius: 7px;
  padding: 9px 12px;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 10px;
  color: #857d6f;
  box-shadow: 0 2px 8px #00000008;
}
.legend-dot {
  width: 6px;
  height: 6px;
  background: #718c62;
  border-radius: 50%;
}
.muted-dot {
  background: #b1a58d;
  margin-left: 10px;
}
.map-loader {
  position: absolute;
  inset: 0;
  background: #fff9;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  font-size: 13px;
  color: #947553;
}
.selected-stall {
  position: absolute;
  left: 24px;
  right: 72px;
  bottom: 20px;
  border-radius: 15px;
  background: #fffcf7f5;
  backdrop-filter: blur(10px);
  border: 1px solid #ede1d0;
  padding: 16px;
  box-shadow: 0 8px 30px #382c1212;
  display: grid;
  grid-template-columns: 70px 1fr;
  gap: 13px;
  max-height: calc(100% - 40px);
  overflow-y: auto;
}
.selected-stall > img,
.selected-stall-placeholder {
  width: 70px;
  height: 80px;
  border-radius: 10px;
  object-fit: cover;
}
.selected-stall-placeholder {
  background: #f5ead9;
  color: #9e7348;
  display: grid;
  place-items: center;
}
.selected-info {
  min-width: 0;
  padding-right: 36px;
}
.selected-info h3 {
  font-size: 16px;
  margin: 5px 0;
}
.selected-info p {
  font-size: 10px;
  color: #a58c68;
  display: flex;
  align-items: center;
  gap: 4px;
}
.selected-info small {
  font-size: 10px;
  color: #a69b87;
}
.close-selection {
  position: absolute;
  right: 8px;
  top: 8px;
  width: 44px;
  height: 44px;
}
.selected-actions {
  grid-column: 1/-1;
  display: flex;
  gap: 8px;
}
.selected-actions .btn {
  min-height: 44px;
  font-size: 11px;
  flex: 1;
  padding: 8px 12px;
}
.map-canvas.is-picking-point .map-container {
  cursor: crosshair;
}
.route-planner-card {
  position: absolute;
  left: 18px;
  top: 18px;
  z-index: 4;
  width: min(392px, calc(100% - 84px));
  max-height: calc(100% - 36px);
  overflow-y: auto;
  border-radius: 16px;
  background: rgba(255, 252, 246, 0.97);
  backdrop-filter: blur(10px);
  border: 1px solid #e7d5bd;
  box-shadow: 0 12px 32px rgba(56, 36, 18, 0.14);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.map-fallback .route-planner-card {
  position: relative;
  left: auto;
  top: auto;
  width: calc(100% - 32px);
  max-width: 760px;
  max-height: none;
  margin: 14px auto;
}
.route-planner-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.route-planner-title {
  min-width: 0;
}
.route-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 700;
  color: #b45118;
  background: #fdead9;
  border-radius: 999px;
  padding: 3px 9px;
}
.route-planner-title h2 {
  font-size: 16px;
  margin: 6px 0 2px;
  color: #35261a;
  overflow-wrap: anywhere;
}
.route-dest-address {
  margin: 0;
  font-size: 12px;
  color: #7b6651;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.route-close-btn {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  border-radius: 12px;
  display: grid;
  place-items: center;
  color: #7b6651;
  background: #f6ede0;
}
.route-origins {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.route-origins-label {
  font-size: 12px;
  font-weight: 600;
  color: #7d6751;
}
.route-origin-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 38px;
  padding: 6px 11px;
  border-radius: 999px;
  border: 1px solid #e4d4be;
  background: #fffdf9;
  color: #6b5540;
  font-size: 12px;
  font-weight: 500;
}
.route-origin-chip.active {
  background: #d95f18;
  border-color: #d95f18;
  color: #fff;
  font-weight: 600;
}
.route-origin-chip.pick-chip {
  border-style: dashed;
}
.route-metrics {
  background: #fbf3e6;
  border: 1px solid #ecdac2;
  border-radius: 12px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 13px;
  color: #6f563e;
}
.route-metrics strong {
  font-size: 14px;
  color: #8f3e12;
}
.route-metrics small {
  font-size: 11px;
  color: #88725b;
}
.route-steps-details {
  border: 1px solid #eadbc7;
  border-radius: 12px;
  background: #fffdf9;
  padding: 0 12px;
}
.route-steps-details summary {
  list-style: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 44px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  color: #6c543d;
}
.route-steps-details summary::-webkit-details-marker {
  display: none;
}
.route-steps-details[open] summary svg {
  transform: rotate(180deg);
}
.route-steps-list {
  margin: 0;
  padding: 2px 0 12px 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-top: 1px dashed #eadbc7;
}
.route-steps-list li {
  font-size: 12px;
  color: #5b4735;
  line-height: 1.55;
}
.route-steps-list small {
  margin-left: 6px;
  color: #937b63;
}
.route-planner-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.route-planner-actions .btn {
  min-height: 44px;
  flex: 1;
  font-size: 12px;
  padding: 8px 12px;
}
.map-pick-banner {
  position: absolute;
  left: 16px;
  top: 16px;
  right: 70px;
  z-index: 4;
  margin: 0;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(53, 38, 26, 0.9);
  color: #fffaf2;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.map-pick-banner button {
  margin-left: auto;
  min-height: 34px;
  padding: 4px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font-size: 12px;
}
.map-footnote-details {
  margin-top: 14px;
  border: 1px solid #eadfce;
  border-radius: 12px;
  background: #fbf7f0;
  padding: 0 14px;
}
.map-footnote-details summary {
  list-style: none;
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 44px;
  cursor: pointer;
  color: #877560;
  font-size: 12px;
  font-weight: 500;
}
.map-footnote-details summary::-webkit-details-marker {
  display: none;
}
.map-footnote-details summary span {
  flex: 1;
}
.map-footnote-details[open] summary svg:last-child {
  transform: rotate(180deg);
}
.map-footnote {
  margin: 0;
  padding: 0 0 12px;
  color: #8c7b66;
  font-size: 12px;
  line-height: 1.65;
}
@media (max-width: 1023px) {
  .map-layout {
    grid-template-columns: 330px 1fr;
  }
  .selected-stall {
    left: 15px;
    right: 65px;
    grid-template-columns: 1fr;
  }
  .selected-stall > img,
  .selected-stall-placeholder {
    display: none;
  }
}
@media (max-width: 767px) {
  .map-page .page-heading {
    display: none;
  }
  .locate-top {
    display: none;
  }
  .map-layout {
    display: flex;
    flex-direction: column-reverse;
    height: auto;
    border: 0;
    border-radius: 0;
    background: none;
    gap: 10px;
    overflow: visible;
  }
  .map-canvas {
    height: clamp(216px, 34dvh, 260px);
    min-height: 216px;
    border-radius: 15px;
    overflow: hidden;
    border: 1px solid #e7dcc9;
    box-shadow: 0 8px 20px rgba(60, 37, 20, 0.06);
  }
  .map-canvas.map-fallback {
    height: auto;
    min-height: 0;
    box-shadow: none;
  }
  .map-canvas.has-active-route {
    display: flex;
    flex-direction: column;
    gap: 10px;
    height: auto;
    min-height: 0;
    border: 0;
    border-radius: 0;
    background: none;
    box-shadow: none;
    overflow: visible;
  }
  .map-canvas.has-active-route .map-container {
    position: relative;
    height: 210px;
    border-radius: 15px;
    overflow: hidden;
    border: 1px solid #e7dcc9;
    box-shadow: 0 8px 20px rgba(60, 37, 20, 0.06);
  }
  .map-canvas.has-active-route .route-planner-card {
    position: relative;
    left: auto;
    top: auto;
    width: 100%;
    max-height: none;
    margin: 0;
  }
  .route-origin-chip {
    min-height: 44px;
    padding: 8px 13px;
  }
  .map-sidebar {
    border: 0;
    border-radius: 17px;
    background: none;
  }
  .map-list-controls {
    padding: 0 0 8px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 10px;
  }
  .map-list-controls .search-box,
  .map-list-controls :deep(.discovery-filters) {
    width: 100%;
    margin: 0;
  }
  .map-list-controls .filter-chips {
    margin: 0;
    order: 4;
    margin-left: auto;
  }
  .map-list-controls .map-result-count {
    margin: 0;
    order: 3;
    gap: 4px;
  }
  .map-results {
    padding: 0;
    overflow: visible;
  }
  .map-list-item {
    background: #fff;
    border: 1px solid var(--line);
  }
  .map-list-controls .search-box {
    background: #fff;
  }
  .map-unavailable {
    padding: 16px;
    flex-wrap: wrap;
  }
  .map-unavailable > div {
    flex: 1;
    min-width: 180px;
  }
  .map-fallback {
    min-height: 0;
  }
  .map-fallback .map-results {
    max-height: none;
  }
  .map-fallback .map-sidebar {
    padding: 0;
  }
  .map-fallback .selected-stall {
    width: calc(100% - 20px);
    margin: 0 10px 10px;
  }
  .selected-stall {
    left: 10px;
    right: 10px;
    bottom: 10px;
    padding: 12px 13px;
    grid-template-columns: 56px 1fr;
    gap: 10px;
    border-radius: 16px;
    background: rgba(255, 253, 249, 0.97);
    backdrop-filter: blur(8px);
    box-shadow: 0 12px 28px rgba(48, 30, 16, 0.16);
  }
  .selected-stall > img,
  .selected-stall-placeholder {
    display: block;
    width: 57px;
    height: 65px;
  }
  .selected-stall-placeholder {
    display: grid;
  }
  .selected-info h3 {
    font-size: 14px;
  }
  .selected-info .badge {
    font-size: 9px;
    padding: 2px 5px;
  }
  .selected-info small {
    display: none;
  }
  .selected-actions .btn {
    min-height: 44px;
  }
  .map-footnote {
    font-size: 10px;
    align-items: flex-start;
    line-height: 1.8;
  }
  .map-footnote svg {
    margin-top: 4px;
  }
  .map-location {
    bottom: auto;
    top: 12px;
    right: 12px;
    width: 44px;
    height: 44px;
  }
}
</style>
<style>
.food-map-pin {
  border: 3px solid #fdfaf4;
  width: 48px;
  height: 48px;
  background: #f3e2c3;
  border-radius: 50% 50% 50% 5px;
  transform: rotate(-45deg);
  padding: 0;
  box-shadow: 0 3px 8px #3c251433;
  overflow: hidden;
}
.food-map-pin img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transform: rotate(45deg) scale(1.25);
}
.food-map-pin-initial {
  width: 100%;
  height: 100%;
  display: grid;
  place-items: center;
  transform: rotate(45deg);
  font-size: 15px;
  font-weight: 700;
  color: #8f481f;
}
.food-map-pin.selected {
  border-color: #e76b28;
  width: 57px;
  height: 57px;
}
.food-map-pin.closed,
.food-map-pin.stale,
.food-map-pin.paused {
  filter: grayscale(0.8);
  opacity: 0.75;
}
.user-position-dot {
  display: block;
  width: 16px;
  height: 16px;
  background: #4296d9;
  border: 3px solid #fff;
  border-radius: 50%;
  box-shadow: 0 0 0 7px #4296d926;
}
.route-endpoint-pin {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 800;
  color: #fff;
  border: 2.5px solid #fffaf2;
  box-shadow: 0 4px 12px rgba(48, 28, 12, 0.28);
}
.route-start-pin {
  background: #457b59;
}
.route-end-pin {
  background: #d95f18;
}
</style>
