import { defineStore } from "pinia";
import { ref, watch } from "vue";
export const useDiscovery = defineStore("discovery", () => {
  const area = ref(Number(localStorage.getItem("yanhuo-area") || 0)),
    q = ref(""),
    category = ref(""),
    status = ref("open"),
    sort = ref("freshness"),
    position = ref<{ lat: number; lng: number } | null>(null);
  watch(position, (point) => {
    if (point && (sort.value === "freshness" || sort.value === "recommended"))
      sort.value = "distance";
    if (!point && sort.value === "distance") sort.value = "freshness";
  });
  function setArea(id: number) {
    area.value = id;
    localStorage.setItem("yanhuo-area", String(id));
  }
  return { area, q, category, status, sort, position, setArea };
});
