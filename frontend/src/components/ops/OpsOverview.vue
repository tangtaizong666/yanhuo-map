<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  SlidersHorizontal,
} from "lucide-vue-next";
import { api } from "../../lib/api";
import { notify } from "../../lib/notify";
import { useSession } from "../../stores/session";

const props = defineProps<{
  data: any;
  days: number;
  mode: "live" | "simulation";
  loading: boolean;
}>();

const emit = defineEmits<{
  (e: "changePeriod", days: number): void;
  (e: "changeMode", mode: "live" | "simulation"): void;
  (e: "navigate", section: string): void;
  (e: "refresh"): void;
}>();

const session = useSession();
const saving = ref(false);
const configError = ref("");
const cfgForm = reactive({
  stale_minutes: 60,
  pending_minutes: 15,
});

watch(
  () => props.data?.config,
  (cfg) => {
    if (!cfg) return;
    cfgForm.stale_minutes = cfg.stale_minutes;
    cfgForm.pending_minutes = cfg.pending_minutes;
  },
  { immediate: true },
);

const workerLabels: Record<string, string> = {
  expire_orders: "超时未接单自动关单",
  reconcile_payments: "微信支付与退款对账",
  process_payment_notifications: "支付回调异步队列",
};

function formatSeconds(sec: number | null | undefined) {
  if (sec === null || sec === undefined) return "暂无样本";
  if (sec < 60) return `${Math.round(sec)} 秒`;
  return `${(sec / 60).toFixed(1)} 分钟`;
}

