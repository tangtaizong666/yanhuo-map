import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import { useSession } from "./stores/session";
import { isOpsPath } from "./lib/identity";
import { notify } from "./lib/notify";
import "./style.css";
createApp(App).use(createPinia()).use(router).mount("#app");
window.addEventListener("session-expired", (event) => {
  const session = useSession();
  if (!session.expire((event as CustomEvent).detail?.epoch)) return;
  const current = router.currentRoute.value;
  if (current.path !== "/login" && current.path !== "/ops/login") {
    notify("登录已过期，请重新登录。", "info");
    router.push({
      path: isOpsPath(current.path) ? "/ops/login" : "/login",
      query:
        isOpsPath(current.path) && current.fullPath === "/ops"
          ? {}
          : { returnTo: current.fullPath },
    });
  }
});
