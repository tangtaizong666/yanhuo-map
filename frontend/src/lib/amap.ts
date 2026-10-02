import type { Config } from "./types";
declare global {
  interface Window {
    AMap: any;
    _AMapSecurityConfig?: { serviceHost: string };
  }
}
let pending: Promise<any> | null = null;
export function loadAMap(config: Config) {
  if (window.AMap) return Promise.resolve(window.AMap);
  if (!config.amap_key)
    return Promise.reject(
      new Error("地图服务暂未开放，你仍可通过列表查看摊位地址。"),
    );
  if (pending) return pending;
  pending = new Promise((resolve, reject) => {
    if (config.amap_proxy)
      window._AMapSecurityConfig = {
        serviceHost: new URL(config.amap_proxy, location.origin).href,
      };
    const script = document.createElement("script");
    const timeout = window.setTimeout(() => {
      script.remove();
      pending = null;
      reject(new Error("地图加载超时，请检查网络或稍后重试。"));
    }, 15000);
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(config.amap_key)}&plugin=AMap.Geolocation,AMap.Scale`;
    script.onload = () => {
      clearTimeout(timeout);
      if (window.AMap) resolve(window.AMap);
      else {
        pending = null;
        reject(new Error("地图服务暂时不可用。"));
      }
    };
    script.onerror = () => {
      clearTimeout(timeout);
      script.remove();
      pending = null;
      reject(new Error("地图加载失败，仍可在列表里寻找好味道。"));
    };
    document.head.appendChild(script);
  });
  return pending;
}
export async function locate(
  config: Config,
): Promise<{ lat: number; lng: number }> {
  const AMap = await loadAMap(config);
  return new Promise((resolve, reject) => {
    const geo = new AMap.Geolocation({
      enableHighAccuracy: true,
      timeout: 10000,
      convert: true,
    });
    geo.getCurrentPosition((status: string, result: any) => {
      if (status === "complete" && result?.position)
        resolve({ lat: result.position.lat, lng: result.position.lng });
      else
        reject(
          new Error("未能获取位置，可在上方手动选择校园，继续浏览附近摊位。"),
        );
    });
  });
}
