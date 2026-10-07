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
  Map as MapIcon,
} from "lucide-vue-next";
import { useStalls } from "../lib/discovery";
import { useSession } from "../stores/session";
import { loadAMap, locate } from "../lib/amap";
import { api, confirmedText, statusText, routeUrl } from "../lib/api";
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
watch(
  followOnly,
  (only) => {
    if (only) filters.status = "";
  },
  { immediate: true },
);
let map: any = null,
  markers: any[] = [],
  unmounted = false,
  userMarker: any = null;
const area = computed(() =>
  session.config?.areas.find((a) => a.id === filters.area),
);
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
      zoom: restoredMap?.zoom || 15,
      center: restoredMap?.center || (area.value
        ? [area.value.longitude, area.value.latitude]
        : [126.632, 45.752]),
      viewMode: "2D",
      mapStyle: "amap://styles/whitesmoke",
    });
    map.addControl(new AMap.Scale());
    map.on("moveend", syncBounds);
    syncBounds();
    renderMarkers();
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
    const image = document.createElement("img");
    image.src = s.image;
    image.alt = "";
    content.appendChild(image);
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
  if (a && map) map.setZoomAndCenter(15, [a.longitude, a.latitude]);
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
  map?.destroy();
});
</script>
<template>
  <div class="page map-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">FOLLOW THE GOOD SMELL</p>
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
      <section class="map-canvas" aria-label="摊位地图">
        <div ref="mapNode" class="map-container"></div>
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
        <div v-if="!mapError" class="map-legend">
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
          <img :src="selected.image" :alt="selected.name" />
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
    <p class="map-footnote">
      <Clock3
        :size="12"
      />营业位置由商家确认更新，出发前请留意最新状态。距离为直线距离，实际路程请以导航为准。
    </p>
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
  font-size: 11px;
  margin-left: auto;
  min-height: 44px;
  max-width: 88px;
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
.selected-stall > img {
  width: 70px;
  height: 80px;
  border-radius: 10px;
  object-fit: cover;
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
.map-footnote {
  margin-top: 15px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: #a19581;
  font-size: 11px;
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
  .selected-stall > img {
    display: none;
  }
}
@media (max-width: 767px) {
  .map-page .page-heading {
    margin-bottom: 18px;
  }
  .map-page .page-heading h1 {
    font-size: 22px;
  }
  .map-page .eyebrow {
    font-size: 8px;
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
    gap: 18px;
    overflow: visible;
  }
  .map-canvas {
    height: 330px;
    min-height: 330px;
    border-radius: 17px;
    overflow: hidden;
    border: 1px solid #e9e1d1;
  }
  .map-sidebar {
    border: 0;
    border-radius: 17px;
    background: none;
  }
  .map-list-controls {
    padding: 0 0 12px;
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
    left: 12px;
    right: 12px;
    bottom: 12px;
    padding: 12px;
    grid-template-columns: 57px 1fr;
    gap: 9px;
  }
  .selected-stall > img {
    display: block;
    width: 57px;
    height: 65px;
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
</style>
