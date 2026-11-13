<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ChevronDown, ChevronUp, RefreshCw, Search } from "lucide-vue-next";
import { api } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";

const emit = defineEmits<{
  (e: "changed"): void;
}>();

const session = useSession();
const loading = ref(true);
const error = ref("");
const stalls = ref<any[]>([]);
const areas = ref<any[]>([]);
const deliveryPoints = ref<any[]>([]);

const filterArea = ref("");
const filterTier = ref("all");
const filterVisibility = ref("all");
const filterEligibility = ref("all");
const keyword = ref("");

const expandedId = ref<number | null>(null);
const savingMerchant = ref(false);
const savingStall = ref(false);
const drawerError = ref("");

const merchantDraft = reactive({
  business_name: "",
  contact_phone: "",
  qualification_tier: "mobile_vendor" as "mobile_vendor" | "storefront",
  is_verified: false,
  license_number: "",
  licensed_business_address: "",
  food_preparation_address: "",
  license_valid_until: "",
  wechat_pay_account: "",
  qualification_note: "",
});

const stallDraft = reactive({
  name: "",
  category: "",
  area_id: 0,
  is_visible: false,
  public_phone_enabled: false,
  transaction_enabled: false,
  delivery_approved: false,
  delivery_enabled: false,
  delivery_point_ids: [] as number[],
  update_location: false,
  address: "",
  latitude: 31.2304,
  longitude: 121.4737,
});

