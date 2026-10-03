import { defineStore } from "pinia";
import { ref, watch } from "vue";
import { readStorage, writeStorage } from "../lib/storage";
export const useDiscovery = defineStore("discovery", () => {
  const savedArea = Number(readStorage("yanhuo-area") || 0);
  const area = ref(
      Number.isSafeInteger(savedArea) && savedArea >= 0 ? savedArea : 0,
    ),
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
    area.value = Number.isSafeInteger(id) && id >= 0 ? id : 0;
    writeStorage("yanhuo-area", String(area.value));
  }
  return { area, q, category, status, sort, position, setArea };
});
