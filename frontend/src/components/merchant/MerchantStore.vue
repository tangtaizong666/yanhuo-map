<script setup lang="ts">
import { ownedImage } from "../../lib/media";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import {
  Store,
  MapPin,
  Navigation,
  ShieldCheck,
  Save,
  Check,
  Camera,
  Settings2,
  ExternalLink,
  CircleCheck,
  CirclePause,
  Wallet,
} from "lucide-vue-next";
import { api } from "../../lib/api";
import { loadAMap, locate } from "../../lib/amap";
import { useSession } from "../../stores/session";
import { notify } from "../../lib/notify";
import MerchantPayoutCard from "./MerchantPayoutCard.vue";
import MerchantServices from "./MerchantServices.vue";
import MerchantOperations from "./MerchantOperations.vue";
import { hasPickupEligibility } from "./mode";
const props = withDefaults(
    defineProps<{ stall: any; orders?: any[]; ordersReady?: boolean; discoveryOnly?: boolean }>(),
    { orders: () => [], ordersReady: false },
  ),
  emit = defineEmits<{ refresh: []; stallUpdated: [stall: any] }>(),
  session = useSession();
const busy = ref(""),
  error = ref(""),
  relocationNeedsReview = ref(false);
const profile = reactive({
  name: "",
  description: "",
  image: "",
  prep_minutes: 10,
  contact_phone: "",
  public_phone_enabled: false,
  arrival_note: "",
  arrival_image: "",
  payment_qr_image: "",
  location_draft_address: "",
  usual_hours: "",
});
const location = reactive({
  address: "",
  latitude: "",
  longitude: "",
  closes_at: "",
});
const mapAvailable = computed(() => !!session.config?.amap_key);
const hasLocationCoordinates = computed(() => {
  const latitude = Number(location.latitude), longitude = Number(location.longitude);
  return !!location.latitude.trim() && !!location.longitude.trim() &&
    Number.isFinite(latitude) && Number.isFinite(longitude) &&
    latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180;
});
const profileBase: Record<string, any> = {};
const locationBase: Record<string, string> = {};
const route = useRoute();
const locationOpen = ref(route.hash === "#location" || !props.stall.activation?.has_location),
  mapOpen = ref(false),
  mapElement = ref<HTMLElement>();
let map: any = null,
  marker: any = null,
  disposed = false;
