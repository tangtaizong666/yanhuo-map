<script setup lang="ts">
import {
  ArrowUpRight,
  BookOpenCheck,
  ChevronRight,
  History,
  MessageSquare,
  Settings2,
  ShieldCheck,
  Wallet,
} from "lucide-vue-next";
import { computed } from "vue";
import { useSession } from "../../stores/session";
import StallShare from "../StallShare.vue";

const props = defineProps<{
  stall: any;
  sellable: boolean;
  discoveryOnly?: boolean;
}>();
const emit = defineEmits<{ guide: [] }>();
const session = useSession();

const payoutBadge = computed(() => {
  if (props.discoveryOnly) return "";
  return props.stall?.payout_account?.registered ? "已登记收款号" : "去登记收款号";
});

const preparationSteps = computed(() =>
  (props.stall.activation?.steps || []).filter(
    (step: any) => !props.discoveryOnly || ["visibility", "location"].includes(step.key),
  ),
);
</script>

<template>
  <div class="m-more-page">
    <div class="m-page-brief">
      <p>
        <strong>功能总览：</strong>所有辅助工具已按<strong>「收款与账本、日常摆摊设置、评价与账号」</strong>分好类，点进对应项目即可操作。
      </p>
      <details class="m-detail-toggle">
        <summary>不确定去哪里找设置？点此查看分类说明</summary>
        <ul>
          <li><strong>查今天赚了多少、改收款微信号/银行卡：</strong>进下方第一组「收款与经营数据」。</li>
          <li><strong>改出摊地址、调付款自动接单、开外卖配送、换店招封面：</strong>进第二组「店铺与营业」。</li>
          <li><strong>开新单提示音：</strong>直接滑到本页底部的「新订单提醒」打开声音即可。</li>
        </ul>
      </details>
    </div>

    <section class="m-cat-group" aria-label="收款与账本">
      <div class="m-cat-head">
        <h2>收款与账本</h2>
        <p>看实赚到手多少钱、登记收款账户、查找过往订单</p>
      </div>
      <div class="m-more-menu">
        <RouterLink to="/merchant/analytics" class="m-more-payout-link">
          <span class="m-more-icon m-more-icon-payout"><Wallet :size="22" /></span>
          <span>
            <strong>
              收款与经营数据
              <em v-if="payoutBadge" :class="{ ready: stall.payout_account?.registered }">{{
                payoutBadge
              }}</em>
            </strong>
            <small>{{
              discoveryOnly
                ? "查看摊位浏览来源、关注人数与经营统计"
                : "看到手净收入、登记收款微信/银行卡、导出账单明细"
            }}</small>
          </span>
          <ChevronRight :size="18" />
        </RouterLink>
        <RouterLink to="/merchant/orders?filter=all">
          <span class="m-more-icon"><History :size="22" /></span>
          <span>
            <strong>{{ discoveryOnly ? "已有订单" : "历史订单" }}</strong>
            <small>{{
              discoveryOnly
                ? "处理原有订单、取消与退款"
                : "按订单号或状态查找已完成、已取消与退款订单"
            }}</small>
          </span>
          <ChevronRight :size="18" />
        </RouterLink>
      </div>
    </section>

    <section class="m-cat-group" aria-label="日常摆摊与店铺设置">
      <div class="m-cat-head">
        <h2>日常摆摊与店铺设置</h2>
        <p>改出摊位置、开关自动接单与配送、看一分钟上手指南</p>
      </div>
      <div class="m-more-menu">
        <RouterLink to="/merchant/store">
          <span class="m-more-icon"><Settings2 :size="22" /></span>
          <span>
            <strong>店铺与营业</strong>
            <small>{{
              discoveryOnly
                ? "确认出摊位置、收摊时间、修改店名封面与电话"
                : "确认出摊位置、管理收款与配送开关、修改店铺门面"
            }}</small>
          </span>
          <ChevronRight :size="18" />
        </RouterLink>
        <button type="button" class="m-more-guide-btn" @click="emit('guide')">
          <span class="m-more-icon m-more-icon-guide"><BookOpenCheck :size="22" /></span>
          <span>
            <strong>摆摊使用说明</strong>
            <small>{{
              discoveryOnly
                ? "确认位置、修改今日供应与收摊流程一分钟上手"
                : "自动锁价收款、自动接单、4 位取餐码交餐一分钟上手"
            }}</small>
          </span>
          <ChevronRight :size="18" />
        </button>
      </div>

      <details
        id="activation"
        class="m-panel m-more-details"
        :open="!stall.is_visible || !stall.activation?.has_location || (!discoveryOnly && !sellable)"
      >
        <summary>
          {{ discoveryOnly ? "找摊信息" : "开摊准备" }}
          <span>{{
            discoveryOnly
              ? stall.is_visible
                ? "已公开展示 · 点击查看详情"
                : "待核验展示"
              : stall.can_order
                ? "已可正常接单 · 点击核对"
                : "查看还缺哪一步"
          }}</span>
        </summary>
        <section aria-label="开摊准备">
          <p>
            {{
              discoveryOnly
                ? "当前提供找摊信息服务。公开位置与菜品展示不代表已开放线上交易；已有订单仍可处理。"
                : "下方打勾项表示已就绪；标明“由运营核验”的项目会由校园运营团队协助办理。"
            }}
          </p>
          <ol class="activation-checklist">
            <li v-for="step in preparationSteps" :key="step.key">
              <strong>{{ step.status === "done" ? "✓" : "○" }} {{ step.label }}</strong>
              <span>
                {{ step.reason }}
                <small> · {{ step.owner === "merchant" ? "由商家完善" : "由运营核验" }}</small>
              </span>
            </li>
          </ol>
          <div class="m-preparation-links">
            <RouterLink to="/merchant/products">管理菜品</RouterLink>
            <RouterLink to="/merchant/store#location">设置取餐位置</RouterLink>
          </div>
        </section>
      </details>
    </section>

    <section class="m-cat-group" aria-label="评价、分享与账号">
      <div class="m-cat-head">
        <h2>评价、分享与账号</h2>
        <p>回复同学评价、保存餐车二维码、管理登录密码</p>
      </div>
      <div class="m-more-menu">
        <RouterLink to="/merchant/reviews">
          <span class="m-more-icon"><MessageSquare :size="22" /></span>
          <span>
            <strong>顾客评价</strong>
            <small>查看同学真实评价并公开回复</small>
          </span>
          <ChevronRight :size="18" />
        </RouterLink>
        <RouterLink to="/account-security?returnTo=/merchant/more">
          <span class="m-more-icon"><ShieldCheck :size="22" /></span>
          <span>
            <strong>账号安全</strong>
            <small>修改登录密码、保存一次性找回恢复码</small>
          </span>
          <ChevronRight :size="18" />
        </RouterLink>
        <RouterLink to="/" @click="session.setConsumerPreview(true)">
          <span class="m-more-icon"><ArrowUpRight :size="22" /></span>
          <span>
            <strong>预览学生端</strong>
            <small>切换到顾客视角看看学生端首页与菜单展示</small>
          </span>
          <ChevronRight :size="18" />
        </RouterLink>
      </div>

      <details v-if="stall.is_visible" class="m-panel m-more-details">
        <summary>
          分享摊位二维码<span>贴在餐车上，同学扫码直达你的菜单</span>
        </summary>
        <StallShare :stall="stall" compact />
      </details>
      <p v-else class="m-muted">
        公开展示尚未开通，暂不能分享学生端地址。工作台仍可继续完善资料。
      </p>
    </section>
  </div>
</template>

<style scoped>
.m-more-payout-link {
  background: linear-gradient(135deg, #fff8ec, #fffdf9);
  margin: 0 -16px;
  padding: 14px 16px !important;
  border-radius: 16px 16px 0 0;
}
.m-more-icon-payout {
  background: #fde9d3 !important;
  color: #b55523 !important;
}
.m-more-payout-link strong {
  display: flex !important;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.m-more-payout-link em {
  font-style: normal;
  font-size: 11.5px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: #fde6cc;
  color: #9e4314;
}
.m-more-payout-link em.ready {
  background: #e8f2e6;
  color: #48734b;
}
.m-more-guide-btn {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 76px;
  padding: 12px 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.m-more-guide-btn > svg:last-child {
  margin-left: auto;
  color: #a39384;
}
.m-more-icon-guide {
  background: #f8efe2 !important;
  color: #9f5425 !important;
}
</style>
