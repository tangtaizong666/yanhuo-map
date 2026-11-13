<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { Check, RefreshCw } from "lucide-vue-next";
import { api } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";

const emit = defineEmits<{
  (e: "changed"): void;
}>();

const session = useSession();
const loading = ref(true);
const error = ref("");
const statusFilter = ref("unresolved");
const kindFilter = ref("all");
const counts = ref<Record<string, number>>({});
const items = ref<any[]>([]);
const busyId = ref<number | null>(null);

const drafts = reactive<
  Record<
    number,
    {
      verification: "unreviewed" | "confirmed" | "dismissed";
      verification_note: string;
      resolved: boolean;
    }
  >
>({});

const kindLabels: Record<string, string> = {
  not_found: "没找到摊位",
  wrong_location: "现场位置不符",
  closed_early: "提前收摊",
  order_help: "订单求助",
  general: "建议与反馈",
};

const verificationLabels: Record<string, { text: string; tone: string }> = {
  unreviewed: { text: "待核实", tone: "warn" },
  confirmed: { text: "已核实属实", tone: "danger" },
  dismissed: { text: "已核实不属实", tone: "ok" },
};

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({
      status: statusFilter.value,
      kind: kindFilter.value,
    });
    const res = await api<{ counts: Record<string, number>; results: any[] }>(
      `/ops/feedback?${params.toString()}`,
    );
    counts.value = res.counts || {};
    items.value = res.results || [];
    for (const row of items.value) {
      drafts[row.id] = {
        verification: row.verification || "unreviewed",
        verification_note: row.verification_note || "",
        resolved: !!row.resolved,
      };
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

watch([statusFilter, kindFilter], () => {
  void load();
});

async function save(row: any) {
  const draft = drafts[row.id];
  if (!draft || busyId.value !== null) return;
  if (
    (draft.verification === "confirmed" ||
      draft.verification === "dismissed") &&
    !draft.verification_note.trim()
  ) {
    notify("标记已核实属实或不属实时，必须填写核实方式与结论依据。", "error");
    return;
  }
  busyId.value = row.id;
  try {
    await api(`/ops/feedback/${row.id}`, {
      method: "PATCH",
      body: {
        verification: draft.verification,
        verification_note: draft.verification_note.trim(),
        resolved: draft.resolved,
      },
    });
    notify("反馈核实结论已保存", "success");
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
          <h2>现场位置纠错与订单求助核实</h2>
          <p>
            学生提交的联系方式与正文仅限治理台核实使用，不会向商家端暴露；仅填写核实依据并标记「已核实属实」的位置报错计入试点核实指标。
          </p>
        </div>
        <button type="button" class="o-btn" :disabled="loading" @click="load">
          <RefreshCw :size="15" /> 刷新
        </button>
      </div>

      <div class="o-toolbar">
        <div class="o-tabs" role="tablist" aria-label="反馈处理筛选">
          <button
            type="button"
            class="o-tab"
            :class="{ active: statusFilter === 'unresolved' }"
            @click="statusFilter = 'unresolved'"
          >
            待处理 ({{ counts.unresolved ?? 0 }})
          </button>
          <button
            type="button"
            class="o-tab"
            :class="{ active: statusFilter === 'unreviewed' }"
            @click="statusFilter = 'unreviewed'"
          >
            待现场核实 ({{ counts.unreviewed ?? 0 }})
          </button>
          <button
            type="button"
            class="o-tab"
            :class="{ active: statusFilter === 'all' }"
            @click="statusFilter = 'all'"
          >
            全部记录 ({{ counts.all ?? 0 }})
          </button>
        </div>

        <div class="o-filters">
          <select
            v-model="kindFilter"
            class="o-select"
            aria-label="按反馈类型筛选"
          >
            <option value="all">全部反馈类型</option>
            <option value="not_found">没找到摊位</option>
            <option value="wrong_location">现场位置不符</option>
            <option value="closed_early">提前收摊</option>
            <option value="order_help">订单求助</option>
            <option value="general">建议与反馈</option>
          </select>
        </div>
      </div>
    </section>

    <p v-if="error" class="o-alert" role="alert">{{ error }}</p>
    <div v-else-if="loading" class="o-card o-empty">正在读取反馈列表…</div>
    <div v-else-if="!items.length" class="o-card o-empty">
      当前筛选下暂无待处理的现场反馈。
    </div>

    <div v-else class="o-list">
      <article v-for="row in items" :key="row.id" class="o-item">
        <div class="o-item-top">
          <div class="o-item-title">
            <h3>{{ kindLabels[row.kind] || row.kind }}</h3>
            <span
              class="o-badge"
              :class="verificationLabels[row.verification]?.tone || ''"
            >
              {{ verificationLabels[row.verification]?.text || row.verification }}
            </span>
            <span class="o-badge" :class="row.resolved ? 'ok' : 'warn'">
              {{ row.resolved ? "已结案" : "跟进中" }}
            </span>
          </div>
          <span class="muted" style="font-size: 12px">
            {{ new Date(row.created_at).toLocaleString("zh-CN") }}
          </span>
        </div>

        <div class="o-meta-row">
          <span v-if="row.stall_name"
            >涉及摊位：{{ row.stall_name }}（#{{ row.stall_id }}）</span
          >
          <span v-if="row.order_number">关联订单：{{ row.order_number }}</span>
          <span>提交人：{{ row.username }}</span>
          <span v-if="row.contact">回访联系方式：{{ row.contact }}</span>
        </div>

        <p style="margin: 0; line-height: 1.65">{{ row.content }}</p>

        <div
          v-if="row.location_snapshot?.address || row.current_stall_address"
          class="o-callout"
        >
          <div>
            上报时展示位置：{{ row.location_snapshot?.address || "未记录" }}
          </div>
          <div v-if="row.current_stall_address">
            当前核验位置：{{ row.current_stall_address }}
          </div>
        </div>

        <form
          v-if="session.hasOpsPerm('manage_feedback') && drafts[row.id]"
          class="o-drawer o-form-grid"
          @submit.prevent="save(row)"
        >
          <label class="o-field">
            核实结论
            <select v-model="drafts[row.id].verification" class="o-select">
              <option value="unreviewed">待核实</option>
              <option value="confirmed">已核实属实</option>
              <option value="dismissed">已核实不属实</option>
            </select>
          </label>
          <label class="o-check" style="align-self: end">
            <input v-model="drafts[row.id].resolved" type="checkbox" />
            标记该工单已处理完结
          </label>
          <label class="o-field span-2">
            核实方式与结论依据（标记属实或不属实时必填）
            <input
              v-model="drafts[row.id].verification_note"
              class="o-input"
              placeholder="例如 巡查员现场查看、电话联系摊主核对照片等"
            />
          </label>
          <div class="span-2 o-actions-row">
            <button
              type="submit"
              class="o-btn o-btn-primary"
              :disabled="busyId !== null"
            >
              <Check :size="15" /> 保存核实结果
            </button>
          </div>
        </form>
      </article>
    </div>
  </div>
</template>
