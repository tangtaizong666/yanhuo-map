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
} from "lucide-vue-next";
import { api } from "../../lib/api";
import { loadAMap, locate } from "../../lib/amap";
import { useSession } from "../../stores/session";
import { notify } from "../../lib/notify";
import MerchantServices from "./MerchantServices.vue";
import MerchantOperations from "./MerchantOperations.vue";
const props = withDefaults(
    defineProps<{ stall: any; orders?: any[]; ordersReady?: boolean }>(),
    { orders: () => [], ordersReady: false },
  ),
  emit = defineEmits<{ refresh: [] }>(),
  session = useSession();
const busy = ref(""),
  error = ref("");
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
      if (location.address.trim() !== locationBase.address)
        body.address = location.address.trim();
      if (
        lat !== Number(locationBase.latitude) ||
        lng !== Number(locationBase.longitude)
      )
        Object.assign(body, { latitude: lat, longitude: lng });
      if (location.closes_at !== locationBase.closes_at)
        body.closes_at = d?.toISOString() ?? null;
    }
    await api(`/merchant/stalls/${props.stall.id}/status`, {
      method: "POST",
      body,
    });
    if (disposed) return;
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
async function position() {
  if (busy.value || !session.config) return;
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
    const lat = location.latitude ? Number(location.latitude) : area?.latitude;
    const lng = location.longitude
      ? Number(location.longitude)
      : area?.longitude;
    map = new AMap.Map(mapElement.value, {
      zoom: 17,
      ...(lat != null && lng != null ? { center: [lng, lat] } : {}),
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
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    error.value = "图片不能超过 5MB";
    return;
  }
  busy.value = "photo";
  error.value = "";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await api<{ url: string }>(
      `/merchant/stalls/${props.stall.id}/image`,
      { method: "POST", body: fd },
    );
    if (disposed) return;
    profile[field] = res.url;
    notify(
      field === "image"
        ? "封面已上传，保存店铺信息后生效"
        : field === "payment_qr_image"
          ? "收款码已上传，保存店铺信息后顾客下单即可看到"
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
    <MerchantOperations
      :key="stall.id"
      :stall="stall"
      :orders="orders"
      :ready="ordersReady"
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
          >位置与经营资料<small>更换取餐位置、收摊时间与资质信息</small></span
        >
      </summary>
      <section class="store-inner-section">
        <div class="m-panel-head">
          <h2><MapPin :size="20" /> 取餐位置与时间</h2>
          <button
            class="btn btn-secondary"
            :disabled="!!busy"
            @click="position"
          >
            <Navigation :size="16" /> 获取当前位置
          </button>
        </div>
        <p v-if="!session.config?.amap_key" class="m-info-banner">
          地图暂未配置。可以先保存地址草稿，请团队协助核实；这不会确认位置或开放接单。
        </p>
        <button
          class="btn btn-secondary"
          :disabled="!!busy"
          @click="pickLocation"
        >
          在地图上选择位置
        </button>
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
          @submit.prevent="updateStatus(confirmationStatus, true)"
        >
          <label
            >详细取餐地址<input
              v-model="location.address"
              required
              maxlength="200"
              placeholder="例如：学府路夜市入口左侧第三个摊位"
          /></label>
          <label
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
          <p class="m-setting-note">
            <ShieldCheck :size="18" />
            更换取餐地址或坐标后，将暂停在线接单，待运营重新核验。历史订单仍保留原取餐地址，请主动联系顾客。
          </p>
          <button class="btn btn-primary" :disabled="!!busy" type="submit">
            <Save :size="16" /> 确认并保存位置与时间
          </button>
          <button
            class="btn btn-secondary"
            :disabled="!!busy"
            type="button"
            @click="saveAddressDraft"
          >
            仅保存地址草稿
          </button>
        </form>
      </section>
      <section class="store-inner-section">
        <div class="m-panel-head">
          <h2><ShieldCheck :size="20" /> 经营信息</h2>
          <span
            class="m-status"
            :class="stall.transaction_enabled ? 'open' : 'paused'"
            >{{
              stall.transaction_enabled
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
        <p class="m-muted">
          流动摊位由顾客到摊扫你的收款码付款，钱直接到你账户，平台不经手。登记了实体门店且证照核验通过的商户，才能开通平台微信支付和配送。
        </p>
      </section>
    </details>
    <details class="m-panel store-details store-services">
      <summary><Settings2 :size="20" /><span>支付与配送<small>线上支付、配送开关与交接点</small></span></summary>
      <MerchantServices :stall="stall" @refresh="emit('refresh')" />
    </details>
    <details class="m-panel store-details">
      <summary>
        <Store :size="20" /><span
          >店铺资料<small>名称、封面、简介与联系电话</small></span
        >
      </summary>
      <section class="store-inner-section">
        <div class="m-panel-head">
          <h2><Store :size="20" /> 店铺信息</h2>
          <RouterLink v-if="stall.is_visible" :to="`/stalls/${stall.id}`" class="m-text-link"
            >预览 <ExternalLink :size="15"
          /></RouterLink>
          <small v-else class="muted">公开展示核验后可预览</small>
        </div>
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
          <p class="m-muted">关闭公开展示后，已下单的同学仍能通过订单联系商家。同一经营主体下的摊位共用此号码。</p>
          <label
            >通常出摊时段（选填）<input
              v-model="profile.usual_hours"
              maxlength="100"
              placeholder="例如：通常周一至周五 17:00–21:00"
            /><small class="m-muted"
              >只填写真实安排；这是计划说明，不代表此刻已经出摊。</small
            ></label
          >
          <label
            >怎样更容易找到你<textarea
              v-model="profile.arrival_note"
              rows="3"
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
              ><small>独立于店铺封面。照片中请包含路口、招牌等明显参照。</small
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
          <div class="m-shop-photo">
            <img
              v-if="ownedImage(profile.payment_qr_image)"
              :src="ownedImage(profile.payment_qr_image)"
              alt="摊主自有收款码"
            />
            <div>
              <label class="btn btn-secondary m-upload"
                ><Camera :size="16" /> 上传我的收款码<input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  :disabled="!!busy"
                  @change="upload($event, 'payment_qr_image')"
                  aria-label="上传我的收款码" /></label
              ><small
                >上传你自己的微信或支付宝收款码。只给已下单、待付款的顾客看，钱直接进你的账户，平台不经手。</small
              ><button
                v-if="profile.payment_qr_image"
                type="button"
                class="m-text-link"
                @click="profile.payment_qr_image = ''"
              >
                移除收款码
              </button>
            </div>
          </div>
          <button class="btn btn-primary" type="submit" :disabled="!!busy">
            <Save :size="16" /> 保存店铺信息
          </button>
        </form>
      </section>
    </details>
    <details class="m-panel store-details">
      <summary>
        <Store :size="20" /><span>更多设置<small>账号安全与找回</small></span>
      </summary>
      <section class="store-inner-section">
        <p class="m-muted">
          保存一次性恢复码，忘记密码时可自行重设。恢复码请自己保管，不要贴在摊位二维码旁。
        </p>
        <RouterLink to="/account-security" class="btn btn-secondary"
          >管理账号恢复码</RouterLink
        >
      </section>
    </details>
  </div>
</template>

<style scoped>
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
  content: "+";
  font-size: 24px;
  font-weight: 400;
  color: #9f8267;
}
.store-details[open] > summary::after {
  content: "−";
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