onBeforeUnmount(() => {
  disposed = true;
  map?.destroy();
});
watch(
  () => route.hash,
  (hash) => {
    if (hash === "#location") showLocation();
  },
);
watch(
  () => hasPickupEligibility(props.stall),
  (eligible) => { if (eligible) relocationNeedsReview.value = false; },
);
function showLocation() {
  locationOpen.value = true;
  void nextTick(() =>
    document
      .getElementById("location")
      ?.scrollIntoView({ behavior: "smooth", block: "start" }),
  );
}
onMounted(() => {
  if (route.hash === "#location") showLocation();
});
const confirmationStatus = computed(() =>
  props.stall.status === "closed"
    ? "closed"
    : props.stall.session_status ||
      (props.stall.status === "stale" ? "paused" : props.stall.status),
);
function localDate(v: string) {
  if (!v) return "";
  const d = new Date(v);
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fill(current: any, previous?: any) {
  const incomingProfile = {
    name: props.stall.name,
    description: props.stall.description,
    image: props.stall.image,
    prep_minutes: props.stall.prep_minutes,
    contact_phone: props.stall.contact_phone,
    public_phone_enabled: !!props.stall.public_phone_enabled,
    arrival_note: props.stall.arrival_note || "",
    arrival_image: props.stall.arrival_image || "",
    payment_qr_image: props.stall.payment_qr_image || "",
    location_draft_address: props.stall.location_draft_address || "",
    usual_hours: props.stall.usual_hours || "",
  };
  const incomingLocation = {
    address: props.stall.activation?.has_location ? props.stall.address : props.stall.location_draft_address || "",
    latitude: String(props.stall.latitude ?? ""),
    longitude: String(props.stall.longitude ?? ""),
    closes_at: localDate(props.stall.closes_at),
  };
  for (const key of Object.keys(incomingProfile) as (keyof typeof profile)[]) {
    if (
      !previous ||
      current.id !== previous.id ||
      profile[key] === profileBase[key]
    )
      (profile as any)[key] = incomingProfile[key];
    profileBase[key] = incomingProfile[key];
  }
  for (const key of Object.keys(
    incomingLocation,
  ) as (keyof typeof location)[]) {
    if (
      !previous ||
      current.id !== previous.id ||
      location[key] === locationBase[key]
    )
      location[key] = incomingLocation[key];
    locationBase[key] = incomingLocation[key];
  }
}
watch(() => props.stall, fill, { immediate: true });
async function saveProfile() {
  if (busy.value) return;
  busy.value = "profile";
  error.value = "";
  try {
    const values = {
      ...profile,
      name: profile.name.trim(),
      description: profile.description.trim(),
    };
    const changes = Object.fromEntries(
      Object.entries(values).filter(
        ([key, value]) => value !== profileBase[key],
      ),
    );
    if (!Object.keys(changes).length) {
      notify("没有需要保存的修改", "info");
      return;
    }
    await api(`/merchant/stalls/${props.stall.id}/profile`, {
      method: "PATCH",
      body: changes,
    });
    Object.assign(profileBase, changes);
    Object.assign(profile, changes);
    emit("refresh");
    notify("店铺信息已保存，学生端同步更新", "success");
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = "";
  }
}
async function updateStatus(
  status: string,
  saveLocation = false,
  confirm = false,
) {
  if (busy.value) return;
  busy.value = "status";
  error.value = "";
  const hadTradeEligibility = hasPickupEligibility(props.stall);
  try {
    const body: any = {
      status,
      confirm_location: confirm || status === "open" || saveLocation,
    };
    if (saveLocation) {
      const lat = Number(location.latitude),
        lng = Number(location.longitude);
      if (
        !location.address.trim() ||
        !location.latitude.trim() ||
        !location.longitude.trim() ||
        !Number.isFinite(lat) ||
        !Number.isFinite(lng) ||
        lat < -90 ||
        lat > 90 ||
        lng < -180 ||
        lng > 180
      )
        throw new Error("请填写有效的取餐地址和经纬度。");
      const d = location.closes_at ? new Date(location.closes_at) : null;
      if (d && Number.isNaN(d.getTime())) throw new Error("收摊时间无效。");
      const firstLocation = !props.stall.activation?.has_location;
      // An application address draft is not an existing confirmed location.
      if (firstLocation || location.address.trim() !== locationBase.address)
        body.address = location.address.trim();
      if (
        firstLocation ||
        lat !== Number(locationBase.latitude) ||
        lng !== Number(locationBase.longitude)
      )
        Object.assign(body, { latitude: lat, longitude: lng });
      if (location.closes_at !== locationBase.closes_at)
        body.closes_at = d?.toISOString() ?? null;
    }
    const updated = await api<any>(`/merchant/stalls/${props.stall.id}/status`, {
      method: "POST",
      body,
    });
    if (disposed) return;
    // Relocation can switch this stall into information-only mode. Keep the
    // confirmed action outcome visible beside the editor across that switch.
    if (
      saveLocation &&
      ["address", "latitude", "longitude"].some((field) => field in body) &&
      hadTradeEligibility &&
      !hasPickupEligibility(updated)
    ) relocationNeedsReview.value = true;
    if (saveLocation) Object.assign(locationBase, location);
    emit("refresh");
    notify(
      saveLocation
        ? "取餐位置和收摊时间已保存"
        : `已${status === "open" ? "确认出摊" : status === "paused" ? "暂停接单" : "收摊"}，已有订单仍可处理`,
      "success",
    );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = "";
  }
}
function applyAreaPreset(item: {
  name: string;
  latitude: number;
  longitude: number;
}) {
  location.latitude = item.latitude.toFixed(6);
  location.longitude = item.longitude.toFixed(6);
  if (!location.address.trim()) {
    location.address = `${item.name}便民摊位点`;
  }
  notify(`已填入「${item.name}」坐标，请补充具体摊位位置描述后保存`, "info");
}
async function position() {
  if (busy.value || !session.config) return;
  if (
    typeof window !== "undefined" &&
    window.isSecureContext === false &&
    session.config.amap_key
  ) {
    error.value =
      "局域网 HTTP 访问下浏览器限制自动定位；已为你展开地图选点，也可点选下方校区常用坐标。";
    await pickLocation();
    return;
  }
  busy.value = "location";
  error.value = "";
  try {
    const pos = await locate(session.config);
    location.latitude = String(pos.lat);
    location.longitude = String(pos.lng);
    if (disposed) return;
    notify("已获取地图位置，请核对详细地址后保存", "info");
  } catch (e) {
    if (!disposed)
      error.value =
        "暂时未能定位。可在地图上选点，或先保存地址草稿，请团队协助核实位置。";
  } finally {
    busy.value = "";
  }
}
async function pickLocation() {
  if (busy.value || !session.config) return;
  busy.value = "map";
  error.value = "";
  try {
    if (!session.config.amap_key)
      throw new Error(
        "地图暂未配置。先保存地址草稿，团队核实后再确认出摊；草稿不会公开为已确认位置。",
      );
    const AMap = await loadAMap(session.config);
    if (disposed) return;
    mapOpen.value = true;
    await nextTick();
    if (disposed) return;
    map?.destroy();
    const area = session.config.areas.find((a) => a.id === props.stall.area_id);
    const lat = location.latitude
      ? Number(location.latitude)
      : (area?.latitude ?? 45.711192);
    const lng = location.longitude
      ? Number(location.longitude)
      : (area?.longitude ?? 126.634053);
    map = new AMap.Map(mapElement.value, {
      zoom: 17,
      center: [lng, lat],
    });
    marker = null;
    map.on("click", (event: any) => {
      location.latitude = String(event.lnglat.getLat());
      location.longitude = String(event.lnglat.getLng());
      if (!marker) {
        marker = new AMap.Marker({ position: event.lnglat });
        map.add(marker);
      } else marker.setPosition(event.lnglat);
    });
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    if (!disposed) busy.value = "";
  }
}
async function saveAddressDraft() {
  if (busy.value) return;
  if (!location.address.trim()) {
    error.value = "请先填写地址说明。";
    return;
  }
  busy.value = "draft";
  error.value = "";
  try {
    await api(`/merchant/stalls/${props.stall.id}/profile`, {
      method: "PATCH",
      body: { location_draft_address: location.address.trim() },
    });
    if (disposed) return;
    profile.location_draft_address = location.address.trim();
    profileBase.location_draft_address = location.address.trim();
    emit("refresh");
    notify("地址草稿已保存，尚未确认出摊位置", "success");
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    if (!disposed) busy.value = "";
  }
}
async function upload(
  e: Event,
  field: "image" | "arrival_image" | "payment_qr_image" = "image",
) {
  const input = e.target as HTMLInputElement,
    file = input.files?.[0];
  if (!file || busy.value) {
    input.value = "";
    return;
  }
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    input.value = "";
    error.value = "请上传 JPG、PNG 或 WebP 格式的图片";
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    input.value = "";
    error.value = "图片不能超过 5MB";
    return;
  }
  busy.value = "photo";
  error.value = "";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const query = field === "payment_qr_image" ? "?purpose=payment_qr" : "";
    const res = await api<{ url: string }>(
      `/merchant/stalls/${props.stall.id}/image${query}`,
      { method: "POST", body: fd },
    );
    if (disposed) return;
    profile[field] = res.url;
    notify(
      field === "image"
        ? "封面已上传，保存店铺信息后生效"
        : field === "payment_qr_image"
          ? "收款码已上传，保存后仅在符合现场付款条件的订单中展示"
          : "找摊照片已上传，保存店铺信息后生效，不会替换封面",
      "info",
    );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    input.value = "";
    busy.value = "";
  }
}
</script>
<template>
  <div class="m-store-settings">
    <p v-if="error" role="alert" class="m-alert">{{ error }}</p>

    <div class="m-page-brief">
      <p>
        <strong>设置指引：</strong>日常摆摊只需在第一组<strong>确认出摊位置</strong>；{{
          discoveryOnly
            ? "店铺封面、找摊照片与资质已在下方分类整理，需要改哪项点开哪项即可。"
            : "收款账户、自动接单与店铺门面已按用途分好类，需要改哪项点开哪项即可。"
        }}
      </p>
      <details class="m-detail-toggle">
        <summary>不确定去哪一项修改？点此查看速查说明</summary>
        <ul>
          <li><strong>今天在哪摆摊、几点收摊：</strong>在下方第一组「出摊状态与位置」直接确认或重选地图点位。</li>
          <li v-if="!discoveryOnly">
            <strong>钱打到哪个微信号/银行卡、开关自动接单与外卖配送：</strong>点开第二组「收款、支付与配送」。
          </li>
          <li><strong>换摊位封面照片、写简介、留电话、传路口认摊照：</strong>点开最后一组「店铺资料」。</li>
        </ul>
      </details>
    </div>

    <!-- 分类一：今日出摊与位置设置 -->
    <section class="m-cat-group" aria-label="今日出摊与位置设置">
      <div class="m-cat-head">
        <h2>出摊状态与位置</h2>
        <p>每次出摊确认一次位置；换地方摆摊时点开下方「位置与时间」重新定位</p>
      </div>

      <MerchantOperations
        :key="stall.id"
        :stall="stall"
        :orders="orders"
        :ready="ordersReady"
        :discovery-only="discoveryOnly"
        @refresh="emit('refresh')"
        @location="showLocation"
      />
      <details
        id="location"
        class="m-panel store-details"
        :open="locationOpen"
        @toggle="locationOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary>
          <MapPin :size="20" /><span
            >位置与时间<small>定位或点选摆摊地点、填写详细地址与预计收摊时间</small></span
          >
        </summary>
        <section class="store-inner-section">
          <div class="m-panel-head">
            <h2><MapPin :size="20" /> 取餐位置与时间</h2>
            <button
              v-if="mapAvailable"
              class="btn btn-secondary"
              :disabled="!!busy"
              @click="position"
            >
              <Navigation :size="16" /> 获取当前位置
            </button>
          </div>
          <p class="m-muted" style="margin: -4px 0 12px">
            先点「获取当前位置」或常用校区点位，再写一句好认的摊位描述（如“正门入口左侧第 3 个摊位”），最后点保存即可。
          </p>
          <p v-if="!mapAvailable && !hasLocationCoordinates" class="m-info-banner">
            暂时不能使用地图。先保存地址草稿，请团队协助确认位置；草稿不会公开为出摊位置，也不会开始营业。
          </p>
          <button
            v-if="mapAvailable"
            class="btn btn-secondary"
            :disabled="!!busy"
            @click="pickLocation"
          >
            在地图上选择位置
          </button>
          <div
            v-if="session.config?.areas?.length"
            class="campus-preset-row"
            role="group"
            aria-label="快速填入校区常用坐标"
          >
            <span>常用校区点位：</span>
            <button
              v-for="item in session.config.areas"
              :key="item.id"
              type="button"
              class="campus-preset-chip"
              :disabled="!!busy"
              @click="applyAreaPreset(item)"
            >
              {{ item.name.replace(/^哈尔滨理工大学南区[·\s]*/, "") }}
            </button>
          </div>
          <div v-if="mapOpen" class="map-picker">
            <div
              ref="mapElement"
              class="map-canvas"
              aria-label="点击地图设置取餐位置"
            />
            <p>
              点选你的实际摊位位置，再填写同学看得懂的地址。{{
                location.latitude && location.longitude
                  ? "已选点，尚未保存。"
                  : "尚未选点。"
              }}
            </p>
          </div>
          <form
            class="m-form"
            @submit.prevent="hasLocationCoordinates ? updateStatus(confirmationStatus, true) : saveAddressDraft()"
          >
            <label
              >详细取餐地址<input
                v-model="location.address"
                required
                maxlength="200"
                placeholder="例如：哈理工南区正门夜市入口左侧第三个摊位"
            /></label>
            <label v-show="hasLocationCoordinates"
              >预计收摊时间<input
                v-model="location.closes_at"
                type="datetime-local"
            /></label>
            <details class="coordinate-details">
              <summary>手动填写地图坐标</summary>
              <div class="m-field-row">
                <label
                  >纬度（高德坐标）<input
                    v-model="location.latitude"
                    inputmode="decimal" /></label
                ><label
                  >经度（高德坐标）<input
                    v-model="location.longitude"
                    inputmode="decimal"
                /></label>
              </div>
            </details>
            <details v-if="hasLocationCoordinates" class="m-detail-toggle">
              <summary>更换摆摊地点后要注意什么？（点此查看说明）</summary>
              <div>
                更换取餐地址或地图坐标后，系统会先暂停接新单，待校园运营快速核验新点位后恢复；改位置前已接的老订单仍保留原取餐地址，请主动联系同学交接。
              </div>
            </details>
            <div v-if="relocationNeedsReview" class="m-info-banner" role="status">
              <strong>取餐位置已变更，等待运营重新核验</strong>
              <p>新位置已保存，线上新单暂停。已有订单保留原取餐地址，请联系顾客确认交付安排。</p>
              <RouterLink to="/merchant/orders?filter=active" class="m-text-link">处理已有订单</RouterLink>
            </div>
            <button v-if="hasLocationCoordinates" class="btn btn-primary" :disabled="!!busy" type="submit">
              <Save :size="16" /> 确认并保存位置与时间
            </button>
            <button
              :class="['btn', hasLocationCoordinates ? 'btn-secondary' : 'btn-primary']"
              :disabled="!!busy"
              :type="hasLocationCoordinates ? 'button' : 'submit'"
              @click="hasLocationCoordinates && saveAddressDraft()"
            >
              {{ hasLocationCoordinates ? '仅保存地址草稿' : '保存地址草稿' }}
            </button>
            <p v-if="!hasLocationCoordinates && mapAvailable" class="m-muted">先保存地址也可以。确认出摊位置前，请定位或在地图上选点。</p>
          </form>
        </section>
      </details>
    </section>

    <!-- 分类二：收款账户、支付与配送 -->
    <section v-if="!discoveryOnly" class="m-cat-group" aria-label="收款、支付与配送">
      <div class="m-cat-head">
        <h2>收款、支付与配送</h2>
        <p>登记打款微信/银行卡、查看自动锁价说明，或开关自动接单与外卖配送</p>
      </div>

      <details class="m-panel store-details store-payout">
        <summary>
          <Wallet :size="20" />
          <span>
            收款账户与锁价说明
            <small>{{
              stall.payout_account?.registered
                ? `已登记收款人：${stall.payout_account.holder_name} · 点开查看或修改`
                : "登记钱打到哪个微信/银行卡 · 了解为什么不用传个人收款码"
            }}</small>
          </span>
        </summary>
        <div class="store-inner-section">
          <MerchantPayoutCard
            :stall="stall"
            embedded
            @saved="emit('stallUpdated', $event)"
          />
        </div>
      </details>

      <details class="m-panel store-details store-services">
        <summary>
          <Settings2 :size="20" />
          <span>支付与配送<small>付款后自动接单、线上支付开关、配送费与交接点</small></span>
        </summary>
        <MerchantServices :stall="stall" @refresh="emit('refresh')" />
      </details>
    </section>

    <!-- 分类三：店铺门面、资质与账号安全 -->
    <section class="m-cat-group" aria-label="店铺门面、资质与账号">
      <div class="m-cat-head">
        <h2>店铺门面、资质与账号</h2>
        <p>修改顾客看到的店招封面与认摊照片，查看经营资质与密码恢复码</p>
      </div>

      <details class="m-panel store-details">
        <summary>
          <Store :size="20" /><span
            >店铺资料<small>名称、封面、备餐时间、电话与路口找摊照片</small></span
          >
        </summary>
        <section class="store-inner-section">
          <div class="m-panel-head">
            <h2><Store :size="20" /> 店铺信息</h2>
            <RouterLink v-if="stall.is_visible" :to="`/stalls/${stall.id}`" class="m-text-link" @click="session.setConsumerPreview(true)"
              >预览 <ExternalLink :size="15"
            /></RouterLink>
            <small v-else class="muted">公开展示核验后可预览</small>
          </div>
          <p class="m-muted" style="margin: -4px 0 14px">
            这里填写的内容会展示在学生端摊位主页，帮助同学一眼认出你的餐车并预估等餐时间。
          </p>
          <form class="m-form" @submit.prevent="saveProfile">
            <p v-if="(profile.image && !ownedImage(profile.image)) || (profile.arrival_image && !ownedImage(profile.arrival_image))" class="m-alert">旧第三方图片地址仍保留，已停止对外加载。请重新上传封面或找摊照片后保存；已有平台上传照片不受影响。</p>
            <div class="m-shop-photo">
              <img v-if="ownedImage(profile.image)" :src="ownedImage(profile.image)" alt="店铺封面" />
              <div>
                <label class="btn btn-secondary m-upload"
                  ><Camera :size="16" /> 更换店铺封面<input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    :disabled="!!busy"
                    @change="upload"
                    aria-label="上传店铺封面" /></label
                ><small>JPG / PNG / WebP，最大 5MB</small>
              </div>
            </div>
            <label
              >摊位名称<input
                v-model="profile.name"
                required
                maxlength="80"
                placeholder="给小摊一个好记的名字" /></label
            ><label
              >店铺简介<textarea
                v-model="profile.description"
                rows="3"
                maxlength="1000"
                placeholder="介绍你的拿手好味道…"
              />
            </label>
            <div class="m-field-row">
              <label
                >预计备餐时间（分钟）<input
                  type="number"
                  v-model.number="profile.prep_minutes"
                  min="1"
                  max="180"
                  required /></label
              ><label
                >公开联系电话<input
                  v-model="profile.contact_phone"
                  type="tel"
                  maxlength="30"
                  placeholder="按需填写"
              /></label>
            </div>
            <label class="phone-consent"><input type="checkbox" v-model="profile.public_phone_enabled" /> 在公开摊位页展示联系电话</label>
            <label
              >通常出摊时段（选填）<input
                v-model="profile.usual_hours"
                maxlength="100"
                placeholder="例如：通常周一至周五 17:00–21:00"
              /></label
            >
            <label
              >怎样更容易找到你<textarea
                v-model="profile.arrival_note"
                rows="2"
                maxlength="200"
                placeholder="例如：南门入口左手边，橙色棚子下面"
              />
            </label>
            <div class="m-shop-photo">
              <img
                v-if="ownedImage(profile.arrival_image)"
                :src="ownedImage(profile.arrival_image)"
                alt="找摊参照照片"
              />
              <div>
                <label class="btn btn-secondary m-upload"
                  ><Camera :size="16" /> 上传找摊参照照片<input
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    :disabled="!!busy"
                    @change="upload($event, 'arrival_image')"
                    aria-label="上传找摊参照照片" /></label
                ><small>拍一张带路口或灯箱的现场照，方便同学找摊。</small
                ><button
                  v-if="profile.arrival_image"
                  type="button"
                  class="m-text-link"
                  @click="profile.arrival_image = ''"
                >
                  移除找摊照片
                </button>
              </div>
            </div>
            <details class="m-detail-toggle">
              <summary>查看电话隐私与计划时段详细说明</summary>
              <ul>
                <li>关闭“在公开摊位页展示联系电话”后，未下单路人看不到号码，但已下单顾客仍可通过订单详情联系你。</li>
                <li>“通常出摊时段”仅作为计划预告展示，不会自动帮你开摊，实际出摊仍需点「开始营业」。</li>
              </ul>
            </details>
            <button class="btn btn-primary" type="submit" :disabled="!!busy">
              <Save :size="16" /> 保存店铺信息
            </button>
          </form>
        </section>
      </details>

      <details class="m-panel store-details">
        <summary><ShieldCheck :size="20" /><span>经营信息<small>经营主体与资质核验公示状态</small></span></summary>
        <section class="store-inner-section">
          <div class="m-panel-head">
            <h2><ShieldCheck :size="20" /> 经营信息</h2>
            <span
              class="m-status"
              :class="!discoveryOnly ? 'open' : 'paused'"
              >{{
                !discoveryOnly
                  ? "已开放在线接单"
                  : "仅展示，未开放在线接单"
              }}</span
            >
          </div>
          <div class="m-qualification">
            <div>
              <small>经营主体</small><strong>{{ stall.merchant_name }}</strong>
            </div>
            <div>
              <small>资质公示</small>
              <p>
                {{
                  stall.qualification_note ||
                  "尚未公示经营资质，由运营核验后维护。"
                }}
              </p>
            </div>
          </div>
          <details class="m-detail-toggle">
            <summary>查看交易与展示资格核验说明</summary>
            <div>
              {{
                discoveryOnly
                  ? "当前提供找摊信息服务，可发布位置与菜品。自取下单、线上支付和配送需分别由运营核验开通；使用个人收款码不等于获得线上交易资格。"
                  : "自取下单、线上支付和配送按当前核验资格开放。若资格变更仅限制新订单，已接老订单仍可正常履约完成。"
              }}
            </div>
          </details>
        </section>
      </details>

      <details class="m-panel store-details">
        <summary>
          <ShieldCheck :size="20" /><span>更多设置<small>账号密码安全与找回恢复码</small></span>
        </summary>
        <section class="store-inner-section">
          <p class="m-muted">
            保存一次性恢复码，忘记密码时可自行重设。恢复码请妥善私下保管，不要贴在餐车二维码旁。
          </p>
          <RouterLink
            to="/account-security?returnTo=/merchant/store"
            class="btn btn-secondary"
            >管理账号恢复码</RouterLink
          >
        </section>
      </details>
    </section>
  </div>