const statusTextMap: Record<string, { label: string; tone: string }> = {
  open: { label: "营业中", tone: "ok" },
  paused: { label: "暂停接单", tone: "warn" },
  stale: { label: "位置过期停单", tone: "danger" },
  closed: { label: "收摊中", tone: "" },
};

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({
      tier: filterTier.value,
      visibility: filterVisibility.value,
      eligibility: filterEligibility.value,
    });
    if (filterArea.value) params.set("area", filterArea.value);
    if (keyword.value.trim()) params.set("q", keyword.value.trim());
    const res = await api<{
      stalls: any[];
      areas: any[];
      delivery_points: any[];
    }>(`/ops/stalls?${params.toString()}`);
    stalls.value = res.stalls || [];
    areas.value = res.areas || [];
    deliveryPoints.value = res.delivery_points || [];
    if (expandedId.value !== null) {
      const current = stalls.value.find((s) => s.id === expandedId.value);
      if (current) syncDrafts(current);
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

watch([filterArea, filterTier, filterVisibility, filterEligibility], () => {
  void load();
});

function syncDrafts(stall: any) {
  const m = stall.merchant;
  merchantDraft.business_name = m.business_name || "";
  merchantDraft.contact_phone = m.contact_phone || "";
  merchantDraft.qualification_tier = m.qualification_tier || "mobile_vendor";
  merchantDraft.is_verified = !!m.is_verified;
  merchantDraft.license_number = m.license_number || "";
  merchantDraft.licensed_business_address = m.licensed_business_address || "";
  merchantDraft.food_preparation_address = m.food_preparation_address || "";
  merchantDraft.license_valid_until = m.license_valid_until || "";
  merchantDraft.wechat_pay_account = m.wechat_pay_account || "";
  merchantDraft.qualification_note = m.qualification_note || "";

  stallDraft.name = stall.name || "";
  stallDraft.category = stall.category || "";
  stallDraft.area_id = stall.area_id;
  stallDraft.is_visible = !!stall.is_visible;
  stallDraft.public_phone_enabled = !!stall.public_phone_enabled;
  stallDraft.transaction_enabled = !!stall.transaction_enabled;
  stallDraft.delivery_approved = !!stall.delivery_approved;
  stallDraft.delivery_enabled = !!stall.delivery_enabled;
  stallDraft.delivery_point_ids = [...(stall.delivery_point_ids || [])];
  stallDraft.update_location = false;
  const areaObj = areas.value.find((a) => a.id === stall.area_id);
  stallDraft.address =
    stall.location?.address || stall.location_draft_address || "";
  stallDraft.latitude = stall.location?.latitude ?? areaObj?.latitude ?? 31.2304;
  stallDraft.longitude =
    stall.location?.longitude ?? areaObj?.longitude ?? 121.4737;
}

function toggleExpand(stall: any) {
  drawerError.value = "";
  if (expandedId.value === stall.id) {
    expandedId.value = null;
    return;
  }
  expandedId.value = stall.id;
  syncDrafts(stall);
}

const candidatePoints = computed(() => {
  const current = stalls.value.find((s) => s.id === expandedId.value);
  if (!current) return [];
  return deliveryPoints.value.filter(
    (p) =>
      p.area_id === stallDraft.area_id &&
      (current.is_demo && session.config?.demo_mode ? true : !p.is_simulation),
  );
});

function useDraftAddress(stall: any) {
  if (!stall.location_draft_address) return;
  stallDraft.update_location = true;
  stallDraft.address = stall.location_draft_address;
}

async function saveMerchant(stall: any) {
  if (savingMerchant.value) return;
  savingMerchant.value = true;
  drawerError.value = "";
  try {
    await api(`/ops/merchants/${stall.merchant_id}`, {
      method: "PATCH",
      body: {
        business_name: merchantDraft.business_name.trim(),
        contact_phone: merchantDraft.contact_phone.trim(),
        qualification_tier: merchantDraft.qualification_tier,
        is_verified: merchantDraft.is_verified,
        license_number: merchantDraft.license_number.trim(),
        licensed_business_address:
          merchantDraft.licensed_business_address.trim(),
        food_preparation_address: merchantDraft.food_preparation_address.trim(),
        license_valid_until: merchantDraft.license_valid_until || null,
        wechat_pay_account: merchantDraft.wechat_pay_account.trim() || null,
        qualification_note: merchantDraft.qualification_note.trim(),
      },
    });
    notify("经营主体与证照核验信息已更新", "success");
    await load();
    emit("changed");
  } catch (e) {
    drawerError.value = (e as Error).message;
  } finally {
    savingMerchant.value = false;
  }
}

async function saveStall(stall: any) {
  if (savingStall.value) return;
  savingStall.value = true;
  drawerError.value = "";
  try {
    const body: Record<string, any> = {
      name: stallDraft.name.trim(),
      category: stallDraft.category.trim(),
      area_id: Number(stallDraft.area_id),
      is_visible: stallDraft.is_visible,
      public_phone_enabled: stallDraft.public_phone_enabled,
      transaction_enabled: stallDraft.transaction_enabled,
      delivery_approved: stallDraft.delivery_approved,
      delivery_enabled: stallDraft.delivery_enabled,
      delivery_point_ids: stallDraft.delivery_point_ids,
    };
    if (stallDraft.update_location) {
      body.location = {
        address: stallDraft.address.trim(),
        latitude: Number(stallDraft.latitude),
        longitude: Number(stallDraft.longitude),
      };
    }
    await api(`/ops/stalls/${stall.id}`, {
      method: "PATCH",
      body,
    });
    notify("摊位展示、交易与定点权限已保存", "success");
    await load();
    emit("changed");
  } catch (e) {
    drawerError.value = (e as Error).message;
  } finally {
    savingStall.value = false;
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
          <h2>商户证照核验与摊位分层准入</h2>
          <p>
            流动摊默认仅限地图展示发现；仅经核验且证照在有效期内的固定门店，方可开通自取预定、线上支付与定点配送。
          </p>
        </div>
        <button type="button" class="o-btn" :disabled="loading" @click="load">
          <RefreshCw :size="15" /> 刷新
        </button>
      </div>

      <div class="o-toolbar">
        <div class="o-filters">
          <select
            v-model="filterArea"
            class="o-select"
            aria-label="按校园区域筛选"
          >
            <option value="">全部区域</option>
            <option v-for="a in areas" :key="a.id" :value="String(a.id)">
              {{ a.name }}
            </option>
          </select>
          <select
            v-model="filterTier"
            class="o-select"
            aria-label="按资质类型筛选"
          >
            <option value="all">全部资质类型</option>
            <option value="mobile_vendor">流动摊（仅展示发现）</option>
            <option value="storefront">持证固定门店</option>
          </select>
          <select
            v-model="filterVisibility"
            class="o-select"
            aria-label="按展示状态筛选"
          >
            <option value="all">全部展示状态</option>
            <option value="visible">公开展示中</option>
            <option value="hidden">隐藏未展示</option>
          </select>
          <select
            v-model="filterEligibility"
            class="o-select"
            aria-label="按交易资格筛选"
          >
            <option value="all">全部服务模式</option>
            <option value="trade">具备线上交易资格</option>
            <option value="discovery">仅展示发现模式</option>
          </select>
        </div>

        <form class="o-filters" @submit.prevent="load">
          <input
            v-model="keyword"
            class="o-input"
            placeholder="搜索摊位、经营主体或账号"
            aria-label="搜索摊位"
          />
          <button type="submit" class="o-btn">
            <Search :size="15" /> 搜索
          </button>
        </form>
      </div>
    </section>

    <p v-if="error" class="o-alert" role="alert">{{ error }}</p>
    <div v-else-if="loading" class="o-card o-empty">正在读取摊位与商户档案…</div>
    <div v-else-if="!stalls.length" class="o-card o-empty">
      没有符合筛选条件的摊位。
    </div>

    <div v-else class="o-list">
      <article v-for="stall in stalls" :key="stall.id" class="o-item">
        <div class="o-item-top">
          <div class="o-item-title">
            <h3>{{ stall.name }}</h3>
            <span
              class="o-badge"
              :class="statusTextMap[stall.status]?.tone || ''"
            >
              {{ statusTextMap[stall.status]?.label || stall.status }}
            </span>
            <span class="o-badge" :class="stall.is_visible ? 'ok' : ''">
              {{ stall.is_visible ? "公开展示" : "未公开" }}
            </span>
            <span
              class="o-badge"
              :class="stall.transaction_eligible ? 'pine' : 'warn'"
            >
              {{
                stall.transaction_eligible
                  ? "可预约自取"
                  : "仅地图展示"
              }}
            </span>
            <span v-if="stall.is_demo" class="o-badge">示例摊位</span>
          </div>

          <button
            type="button"
            class="o-btn"
            @click="toggleExpand(stall)"
          >
            {{ expandedId === stall.id ? "收起治理面板" : "展开准入治理" }}
            <ChevronUp v-if="expandedId === stall.id" :size="15" />
            <ChevronDown v-else :size="15" />
          </button>
        </div>

        <div class="o-meta-row">
          <span
            >主体：{{ stall.merchant.business_name }}（账号
            {{ stall.merchant.username }}）</span
          >
          <span
            >资质：{{
              stall.merchant.qualification_tier === "storefront"
                ? "持证固定门店"
                : "流动摊"
            }}（{{ stall.merchant.is_verified ? "已人工核验" : "未核验" }}）</span
          >
          <span>区域：{{ stall.area_name }}</span>
          <span
            >上架餐点：{{ stall.active_product_count }}/{{
              stall.product_count
            }}</span
          >
        </div>

        <div class="o-meta-row">
          <span
            >已核验位置：{{
              stall.location?.address || "尚未完成现场位置核验"
            }}</span
          >
          <span v-if="stall.location_draft_address" style="color: #b34c10">
            商家提交位置草稿：{{ stall.location_draft_address }}
          </span>
        </div>

        <div v-if="expandedId === stall.id" class="o-drawer">
          <p v-if="drawerError" class="o-alert" role="alert">
            {{ drawerError }}
          </p>

          <div class="o-two-col">
            <form
              class="o-card"
              style="background: #fcfaf6"
              @submit.prevent="saveMerchant(stall)"
            >
              <div class="o-card-header">
                <div>
                  <h2>1. 经营主体与证照档案</h2>
                  <p>
                    {{
                      stall.merchant.trade_reason ||
                      "主体已满足持证固定门店交易准入要求。"
                    }}
                  </p>
                </div>
              </div>

              <p
                v-if="stall.merchant.sensitive_redacted"
                class="o-callout"
              >
                当前管理员账号仅具备摊位查看/治理权限，未授予商户证照与收款配置查看权限；敏感联系方式、许可证号与支付标识已由服务端脱敏。
              </p>

              <div v-else class="o-form-grid">
                <label class="o-field">
                  经营主体名称 *
                  <input
                    v-model="merchantDraft.business_name"
                    class="o-input"
                    required
                  />
                </label>
                <label class="o-field">
                  联系电话
                  <input
                    v-model="merchantDraft.contact_phone"
                    class="o-input"
                  />
                </label>
                <label class="o-field">
                  经营资质分级
                  <select
                    v-model="merchantDraft.qualification_tier"
                    class="o-select"
                  >
                    <option value="mobile_vendor">
                      流动摊（仅展示发现，不开通线上交易）
                    </option>
                    <option value="storefront">
                      持证固定门店（核验后可开通交易）
                    </option>
                  </select>
                </label>
                <label class="o-field">
                  证照有效期至
                  <input
                    v-model="merchantDraft.license_valid_until"
                    type="date"
                    class="o-input"
                  />
                </label>
                <label class="o-field span-2">
                  食品经营许可证 / 登记证编号
                  <input
                    v-model="merchantDraft.license_number"
                    class="o-input"
                    placeholder="持证门店核验必填"
                  />
                </label>
                <label class="o-field">
                  证载经营场所地址
                  <input
                    v-model="merchantDraft.licensed_business_address"
                    class="o-input"
                  />
                </label>
                <label class="o-field">
                  实际出餐加工地址
                  <input
                    v-model="merchantDraft.food_preparation_address"
                    class="o-input"
                  />
                </label>
                <label class="o-field">
                  微信直连收款配置标识（选填）
                  <input
                    v-model="merchantDraft.wechat_pay_account"
                    class="o-input"
                    placeholder="对应服务端商户收款账户 key"
                    :disabled="
                      !session.user?.is_superuser &&
                      !session.hasOpsPerm('resolve_payments')
                    "
                  />
                </label>
                <label class="o-field">
                  公开资质说明
                  <input
                    v-model="merchantDraft.qualification_note"
                    class="o-input"
                    placeholder="向学生展示的备案/卫生说明"
                  />
                </label>
                <label class="o-check span-2">
                  <input v-model="merchantDraft.is_verified" type="checkbox" />
                  已完成人工证照与主体核验（is_verified）
                </label>
                <div class="span-2 o-actions-row">
                  <button
                    v-if="session.hasOpsPerm('manage_merchants')"
                    type="submit"
                    class="o-btn o-btn-pine"
                    :disabled="savingMerchant"
                  >
                    {{ savingMerchant ? "正在保存…" : "保存主体与证照核验" }}
                  </button>
                </div>
              </div>
            </form>

            <form
              class="o-card"
              style="background: #fcfaf6"
              @submit.prevent="saveStall(stall)"
            >
              <div class="o-card-header">
                <div>
                  <h2>2. 摊位展示、交易权限与现场定位</h2>
                  <p>
                    修改核验取餐坐标时，若未同步勾选「允许自取预定」，系统将自动收回线上交易权限以防错位下单。
                  </p>
                </div>
              </div>

              <div class="o-form-grid">
                <label class="o-field">
                  摊位名称 *
                  <input v-model="stallDraft.name" class="o-input" required />
                </label>
                <label class="o-field">
                  品类
                  <input v-model="stallDraft.category" class="o-input" />
                </label>
                <label class="o-field span-2">
                  所属校园区域
                  <select v-model.number="stallDraft.area_id" class="o-select">
                    <option v-for="a in areas" :key="a.id" :value="a.id">
                      {{ a.name }}
                    </option>
                  </select>
                </label>

                <label class="o-check">
                  <input v-model="stallDraft.is_visible" type="checkbox" />
                  允许学生端公开展示
                </label>
                <label class="o-check">
                  <input
                    v-model="stallDraft.public_phone_enabled"
                    type="checkbox"
                  />
                  公开展示商家联系电话
                </label>
                <label class="o-check">
                  <input
                    v-model="stallDraft.transaction_enabled"
                    type="checkbox"
                  />
                  允许线上自取预定（需持证已核验）
                </label>
                <label class="o-check">
                  <input
                    v-model="stallDraft.delivery_approved"
                    type="checkbox"
                  />
                  批准短距配送资质（需持证已核验）
                </label>

                <div v-if="candidatePoints.length" class="span-2 o-field">
                  <span>授权可送达固定交接点（同区域）</span>
                  <div class="o-actions-row">
                    <label
                      v-for="pt in candidatePoints"
                      :key="pt.id"
                      class="o-check"
                    >
                      <input
                        v-model="stallDraft.delivery_point_ids"
                        type="checkbox"
                        :value="pt.id"
                      />
                      {{ pt.name }}
                    </label>
                  </div>
                </div>

                <div class="span-2" style="border-top: 1px solid #eae1d4; padding-top: 10px">
                  <div class="o-actions-row" style="margin-bottom: 8px">
                    <label class="o-check">
                      <input
                        v-model="stallDraft.update_location"
                        type="checkbox"
                      />
                      同步写入运营核验取餐坐标
                    </label>
                    <button
                      v-if="stall.location_draft_address"
                      type="button"
                      class="o-btn"
                      @click="useDraftAddress(stall)"
                    >
                      填入商家草稿地址
                    </button>
                  </div>

                  <div v-if="stallDraft.update_location" class="o-form-grid">
                    <label class="o-field span-2">
                      核验取餐位置说明 *
                      <input
                        v-model="stallDraft.address"
                        class="o-input"
                        required
                      />
                    </label>
                    <label class="o-field">
                      纬度 *
                      <input
                        v-model.number="stallDraft.latitude"
                        type="number"
                        step="any"
                        class="o-input"
                        required
                      />
                    </label>
                    <label class="o-field">
                      经度 *
                      <input
                        v-model.number="stallDraft.longitude"
                        type="number"
                        step="any"
                        class="o-input"
                        required
                      />
                    </label>
                  </div>
                </div>

                <div class="span-2 o-actions-row">
                  <button
                    v-if="session.hasOpsPerm('manage_stalls')"
                    type="submit"
                    class="o-btn o-btn-primary"
                    :disabled="savingStall"
                  >
                    {{ savingStall ? "正在保存…" : "保存摊位准入设置" }}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>
