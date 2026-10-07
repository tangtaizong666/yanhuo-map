import { createRouter, createWebHistory } from "vue-router";
import { useSession } from "./stores/session";
import { isMerchantPath } from "./lib/identity";
import { hasPendingBrowseReturn, installBrowseReturn } from "./lib/browseReturn";
const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(to, from, saved) {
    if (hasPendingBrowseReturn()) return false;
    if (saved) return saved;
    if (to.path === from.path) return;
    return { top: 0 };
  },
  routes: [
    {
      path: "/",
      component: () => import("./views/HomeView.vue"),
      meta: { title: "附近好味" },
    },
    {
      path: "/search",
      component: () => import("./views/HomeView.vue"),
      meta: { title: "搜索" },
    },
    {
      path: "/map",
      component: () => import("./views/MapView.vue"),
      meta: { title: "地图找摊" },
    },
    {
      path: "/stalls/:id",
      component: () => import("./views/StallView.vue"),
      meta: { title: "摊位详情" },
    },
    {
      path: "/stalls/:id/products/:productId",
      component: () => import("./views/ProductView.vue"),
      meta: { title: "餐点详情" },
    },
    {
      path: "/login",
      component: () => import("./views/AuthView.vue"),
      meta: { title: "登录" },
    },
    {
      path: "/cart",
      component: () => import("./views/CartView.vue"),
      meta: { title: "我的餐袋" },
    },
    {
      path: "/recover",
      component: () => import("./views/AccountRecoveryView.vue"),
      meta: { title: "找回账号" },
    },
    {
      path: "/account-security",
      component: () => import("./views/AccountRecoveryView.vue"),
      meta: { title: "账号安全" },
    },
    {
      path: "/checkout/:id",
      component: () => import("./views/CheckoutView.vue"),
      meta: { title: "确认订单" },
    },
    {
      path: "/orders",
      component: () => import("./views/OrdersView.vue"),
      meta: { title: "我的订单" },
    },
    {
      path: "/orders/:id",
      component: () => import("./views/OrderDetailView.vue"),
      meta: { title: "订单详情" },
    },
    {
      path: "/me",
      component: () => import("./views/ProfileView.vue"),
      meta: { title: "我的" },
    },
    {
      path: "/merchant/:section?",
      component: () => import("./views/MerchantView.vue"),
      meta: { title: "商家工作台" },
    },
    {
      path: "/:pathMatch(.*)*",
      component: () => import("./views/NotFoundView.vue"),
      meta: { title: "页面未找到" },
    },
  ],
});
router.beforeEach(async (to) => {
  const session = useSession();
  try {
    if (!session.loaded) await session.load();
  } catch {
    // The app renders a retryable connection error when configuration is unavailable.
    return true;
  }
  if (isMerchantPath(to.path)) session.setConsumerPreview(false);
  if (to.path === "/" && session.isMerchant && !session.consumerPreview)
    return "/merchant";
  return true;
});
router.afterEach((to) => {
  document.title = `${to.meta.title || "校园好味"} · 烟火地图`;
});
installBrowseReturn(router);
export default router;
