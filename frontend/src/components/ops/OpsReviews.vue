<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import {
  CheckCircle2,
  EyeOff,
  MessageSquareOff,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  Star,
} from "lucide-vue-next";
import { api } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";

const props = defineProps<{
  refreshTick?: number;
}>();

const emit = defineEmits<{
  (e: "changed"): void;
}>();

interface RiskFlag {
  code: string;
  label: string;
  tone: "warn" | "danger";
}

interface OpsReviewItem {
  id: number;
  mode: "live" | "simulation";
  order_id: string;
  order_number: string;
  order_payment_method: "offline" | "wechat";
  order_total_cents: number;
  fulfillment_seconds: number | null;
  stall_id: number;
  stall_name: string;
  user_id: number;
  username: string;
  display_name: string;
  rating: number;
  content: string;
  created_at: string;
  merchant_reply: string;
  replied_at: string | null;
  is_hidden: boolean;
  moderation_status: "unreviewed" | "cleared" | "hidden";
  moderation_reason: string;
  moderated_at: string | null;
  moderated_by_username: string;
  pair_7d_count: number;
  pair_total_count: number;
  risk_flags: RiskFlag[];
  is_flagged: boolean;
}

const session = useSession();
const loading = ref(true);
const error = ref("");

const statusFilter = ref<"flagged" | "all" | "visible" | "hidden">("all");
const modeFilter = ref<"all" | "live" | "simulation">("all");
const ratingFilter = ref("all");
const stallFilter = ref("");
const query = ref("");

const counts = ref<Record<string, number>>({});
const stalls = ref<{ id: number; name: string }[]>([]);
const items = ref<OpsReviewItem[]>([]);
const busyId = ref<number | null>(null);
const reasons = reactive<Record<number, string>>({});
let initializedDefaultTab = false;

const quickReasons = [
  "同客短时高频极速完单，疑似刷评控分",
  "评价含好评返现、场外引流或重复灌水内容",
  "含恶意人身攻击、辱骂或失实诋毁言论",
  "商家回复含辱骂驱赶或暴露隐私等不当言论",
  "核对订单履约属实，属真实消费体验予以保留",
];

