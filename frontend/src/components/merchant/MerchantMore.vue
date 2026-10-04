<script setup lang="ts">
import { Settings2, ChartNoAxesCombined, MessageSquare, History, ArrowUpRight, ChevronRight, ShieldCheck, LogOut } from "lucide-vue-next";
import { useSession } from "../../stores/session";
import StallShare from "../StallShare.vue";

defineProps<{ stall: any; sellable: boolean }>();
const emit = defineEmits<{ logout: [] }>();
const session = useSession();
const links = [
  { to: "/merchant/store", title: "店铺与营业", note: "开摊、位置、支付和配送", icon: Settings2 },
  { to: "/merchant/orders?filter=all", title: "历史订单", note: "查找已完成、已取消的订单", icon: History },
  { to: "/merchant/analytics", title: "经营数据", note: "查看收款、销量和退款", icon: ChartNoAxesCombined },
  { to: "/merchant/reviews", title: "顾客评价", note: "查看与回复评价", icon: MessageSquare },
  { to: "/account-security?returnTo=/merchant/more", title: "账号安全", note: "修改密码、设置恢复码", icon: ShieldCheck },
];
</script>

<template>
  <div class="m-more-page">
    <div class="m-more-menu">
      <RouterLink v-for="link in links" :key="link.to" :to="link.to">
        <span class="m-more-icon"><component :is="link.icon" :size="22" /></span>
        <span><strong>{{ link.title }}</strong><small>{{ link.note }}</small></span>
        <ChevronRight :size="18" />
      </RouterLink>
      <RouterLink to="/" @click="session.setConsumerPreview(true)"><span class="m-more-icon"><ArrowUpRight :size="22" /></span><span><strong>预览学生端</strong><small>看看学生看到的页面</small></span><ChevronRight :size="18" /></RouterLink>
    </div>
    <details id="activation" class="m-panel m-more-details" :open="!stall.transaction_enabled || !stall.is_visible || !stall.activation?.has_location || !sellable">
      <summary>开摊准备<span>{{ stall.can_order ? '已可接单' : '查看准备事项' }}</span></summary>
      <section aria-label="开摊准备">
        <p>按下面的提示准备。需要审核的项目，由运营人员处理。</p>
        <ol class="activation-checklist"><li v-for="step in stall.activation?.steps || []" :key="step.key"><strong>{{ step.status === 'done' ? '✓' : '○' }} {{ step.label }}</strong><span>{{ step.reason }}<small> · {{ step.owner === 'merchant' ? '由商家完善' : '由运营核验' }}</small></span></li></ol>
        <div class="m-preparation-links"><RouterLink to="/merchant/products">管理菜品</RouterLink><RouterLink to="/merchant/store#location">设置取餐位置</RouterLink></div>
      </section>
    </details>
    <details v-if="stall.is_visible" class="m-panel m-more-details"><summary>分享摊位<span>地址与二维码</span></summary><StallShare :stall="stall" compact /></details>
    <p v-else class="m-muted">公开展示尚未开通，暂不能分享学生端地址。工作台仍可继续完善资料。</p>
    <button class="m-more-logout" @click="emit('logout')"><LogOut :size="18" />退出登录</button>
  </div>
</template>