</template>

<style scoped>
.m-store-settings {
  max-width: 920px;
}
.daily-settings {
  grid-template-columns: 1fr;
}
.location-confirmation .m-business-actions {
  display: none;
}
.location-confirmation .m-panel-head {
  display: none;
}
.map-picker {
  margin-top: 16px;
}
.campus-preset-row {
  margin-top: 12px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: #7c654d;
}
.campus-preset-chip {
  min-height: 44px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid #e4d4be;
  background: #fdf9f2;
  color: #6b5239;
  font-size: 12px;
  font-weight: 500;
}
.campus-preset-chip:hover:not(:disabled) {
  border-color: #c98b58;
  background: #f8eedf;
}
.map-canvas {
  height: 280px;
  border-radius: 16px;
  overflow: hidden;
  background: #f6edde;
}
.map-picker p {
  font-size: 13px;
  line-height: 1.7;
  color: #755e47;
}
#location {
  scroll-margin-top: 76px;
}
.m-shop-photo small {
  max-width: 100%;
  line-height: 1.7;
}
.daily-settings .m-business-intro {
  display: none;
}
.daily-settings .m-panel-head {
  margin-bottom: 18px;
}
.store-details {
  padding-top: 0;
  padding-bottom: 0;
}
.store-services :deep(.merchant-services) { border: 0; box-shadow: none; border-radius: 0; padding: 16px 0; margin: 0; border-top: 1px solid #eee2d1; }
.store-details > summary {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 80px;
  cursor: pointer;
  list-style: none;
  color: #5d4530;
  font-size: 15px;
  font-weight: 600;
}
.store-details > summary > span {
  flex: 1;
}
.store-details > summary small {
  display: block;
  margin-top: 6px;
  color: #806b56;
  font-size: 12px;
  font-weight: 400;
  line-height: 1.7;
}
.store-details > summary::after {
  content: "";
  width: 8px;
  height: 8px;
  flex: none;
  border-right: 2px solid currentColor;
  border-bottom: 2px solid currentColor;
  transform: rotate(45deg) translateY(-2px);
  opacity: 0.55;
  transition: transform 0.15s;
  color: #9f8267;
}
.store-details[open] > summary::after {
  transform: rotate(-135deg) translateY(-2px);
}
.store-inner-section {
  padding: 18px 0 24px;
  border-top: 1px solid #eee2d1;
}
.coordinate-details {
  padding: 0 14px;
  border-radius: 10px;
  background: #f8f1e7;
  border: 1px solid #ebdfd0;
}
.coordinate-details summary {
  cursor: pointer;
  min-height: 48px;
  padding: 15px 0;
  font-size: 13px;
  color: #72583f;
}
.coordinate-details .m-field-row {
  padding-bottom: 16px;
}
summary:focus-visible {
  outline: 3px solid #c98b58;
  outline-offset: 3px;
}
</style>