function formatFulfillment(sec: number | null) {
  if (sec === null || sec === undefined) return "未记录";
  if (sec < 60) return `${sec} 秒`;
  const mins = Math.floor(sec / 60);
  const rem = sec % 60;
  return rem ? `${mins} 分 ${rem} 秒` : `${mins} 分钟`;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({
      status: statusFilter.value,
      mode: modeFilter.value,
      rating: ratingFilter.value,
    });
    if (stallFilter.value) params.set("stall", stallFilter.value);
    if (query.value.trim()) params.set("q", query.value.trim());

    const res = await api<{
      counts: Record<string, number>;
      stalls: { id: number; name: string }[];
      results: OpsReviewItem[];
    }>(`/ops/reviews?${params.toString()}`);

    counts.value = res.counts || {};
    stalls.value = res.stalls || [];
    items.value = res.results || [];

    if (!initializedDefaultTab) {
      initializedDefaultTab = true;
      if ((counts.value.flagged || 0) > 0 && statusFilter.value === "all") {
        statusFilter.value = "flagged";
        return;
      }
    }

    for (const row of items.value) {
      if (!(row.id in reasons)) {
        reasons[row.id] = "";
      }
    }
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

watch(
  [statusFilter, modeFilter, ratingFilter, stallFilter, () => props.refreshTick],
  () => {
    void load();
  },
);

function fillPreset(reviewId: number, preset: string) {
  reasons[reviewId] = preset;
}

async function runAction(
  row: OpsReviewItem,
  action: "hide" | "restore" | "clear" | "clear_reply",
) {
  if (busyId.value !== null) return;
  const reason = (reasons[row.id] || "").trim();
  if (!reason) {
    notify("执行评论治理或复核操作时，必须填写治理原因与核实依据。", "error");
    return;
  }

  busyId.value = row.id;
  try {
    await api(`/ops/reviews/${row.id}`, {
      method: "PATCH",
      body: {
        action,
        moderation_reason: reason,
      },
    });
    reasons[row.id] = "";
    const msgMap: Record<typeof action, string> = {
      hide: "已屏蔽该评价，不再计入摊位公开评分与展示",
      restore: "已恢复该评价为公开展示并重新计入评分",
      clear: "已标记复核正常，保留公开展示并移出待查队列",
      clear_reply: "已清理不当商家回复，顾客原评价予以保留",
    };
    notify(msgMap[action], "success");
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
          <h2>评论治理与防刷评风控</h2>
          <p>
            自动识别同客同摊高频连评、极速完单刷评及恶意攻击言论。违规评价屏蔽后立即剔除出摊位公开评分且锁定原单不可重刷；真实体验差评请点击「复核正常保留」，严禁变相控评洗分。
          </p>
        </div>
        <button type="button" class="o-btn" :disabled="loading" @click="load">
          <RefreshCw :size="15" /> 刷新
        </button>
      </div>

      <div class="o-toolbar">
        <div class="o-tabs" role="tablist" aria-label="评价治理状态筛选">
          <button
            type="button"
            class="o-tab"
            :class="{ active: statusFilter === 'flagged' }"
            @click="statusFilter = 'flagged'"
          >
            待复核风险 ({{ counts.flagged ?? 0 }})
          </button>
          <button
            type="button"
            class="o-tab"
            :class="{ active: statusFilter === 'all' }"
            @click="statusFilter = 'all'"
          >
            全部评价 ({{ counts.all ?? 0 }})
          </button>
          <button
            type="button"
            class="o-tab"
            :class="{ active: statusFilter === 'visible' }"
            @click="statusFilter = 'visible'"
          >
            公开展示 ({{ counts.visible ?? 0 }})
          </button>
          <button
            type="button"
            class="o-tab"
            :class="{ active: statusFilter === 'hidden' }"
            @click="statusFilter = 'hidden'"
          >
            已屏蔽 ({{ counts.hidden ?? 0 }})
          </button>
        </div>

        <form class="o-filters" @submit.prevent="load">
          <select
            v-model="modeFilter"
            class="o-select"
            aria-label="按订单模式筛选"
          >
            <option value="all">全部模式</option>
            <option value="live">正式订单</option>
            <option value="simulation">模拟演练</option>
          </select>

          <select
            v-model="ratingFilter"
            class="o-select"
            aria-label="按星级筛选"
          >
            <option value="all">全部星级</option>
            <option value="low">低分差评（1-2 星）</option>
            <option value="1">仅 1 星评价</option>
            <option value="high">高分好评（4-5 星）</option>
          </select>

          <select
            v-model="stallFilter"
            class="o-select"
            aria-label="按摊位筛选"
          >
            <option value="">全部摊位</option>
            <option v-for="s in stalls" :key="s.id" :value="String(s.id)">
              {{ s.name }}
            </option>
          </select>

          <input
            v-model="query"
            class="o-input"
            placeholder="搜正文、回复、用户或订单号"
            aria-label="搜索评价关键词"
          />
          <button type="submit" class="o-btn">
            <Search :size="15" /> 搜索
          </button>
        </form>
      </div>
    </section>

    <p v-if="error" class="o-alert" role="alert">{{ error }}</p>
    <div v-else-if="loading" class="o-card o-empty">正在读取评价与风控信号…</div>
    <div v-else-if="!items.length" class="o-card o-empty">
      当前筛选条件下暂无评价记录。
    </div>

    <div v-else class="o-list">
      <article v-for="row in items" :key="row.id" class="o-item">
        <div class="o-item-top">
          <div class="o-item-title">
            <h3>{{ row.stall_name }}</h3>
            <span
              class="o-badge"
              :class="
                row.rating <= 2 ? 'danger' : row.rating >= 4 ? 'ok' : 'warn'
              "
            >
              <Star :size="12" fill="currentColor" /> {{ row.rating }} 星
            </span>
            <span class="o-badge">
              {{ row.mode === "simulation" ? "模拟演练" : "正式评价" }}
            </span>
            <span v-if="row.is_hidden" class="o-badge danger">
              <EyeOff :size="12" /> 已屏蔽（不计入评分）
            </span>
            <span v-else-if="row.is_flagged" class="o-badge warn">
              <ShieldAlert :size="12" /> 待人工复核
            </span>
            <span
              v-else-if="row.moderation_status === 'cleared'"
              class="o-badge ok"
            >
              <CheckCircle2 :size="12" /> 复核保留公开
            </span>
            <span v-else class="o-badge pine">正常公开</span>
          </div>

          <span class="muted" style="font-size: 12px">
            {{ new Date(row.created_at).toLocaleString("zh-CN") }}
          </span>
        </div>

        <div class="o-meta-row">
          <span>评价人：{{ row.display_name }}（{{ row.username }}）</span>
          <span>订单号：{{ row.order_number }}</span>
          <span>
            结算方式：{{
              row.order_payment_method === "wechat" ? "微信支付" : "到摊付款"
            }}（¥{{ (row.order_total_cents / 100).toFixed(2) }}）
          </span>
          <span>下单至完单耗时：{{ formatFulfillment(row.fulfillment_seconds) }}</span>
          <span>
            同客同摊评价频次：7天 {{ row.pair_7d_count }} 次 / 累计
            {{ row.pair_total_count }} 次
          </span>
        </div>

        <div v-if="row.risk_flags.length" class="o-actions-row">
          <span
            v-for="flag in row.risk_flags"
            :key="flag.code"
            class="o-badge"
            :class="flag.tone"
          >
            <ShieldAlert :size="12" /> {{ flag.label }}
          </span>
        </div>

        <div class="o-callout">
          <strong style="display: block; margin-bottom: 4px; font-size: 12px">
            顾客评价正文
          </strong>
          <span>{{ row.content || "（顾客仅提交星级，未填写文字）" }}</span>
        </div>

        <div
          v-if="row.merchant_reply"
          class="o-callout"
          style="background: #eef3f0; border: 1px solid #d5e2db"
        >
          <strong
            style="
              display: block;
              margin-bottom: 4px;
              font-size: 12px;
              color: #24372f;
            "
          >
            商家公开回复
            <small
              v-if="row.replied_at"
              style="font-weight: 400; margin-left: 6px; color: #5b6c64"
            >
              {{ new Date(row.replied_at).toLocaleString("zh-CN") }}
            </small>
          </strong>
          <span>{{ row.merchant_reply }}</span>
        </div>

        <div v-if="row.moderated_at" class="o-callout warn">
          <strong>最近治理记录：</strong>
          <span>{{ row.moderation_reason || "已执行治理操作" }}</span>
          <small style="display: block; margin-top: 4px">
            操作人：{{ row.moderated_by_username || "管理员" }} · 时间：{{
              new Date(row.moderated_at).toLocaleString("zh-CN")
            }}
          </small>
        </div>

        <div v-if="session.hasOpsPerm('manage_reviews')" class="o-drawer">
          <label class="o-field">
            治理原因与核实依据（所有治理动作必填）
            <input
              v-model="reasons[row.id]"
              class="o-input"
              maxlength="200"
              placeholder="请填写刷评核对证据、违规类型或复核结论说明"
            />
          </label>

          <div class="o-actions-row" style="gap: 6px">
            <button
              v-for="preset in quickReasons"
              :key="preset"
              type="button"
              class="o-btn"
              style="padding: 6px 11px; font-size: 12px; font-weight: 500"
              @click="fillPreset(row.id, preset)"
            >
              {{ preset }}
            </button>
          </div>

          <div class="o-actions-row">
            <template v-if="!row.is_hidden">
              <button
                type="button"
                class="o-btn o-btn-danger"
                :disabled="busyId !== null"
                @click="runAction(row, 'hide')"
              >
                <EyeOff :size="15" />
                {{ busyId === row.id ? "处理中…" : "屏蔽违规/刷评" }}
              </button>
              <button
                v-if="row.is_flagged || row.moderation_status === 'unreviewed'"
                type="button"
                class="o-btn o-btn-pine"
                :disabled="busyId !== null"
                @click="runAction(row, 'clear')"
              >
                <CheckCircle2 :size="15" /> 复核正常保留
              </button>
            </template>

            <button
              v-else
              type="button"
              class="o-btn o-btn-pine"
              :disabled="busyId !== null"
              @click="runAction(row, 'restore')"
            >
              <RotateCcw :size="15" />
              {{ busyId === row.id ? "恢复中…" : "恢复公开展示" }}
            </button>

            <button
              v-if="row.merchant_reply"
              type="button"
              class="o-btn o-btn-danger"
              :disabled="busyId !== null"
              @click="runAction(row, 'clear_reply')"
            >
              <MessageSquareOff :size="15" /> 清理不当商家回复
            </button>
          </div>
        </div>
      </article>
    </div>
  </div>
</template>
