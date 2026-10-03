import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import { useSession } from "./stores/session";
import { notify } from "./lib/notify";
import "./style.css";
createApp(App).use(createPinia()).use(router).mount("#app");
window.addEventListener("session-expired", (event) => {
  const session = useSession();
  if (!session.expire((event as CustomEvent).detail?.epoch)) return;
  if (router.currentRoute.value.path !== "/login") {
    notify("登录已过期，请重新登录。", "info");
    router.push({
      path: "/login",
      query: { returnTo: router.currentRoute.value.fullPath },
    });
  }
});
