<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { Check, Plus, RefreshCw, Search, X } from "lucide-vue-next";
import { api } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";

const emit = defineEmits<{
  (e: "changed"): void;
}>();

const session = useSession();
const loading = ref(true);
const error = ref("");
const statusFilter = ref("all");
const keyword = ref("");
const counts = ref<Record<string, number>>({});
const items = ref<any[]>([]);
const reviewNotes = reactive<Record<number, string>>({});
const busyId = ref<number | null>(null);

const showAssistForm = ref(false);
const assistBusy = ref(false);
const assistError = ref("");
const assistForm = reactive({
  username: "",
  business_name: "",
  stall_name: "",
  contact_phone: "",
  area_id: "" as string | number,
  category: "小吃",
  address_note: "",
  description: "",
});

const statusLabels: Record<string, { text: string; tone: string }> = {
  submitted: { text: "待审核", tone: "warn" },
  needs_changes: { text: "待申请人修改", tone: "warn" },
  draft: { text: "草稿未提交", tone: "" },
  approved: { text: "初审已通过", tone: "ok" },
  rejected: { text: "已驳回", tone: "danger" },
};

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({ status: statusFilter.value });
    if (keyword.value.trim()) params.set("q", keyword.value.trim());
    const res = await api<{ counts: Record<string, number>; results: any[] }>(
      `/ops/applications?${params.toString()}`,
    );
    counts.value = res.counts || {};
    items.value = res.results || [];
    for (const row of items.value) {
      if (reviewNotes[row.id] === undefined) {
        reviewNotes[row.id] = row.review_note || "";
      }
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

watch(statusFilter, () => {
  void load();
});

async function createAssisted() {
  if (assistBusy.value) return;
  assistBusy.value = true;
  assistError.value = "";
  try {
    await api("/ops/applications", {
      method: "POST",
      body: {
        username: assistForm.username.trim(),
        business_name: assistForm.business_name.trim(),
        stall_name: assistForm.stall_name.trim(),
        contact_phone: assistForm.contact_phone.trim(),
        area_id: assistForm.area_id ? Number(assistForm.area_id) : null,
        category: assistForm.category.trim(),
        address_note: assistForm.address_note.trim(),
        description: assistForm.description.trim(),
      },
    });
    notify("辅助建档草稿已创建，请通知申请人登录核对并提交", "success");
    showAssistForm.value = false;
    assistForm.username = "";
    assistForm.business_name = "";
    assistForm.stall_name = "";
    assistForm.contact_phone = "";
    assistForm.address_note = "";
    assistForm.description = "";
    await load();
    emit("changed");
  } catch (e) {
    assistError.value = (e as Error).message;
  } finally {
    assistBusy.value = false;
  }
}

async function review(
  app: any,
  decision: "approved" | "needs_changes" | "rejected",
) {
  if (busyId.value !== null) return;
  const note = (reviewNotes[app.id] || "").trim();
  if (decision !== "approved" && !note) {
    notify("退回修改或驳回时，请先填写明确的审核意见。", "error");
    return;
  }
  busyId.value = app.id;
  try {
    await api(`/ops/applications/${app.id}/review`, {
      method: "POST",
      body: {
        decision,
        review_note: note,
      },
    });
    notify(
      decision === "approved"
        ? "已通过入驻初审并建立待核验摊位档案"
        : decision === "needs_changes"
          ? "已退回申请人补充修改"
          : "已驳回该入驻申请",
      "success",
    );
    await load();
    emit("changed");
  } catch (e) {
    notify((e as Error).message, "error");
  } finally {
    busyId.value = null;
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
          <h2>商家入驻初审与线下辅助建档</h2>
          <p>
            初审通过仅创建隐藏、未开通交易的摊位壳供摊主布置菜单；公开展示与线上交易须在「商户与摊位」模块完成证照与定点核验。
          </p>
        </div>
        <div class="o-actions-row">
          <button
            v-if="session.hasOpsPerm('create_assisted_application')"
            type="button"
            class="o-btn o-btn-pine"
            @click="showAssistForm = !showAssistForm"
          >
            <Plus :size="16" />
            {{ showAssistForm ? "收起辅助建档" : "线下辅助录入草稿" }}
          </button>
          <button
            type="button"
            class="o-btn"
            :disabled="loading"
            @click="load"
          >
            <RefreshCw :size="15" /> 刷新
          </button>
        </div>
      </div>

      <form
        v-if="showAssistForm"
        class="o-drawer o-form-grid"
        style="margin-bottom: 18px"
        @submit.prevent="createAssisted"
      >
        <div class="span-2 o-callout">
          线下访谈协助摊主预填资料后，必须由申请人使用本人账号登录并在入驻页确认提交，平台方可审批通过。
        </div>
        <label class="o-field">
          申请人已注册用户名 *
          <input
            v-model="assistForm.username"
            class="o-input"
            placeholder="例如 vendor_li"
            required
          />
        </label>
        <label class="o-field">
          经营主体名称
          <input
            v-model="assistForm.business_name"
            class="o-input"
            placeholder="个人经营者姓名或营业执照名称"
          />
        </label>
        <label class="o-field">
          拟用摊位名称
          <input
            v-model="assistForm.stall_name"
            class="o-input"
            placeholder="例如 李记手抓饼"
          />
        </label>
        <label class="o-field">
          联系电话
          <input
            v-model="assistForm.contact_phone"
            class="o-input"
            placeholder="13800138000"
          />
        </label>
        <label class="o-field">
          意向校园区域
          <select v-model="assistForm.area_id" class="o-select">
            <option value="">暂不指定</option>
            <option
              v-for="a in session.config?.areas || []"
              :key="a.id"
              :value="a.id"
            >
              {{ a.name }}
            </option>
          </select>
        </label>
        <label class="o-field">
          主营品类
          <input
            v-model="assistForm.category"
            class="o-input"
            placeholder="小吃 / 饮品 / 正餐 / 夜宵"
          />
        </label>
        <label class="o-field span-2">
          常驻位置说明
          <input
            v-model="assistForm.address_note"
            class="o-input"
            placeholder="例如 南门小广场东侧餐车位"
          />
        </label>
        <label class="o-field span-2">
          经营简介与备注
          <textarea
            v-model="assistForm.description"
            class="o-textarea"
            placeholder="主打餐点、出摊时段或现场核对说明"
          />
        </label>
        <div class="span-2 o-actions-row">
          <p v-if="assistError" class="o-alert" role="alert" style="width: 100%">
            {{ assistError }}
          </p>
          <button
            type="submit"
            class="o-btn o-btn-primary"
            :disabled="assistBusy"
          >
            {{ assistBusy ? "正在创建…" : "保存辅助草稿" }}
          </button>
        </div>
      </form>

      <div class="o-toolbar">
        <div class="o-tabs" role="tablist" aria-label="申请状态筛选">
          <button
            v-for="tab in [
              { id: 'all', label: '全部' },
              { id: 'submitted', label: '待审核' },
              { id: 'needs_changes', label: '待修改' },
              { id: 'draft', label: '草稿' },
              { id: 'approved', label: '已通过' },
              { id: 'rejected', label: '已驳回' },
            ]"
            :key="tab.id"
            type="button"
            class="o-tab"
            :class="{ active: statusFilter === tab.id }"
            @click="statusFilter = tab.id"
          >
            {{ tab.label }} ({{ counts[tab.id] ?? 0 }})
          </button>
        </div>

        <form class="o-filters" @submit.prevent="load">
          <input
            v-model="keyword"
            class="o-input"
            placeholder="搜索摊位名、主体或账号"
            aria-label="搜索入驻申请"
          />
          <button type="submit" class="o-btn">
            <Search :size="15" /> 查询
          </button>
        </form>
      </div>
    </section>

    <p v-if="error" class="o-alert" role="alert">{{ error }}</p>
    <div v-else-if="loading" class="o-card o-empty">正在读取入驻申请列表…</div>
    <div v-else-if="!items.length" class="o-card o-empty">
      当前筛选条件下暂无入驻申请记录。
    </div>

    <div v-else class="o-list">
      <article v-for="app in items" :key="app.id" class="o-item">
        <div class="o-item-top">
          <div class="o-item-title">
            <h3>{{ app.stall_name || "未命名摊位" }}</h3>
            <span
              class="o-badge"
              :class="statusLabels[app.status]?.tone || ''"
            >
              {{ statusLabels[app.status]?.text || app.status }}
            </span>
            <span class="o-badge pine">
              {{ app.source === "assisted" ? "运营辅助建档" : "摊主自主申请" }}
            </span>
          </div>
          <span class="muted" style="font-size: 12px">
            申请账号：{{ app.username }}（{{ app.display_name }}）
          </span>
        </div>

        <div class="o-meta-row">
          <span>经营主体：{{ app.business_name || "未填写" }}</span>
          <span>联系电话：{{ app.contact_phone || "未填写" }}</span>
          <span>意向区域：{{ app.area_name || "未选择" }}</span>
          <span>品类：{{ app.category || "未填写" }}</span>
        </div>

        <div class="o-meta-row">
          <span>拟出摊位置：{{ app.address_note || "未填写" }}</span>
          <span v-if="app.description">简介：{{ app.description }}</span>
          <span v-if="app.approved_stall_id"
            >关联摊位 ID：#{{ app.approved_stall_id }}</span
          >
        </div>

        <div
          v-if="
            session.hasOpsPerm('review_applications') &&
            app.status !== 'approved'
          "
          class="o-drawer"
        >
          <label class="o-field">
            审核意见（退回修改或驳回必填，申请人可见）
            <input
              v-model="reviewNotes[app.id]"
              class="o-input"
              placeholder="请填写资料核对意见或需补充事项"
            />
          </label>
          <div class="o-actions-row">
            <button
              type="button"
              class="o-btn o-btn-primary"
              :disabled="busyId !== null || app.status !== 'submitted'"
              @click="review(app, 'approved')"
            >
              <Check :size="15" /> 通过初审并建档
            </button>
            <button
              type="button"
              class="o-btn"
              :disabled="busyId !== null || app.status !== 'submitted'"
              @click="review(app, 'needs_changes')"
            >
              退回补充资料
            </button>
            <button
              type="button"
              class="o-btn o-btn-danger"
              :disabled="busyId !== null || app.status !== 'submitted'"
              @click="review(app, 'rejected')"
            >
              <X :size="15" /> 驳回申请
            </button>
            <small v-if="app.status !== 'submitted'" class="muted">
              当前状态为「{{
                statusLabels[app.status]?.text || app.status
              }}」，需申请人确认提交后方可审批。
            </small>
          </div>
        </div>
        <p v-else-if="app.review_note" class="o-callout">
          审核记录（{{ app.reviewed_by_username || "运营" }}）：{{
            app.review_note
          }}
        </p>
      </article>
    </div>
  </div>
</template>
