<script setup lang="ts">
import { computed } from "vue";
import { useSession } from "../stores/session";

const session = useSession();
const backTarget = computed(() => {
  if (!session.consumerPreview) {
    if (session.isAdmin) return { to: "/ops", label: "返回平台管理台" };
    if (session.isMerchant) return { to: "/merchant", label: "返回商家工作台" };
  }
  return { to: "/", label: "回到首页" };
});
</script>
<template>
  <section class="page empty-state">
    <p class="eyebrow">404 · 迷路了</p>
    <h1>这个转角还没有摊位</h1>
    <p>
      {{
        !session.consumerPreview && (session.isAdmin || session.isMerchant)
          ? "当前访问的地址不存在，请返回你的工作台继续处理。"
          : "页面可能已移动，回首页继续找找好味道。"
      }}
    </p>
    <RouterLink class="btn btn-primary" :to="backTarget.to">{{
      backTarget.label
    }}</RouterLink>
  </section>
</template>
