<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { MapPin, Plus, RefreshCw } from "lucide-vue-next";
import { api } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";

const props = defineProps<{
  refreshTick?: number;
}>();

const emit = defineEmits<{
  (e: "changed"): void;
}>();

const session = useSession();
const loading = ref(true);
const error = ref("");
const areas = ref<any[]>([]);

const showAreaForm = ref(false);
const areaBusy = ref(false);
const areaError = ref("");
const newArea = reactive({
  name: "",
  subtitle: "",
  latitude: 31.2304,
  longitude: 121.4737,
  is_demo: false,
});

const editingAreaId = ref<number | null>(null);
const areaDraft = reactive({
  name: "",
  subtitle: "",
  latitude: 0,
  longitude: 0,
});

const addingPointFor = ref<number | null>(null);
const pointBusy = ref(false);
const pointError = ref("");
const newPoint = reactive({
  name: "",
  address: "",
  latitude: 31.2306,
  longitude: 121.4739,
  is_active: true,
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const res = await api<{ areas: any[] }>("/ops/areas");
    areas.value = res.areas || [];
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

function parseCoord(val: unknown) {
  if (val === "" || val === null || val === undefined || typeof val === "boolean") {
    return Number.NaN;
  }
  return Number(val);
}

watch(
  () => props.refreshTick,
  () => {
    void load();
  },
);

async function createArea() {
  if (areaBusy.value) return;
  areaBusy.value = true;
  areaError.value = "";
  try {
    const lat = parseCoord(newArea.latitude);
    const lng = parseCoord(newArea.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      areaError.value = "请填写有效的中心经纬度数值。";
      return;
    }
    await api("/ops/areas", {
      method: "POST",
      body: {
        name: newArea.name.trim(),
        subtitle: newArea.subtitle.trim(),
        latitude: lat,
        longitude: lng,
        ...(session.config?.demo_mode ? { is_demo: newArea.is_demo } : {}),
      },
    });
    notify("校园区域已创建", "success");
    newArea.name = "";
    newArea.subtitle = "";
    showAreaForm.value = false;
    await load();
    emit("changed");
  } catch (e) {
    areaError.value = (e as Error).message;
  } finally {
    areaBusy.value = false;
  }
}

function startEditArea(area: any) {
  editingAreaId.value = area.id;
  areaDraft.name = area.name;
  areaDraft.subtitle = area.subtitle || "";
  areaDraft.latitude = area.latitude;
  areaDraft.longitude = area.longitude;
}

async function saveArea(areaId: number) {
  if (areaBusy.value) return;
  const lat = parseCoord(areaDraft.latitude);
  const lng = parseCoord(areaDraft.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    notify("请填写有效的经纬度数值。", "error");
    return;
  }
  areaBusy.value = true;
  try {
    await api(`/ops/areas/${areaId}`, {
      method: "PATCH",
      body: {
        name: areaDraft.name.trim(),
        subtitle: areaDraft.subtitle.trim(),
        latitude: lat,
        longitude: lng,
      },
    });
    notify("区域信息已更新", "success");
    editingAreaId.value = null;
    await load();
    emit("changed");
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    areaBusy.value = false;
  }
}

function openPointForm(area: any) {
  addingPointFor.value = addingPointFor.value === area.id ? null : area.id;
  pointError.value = "";
  newPoint.name = "";
  newPoint.address = "";
  newPoint.latitude = area.latitude;
  newPoint.longitude = area.longitude;
  newPoint.is_active = true;
}

async function createPoint(areaId: number) {
  if (pointBusy.value) return;
  pointBusy.value = true;
  pointError.value = "";
  try {
    const lat = parseCoord(newPoint.latitude);
    const lng = parseCoord(newPoint.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      pointError.value = "请填写有效的经纬度数值。";
      return;
    }
    await api("/ops/delivery-points", {
      method: "POST",
      body: {
        area_id: areaId,
        name: newPoint.name.trim(),
        address: newPoint.address.trim(),
        latitude: lat,
        longitude: lng,
        is_active: newPoint.is_active,
      },
    });
    notify("校园固定交接点已添加", "success");
    addingPointFor.value = null;
    await load();
    emit("changed");
  } catch (e) {
    pointError.value = (e as Error).message;
  } finally {
    pointBusy.value = false;
  }
}

async function togglePoint(point: any) {
  try {
    await api(`/ops/delivery-points/${point.id}`, {
      method: "PATCH",
      body: { is_active: !point.is_active },
    });
    notify(
      point.is_active ? "已停用该固定交接点" : "已启用该固定交接点",
      "success",
    );
    await load();
    emit("changed");
  } catch (e) {
    notify((e as Error).message, "error");
  }
}

onMounted(() => {
  void load();
});
</script>

<template>
  <div class="stack" style="gap: 18px">
    <section class="o-card">
      <div class="o-card-header">
        <div>
          <h2>校园经营区域与固定配送交接点</h2>
          <p>
            管理全校开放摆摊片区坐标，以及持证门店短距配送可绑定的校门、宿舍楼下保温取餐交接点。
          </p>
        </div>
        <div class="o-actions-row">
          <button
            v-if="session.hasOpsPerm('add_areas')"
            type="button"
            class="o-btn o-btn-pine"
            @click="showAreaForm = !showAreaForm"
          >
            <Plus :size="16" />
            {{ showAreaForm ? "收起新建区域" : "新增校园区域" }}
          </button>
          <button type="button" class="o-btn" :disabled="loading" @click="load">
            <RefreshCw :size="15" /> 刷新
          </button>
        </div>
      </div>

      <form
        v-if="showAreaForm"
        class="o-drawer o-form-grid"
        @submit.prevent="createArea"
      >
        <label class="o-field">
          区域名称 *
          <input
            v-model="newArea.name"
            class="o-input"
            placeholder="例如 南苑生活区"
            required
          />
        </label>
        <label class="o-field">
          片区副标题说明
          <input
            v-model="newArea.subtitle"
            class="o-input"
            placeholder="例如 下课人流集中、晚间餐车停靠点"
          />
        </label>
        <label class="o-field">
          中心纬度（Latitude）*
          <input
            v-model.number="newArea.latitude"
            type="number"
            min="-90"
            max="90"
            step="any"
            class="o-input"
            required
          />
        </label>
        <label class="o-field">
          中心经度（Longitude）*
          <input
            v-model.number="newArea.longitude"
            type="number"
            min="-180"
            max="180"
            step="any"
            class="o-input"
            required
          />
        </label>
        <label v-if="session.config?.demo_mode" class="o-check span-2">
          <input v-model="newArea.is_demo" type="checkbox" />
          标记为演示示例区域
        </label>
        <div class="span-2 o-actions-row">
          <p v-if="areaError" class="o-alert" role="alert" style="width: 100%">
            {{ areaError }}
          </p>
          <button
            type="submit"
            class="o-btn o-btn-primary"
            :disabled="areaBusy"
          >
            {{ areaBusy ? "正在创建…" : "创建校园区域" }}
          </button>
        </div>
      </form>
    </section>

    <p v-if="error" class="o-alert" role="alert">{{ error }}</p>
    <div v-else-if="loading" class="o-card o-empty">正在读取区域与交接点…</div>

    <div v-else class="o-list">
      <article v-for="area in areas" :key="area.id" class="o-item">
        <div class="o-item-top">
          <div class="o-item-title">
            <MapPin :size="17" />
            <h3>{{ area.name }}</h3>
            <span v-if="area.is_demo" class="o-badge warn">示例区域</span>
            <span class="o-badge pine">关联摊位 {{ area.stall_count }} 家</span>
          </div>
          <div class="o-actions-row">
            <button
              v-if="session.hasOpsPerm('manage_areas')"
              type="button"
              class="o-btn"
              @click="
                editingAreaId === area.id
                  ? (editingAreaId = null)
                  : startEditArea(area)
              "
            >
              {{ editingAreaId === area.id ? "取消编辑" : "编辑区域" }}
            </button>
            <button
              v-if="session.hasOpsPerm('add_delivery_points')"
              type="button"
              class="o-btn o-btn-pine"
              @click="openPointForm(area)"
            >
              <Plus :size="15" /> 新增交接点
            </button>
          </div>
        </div>

        <div class="o-meta-row">
          <span>说明：{{ area.subtitle || "暂无说明" }}</span>
          <span
            >坐标：{{ area.latitude.toFixed(5) }},
            {{ area.longitude.toFixed(5) }}</span
          >
        </div>

        <form
          v-if="editingAreaId === area.id"
          class="o-drawer o-form-grid"
          @submit.prevent="saveArea(area.id)"
        >
          <label class="o-field">
            区域名称
            <input v-model="areaDraft.name" class="o-input" required />
          </label>
          <label class="o-field">
            片区副标题
            <input v-model="areaDraft.subtitle" class="o-input" />
          </label>
          <label class="o-field">
            纬度
            <input
              v-model.number="areaDraft.latitude"
              type="number"
              min="-90"
              max="90"
              step="any"
              class="o-input"
              required
            />
          </label>
          <label class="o-field">
            经度
            <input
              v-model.number="areaDraft.longitude"
              type="number"
              min="-180"
              max="180"
              step="any"
              class="o-input"
              required
            />
          </label>
          <div class="span-2 o-actions-row">
            <button
              type="submit"
              class="o-btn o-btn-primary"
              :disabled="areaBusy"
            >
              保存区域修改
            </button>
          </div>
        </form>

        <form
          v-if="addingPointFor === area.id"
          class="o-drawer o-form-grid"
          @submit.prevent="createPoint(area.id)"
        >
          <label class="o-field">
            交接点名称 *
            <input
              v-model="newPoint.name"
              class="o-input"
              placeholder="例如 三号宿舍楼北门取餐桌"
              required
            />
          </label>
          <label class="o-field">
            现场取餐说明 *
            <input
              v-model="newPoint.address"
              class="o-input"
              placeholder="例如 门禁外右侧白色保温架"
              required
            />
          </label>
          <label class="o-field">
            纬度 *
            <input
              v-model.number="newPoint.latitude"
              type="number"
              min="-90"
              max="90"
              step="any"
              class="o-input"
              required
            />
          </label>
          <label class="o-field">
            经度 *
            <input
              v-model.number="newPoint.longitude"
              type="number"
              min="-180"
              max="180"
              step="any"
              class="o-input"
              required
            />
          </label>
          <label class="o-check span-2">
            <input v-model="newPoint.is_active" type="checkbox" />
            立即启用供区域内获批摊位绑定
          </label>
          <div class="span-2 o-actions-row">
            <p
              v-if="pointError"
              class="o-alert"
              role="alert"
              style="width: 100%"
            >
              {{ pointError }}
            </p>
            <button
              type="submit"
              class="o-btn o-btn-primary"
              :disabled="pointBusy"
            >
              {{ pointBusy ? "正在保存…" : "保存交接点" }}
            </button>
          </div>
        </form>

        <div class="o-drawer">
          <strong style="font-size: 13px"
            >下辖固定交接点（{{ area.delivery_points?.length || 0 }}）</strong
          >
          <div v-if="!area.delivery_points?.length" class="muted">
            该区域暂未设置固定配送交接点。
          </div>
          <div v-else class="o-list">
            <div
              v-for="pt in area.delivery_points"
              :key="pt.id"
              class="o-item"
              style="padding: 12px 14px; background: #fcfaf6"
            >
              <div class="o-item-top">
                <div class="o-item-title">
                  <strong>{{ pt.name }}</strong>
                  <span v-if="pt.is_simulation" class="o-badge warn">
                    演练交接点（随模拟开关生效）
                  </span>
                  <span v-else class="o-badge" :class="pt.is_active ? 'ok' : ''">
                    {{ pt.is_active ? "已启用" : "已停用" }}
                  </span>
                </div>
                <button
                  v-if="!pt.is_simulation && session.hasOpsPerm('manage_delivery_points')"
                  type="button"
                  class="o-btn"
                  @click="togglePoint(pt)"
                >
                  {{ pt.is_active ? "停用" : "启用" }}
                </button>
              </div>
              <div class="o-meta-row">
                <span>位置说明：{{ pt.address }}</span>
                <span
                  >坐标：{{ pt.latitude.toFixed(5) }},
                  {{ pt.longitude.toFixed(5) }}</span
                >
              </div>
            </div>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>