async function saveConfig() {
  if (saving.value) return;
  saving.value = true;
  configError.value = "";
  try {
    await api("/ops/overview", {
      method: "PATCH",
      body: {
        stale_minutes: Number(cfgForm.stale_minutes),
        pending_minutes: Number(cfgForm.pending_minutes),
      },
    });
    notify("全局风控与时效阈值已保存", "success");
    emit("refresh");
  } catch (e) {
    configError.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="stack" style="gap: 18px">
    <section class="o-kpi-grid" aria-label="治理待办概览">
      <div
        class="o-kpi-card"
        :class="{ 'is-warn': (data?.summary?.applications_submitted || 0) > 0 }"
      >
        <span>待审入驻申请</span>
        <strong>{{ data?.summary?.applications_submitted ?? 0 }}</strong>
        <small>
          待补充 {{ data?.summary?.applications_needs_changes ?? 0 }} · 草稿
          {{ data?.summary?.applications_draft ?? 0 }}
        </small>
        <button
          type="button"
          class="o-btn"
          @click="emit('navigate', 'applications')"
        >
          前往入驻审核
        </button>
      </div>

      <div
        class="o-kpi-card"
        :class="{ 'is-warn': (data?.summary?.stalls_stale || 0) > 0 }"
      >
        <span>出摊与准入状态</span>
        <strong
          >{{ data?.summary?.stalls_open ?? 0 }} /
          {{ data?.summary?.stalls_total ?? 0 }}</strong
        >
        <small>
          展示中 {{ data?.summary?.stalls_visible ?? 0 }} · 位置过期
          {{ data?.summary?.stalls_stale ?? 0 }} · 可交易
          {{ data?.summary?.stalls_trade_eligible ?? 0 }}
        </small>
        <button type="button" class="o-btn" @click="emit('navigate', 'stalls')">
          管理商户与摊位
        </button>
      </div>

      <div
        class="o-kpi-card"
        :class="{
          'is-warn':
            (data?.summary?.orders_live_attention || 0) +
              (data?.summary?.orders_simulation_attention || 0) >
            0,
        }"
      >
        <span>需关注异常订单</span>
        <strong>{{
          (data?.summary?.orders_live_attention || 0) +
          (data?.summary?.orders_simulation_attention || 0)
        }}</strong>
        <small>
          正式进行中 {{ data?.summary?.orders_live_active ?? 0 }} · 演练进行中
          {{ data?.summary?.orders_simulation_active ?? 0 }}
        </small>
        <button type="button" class="o-btn" @click="emit('navigate', 'orders')">
          查看订单与对账
        </button>
      </div>

      <div
        class="o-kpi-card"
        :class="{ 'is-warn': (data?.summary?.feedback_unresolved || 0) > 0 }"
      >
        <span>待处理现场反馈</span>
        <strong>{{ data?.summary?.feedback_unresolved ?? 0 }}</strong>
        <small>
          待核实位置报错
          {{ data?.summary?.feedback_unreviewed_location ?? 0 }} · 已核验主体
          {{ data?.summary?.merchants_verified ?? 0 }}/{{
            data?.summary?.merchants_total ?? 0
          }}
        </small>
        <button
          type="button"
          class="o-btn"
          @click="emit('navigate', 'feedback')"
        >
          核实现场反馈
        </button>
      </div>
    </section>

    <div class="o-two-col">
      <section class="o-card">
        <div class="o-card-header">
          <div>
            <h2>服务端试点指标（真实记录口径）</h2>
            <p>
              仅统计数据库已完成的订单、营业场次与经运营核实的位置报错，不混入前端浏览埋点。
            </p>
          </div>
          <div class="o-actions-row">
            <select
              class="o-select"
              :value="mode"
              aria-label="试点指标统计模式"
              @change="
                emit(
                  'changeMode',
                  ($event.target as HTMLSelectElement).value as
                    | 'live'
                    | 'simulation',
                )
              "
            >
              <option value="live">正式服务记录</option>
              <option value="simulation">演练体验记录</option>
            </select>
            <select
              class="o-select"
              :value="days"
              aria-label="试点指标统计周期"
              @change="
                emit(
                  'changePeriod',
                  Number(($event.target as HTMLSelectElement).value),
                )
              "
            >
              <option :value="1">近 1 天</option>
              <option :value="7">近 7 天</option>
              <option :value="30">近 30 天</option>
            </select>
          </div>
        </div>

        <div class="o-dl-grid">
          <div class="o-dl-box">
            <span>活跃出摊商户日</span>
            <strong>{{ data?.pilot?.active_merchant_days ?? 0 }}</strong>
          </div>
          <div class="o-dl-box">
            <span>已完成且结清订单</span>
            <strong>{{ data?.pilot?.completed_orders ?? 0 }}</strong>
          </div>
          <div class="o-dl-box">
            <span>完单去重顾客数</span>
            <strong>{{ data?.pilot?.paying_customers ?? 0 }}</strong>
          </div>
          <div class="o-dl-box">
            <span>接单响应 P50</span>
            <strong>{{
              formatSeconds(data?.pilot?.accept_p50_seconds)
            }}</strong>
          </div>
          <div class="o-dl-box">
            <span>收到位置报错</span>
            <strong>{{ data?.pilot?.location_reports_received ?? 0 }}</strong>
          </div>
          <div class="o-dl-box">
            <span>核实属实报错</span>
            <strong>{{ data?.pilot?.location_reports_confirmed ?? 0 }}</strong>
          </div>
        </div>
      </section>

      <section class="o-card">
        <div class="o-card-header">
          <div>
            <h2>后台任务与队列健康度</h2>
            <p>监控订单超时关单、渠道支付对账与异步通知消费心跳。</p>
          </div>
          <span
            class="o-badge"
            :class="data?.runtime?.status === 'ok' ? 'ok' : 'warn'"
          >
            <CheckCircle2 v-if="data?.runtime?.status === 'ok'" :size="13" />
            <AlertTriangle v-else :size="13" />
            {{
              data?.runtime?.status === "ok"
                ? "任务正常"
                : "本地按需模式 / 待启动守护进程"
            }}
          </span>
        </div>

        <div class="o-list">
          <div
            v-for="w in data?.runtime?.workers || []"
            :key="w.name"
            class="o-item"
            style="padding: 12px 14px"
          >
            <div class="o-item-top">
              <div class="o-item-title">
                <Activity :size="15" />
                <strong>{{ workerLabels[w.name] || w.name }}</strong>
              </div>
              <span class="o-badge" :class="w.healthy ? 'ok' : 'warn'">
                {{ w.healthy ? "心跳正常" : "未检测到近期心跳" }}
              </span>
            </div>
            <div class="o-meta-row">
              <span>
                最近成功：{{
                  w.seconds_since_success !== null
                    ? `${Math.round(w.seconds_since_success)} 秒前`
                    : "尚未执行"
                }}
              </span>
              <span>批处理量：{{ w.processed_last_batch }}</span>
              <span v-if="w.failure_count"
                >失败计数：{{ w.failure_count }}</span
              >
            </div>
          </div>
        </div>

        <div class="o-meta-row" style="margin-top: 12px">
          <span
            >超时待关单积压：{{
              data?.runtime?.order_expiry?.overdue_count ?? 0
            }}</span
          >
          <span
            >待人工资金复核：{{
              data?.runtime?.payments?.reviews_required ?? 0
            }}</span
          >
          <span
            >回调冲突/死信：{{
              (data?.runtime?.notifications?.conflicts ?? 0) +
              (data?.runtime?.notifications?.dead ?? 0)
            }}</span
          >
        </div>
      </section>
    </div>

    <section class="o-card">
      <div class="o-card-header">
        <div>
          <h2>
            <SlidersHorizontal
              :size="17"
              style="vertical-align: -3px; margin-right: 6px"
            />全局营业与订单超时阈值
          </h2>
          <p>
            控制流动摊位置过期自动停单时长（默认 60 分钟）与新订单商家未接单自动释放库存时长。
          </p>
        </div>
      </div>

      <form class="o-form-grid" @submit.prevent="saveConfig">
        <label class="o-field">
          位置过期停单分钟数（1 - 1440）
          <input
            v-model.number="cfgForm.stale_minutes"
            type="number"
            min="1"
            max="1440"
            class="o-input"
            :disabled="!session.hasOpsPerm('manage_config') || saving"
            required
          />
          <small>摊主超过该时间未重新确认现场位置，系统自动转为过期停单。</small>
        </label>

        <label class="o-field">
          待接单超时释放分钟数（1 - 60）
          <input
            v-model.number="cfgForm.pending_minutes"
            type="number"
            min="1"
            max="60"
            class="o-input"
            :disabled="!session.hasOpsPerm('manage_config') || saving"
            required
          />
          <small>超过该时长商家未接单，订单可由定时任务自动过期并归还库存。</small>
        </label>

        <div class="span-2 o-actions-row">
          <p v-if="configError" class="o-alert" role="alert" style="width: 100%">
            {{ configError }}
          </p>
          <button
            v-if="session.hasOpsPerm('manage_config')"
            type="submit"
            class="o-btn o-btn-primary"
            :disabled="saving"
          >
            {{ saving ? "正在保存…" : "保存全局配置" }}
          </button>
          <span v-else class="o-badge">当前账号为只读查看权限</span>
        </div>
      </form>
    </section>
  </div>
</template>
