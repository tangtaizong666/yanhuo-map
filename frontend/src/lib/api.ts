export class ApiError extends Error {
  status: number;
  data: any;
  code?: string;
  constructor(message: string, status: number, data: any) {
    super(message);
    this.status = status;
    this.data = data;
    this.code = data?.code;
  }
}
const REQUEST_TIMEOUT_MS = 20_000;
let csrfPromise: Promise<void> | null = null;
const csrf = () =>
  decodeURIComponent(
    document.cookie
      .split("; ")
      .find((x) => x.startsWith("csrftoken="))
      ?.split("=")
      .slice(1)
      .join("=") || "",
  );

// A cancelled caller must stop waiting without cancelling another caller's CSRF check.
function waitFor<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    if (signal.aborted) return abort();
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject).finally(() => {
      signal.removeEventListener("abort", abort);
    });
  });
}

function ensureCsrf(): Promise<void> {
  if (csrfPromise) return csrfPromise;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  csrfPromise = (async () => {
    try {
      const response = await fetch("/api/v1/auth/csrf", {
        credentials: "include",
        signal: controller.signal,
      });
      if (!response.ok || !csrf())
        throw new ApiError(
          "安全校验暂时失败，请刷新页面后重试。",
          response.status,
          {
            code: "csrf_unavailable",
            submitted: false,
          },
        );
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        controller.signal.aborted
          ? "安全校验等待超时，请检查网络后重试。"
          : "暂时连接不上，请检查网络后重试。",
        0,
        {
          code: controller.signal.aborted ? "request_timeout" : "network_error",
          submitted: false,
        },
      );
    } finally {
      clearTimeout(timer);
      csrfPromise = null;
    }
  })();
  return csrfPromise;
}

export async function api<T = any>(
  path: string,
  options: Omit<RequestInit, "body"> & { body?: any } = {},
): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const mutating = !["GET", "HEAD", "OPTIONS"].includes(method);
  const controller = new AbortController();
  const abort = () => controller.abort();
  let timedOut = false;
  let submitted = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REQUEST_TIMEOUT_MS);
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  try {
    controller.signal.throwIfAborted();
    if (mutating && !csrf()) await waitFor(ensureCsrf(), controller.signal);
    controller.signal.throwIfAborted();
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    const multipart = options.body instanceof FormData;
    if (multipart) headers.delete("Content-Type");
    else if (options.body !== undefined)
      headers.set("Content-Type", "application/json");
    if (mutating) headers.set("X-CSRFToken", csrf());
    submitted = true;
    const response = await fetch("/api/v1" + path, {
      ...options,
      method,
      headers,
      credentials: "include",
      signal: controller.signal,
      body:
        options.body === undefined
          ? undefined
          : multipart
            ? options.body
            : JSON.stringify(options.body),
    });
    if (response.status === 204 || (method === "HEAD" && response.ok))
      return null as T;
    let data: any;
    try {
      data = await response.json();
    } catch {
      controller.signal.throwIfAborted();
      throw new ApiError(
        "未能读取服务器响应，请重新确认当前状态。",
        response.status,
        {
          code: "invalid_response",
          submitted,
        },
      );
    }
    if (!response.ok) {
      if (data?.code === "not_authenticated")
        window.dispatchEvent(new Event("session-expired"));
      throw new ApiError(
        data?.detail ||
          (response.status === 403
            ? "当前账号没有权限执行此操作。"
            : "请求失败，请稍后再试。"),
        response.status,
        data,
      );
    }
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      timedOut
        ? mutating && submitted
          ? "请求等待超时，操作结果尚未确认，请先查看最新状态。"
          : "请求等待超时，请检查网络后重试。"
        : controller.signal.aborted
          ? "已停止等待响应，请重新确认当前状态。"
          : "暂时连接不上，请检查网络后重试。",
      0,
      {
        code: timedOut
          ? "request_timeout"
          : controller.signal.aborted
            ? "request_aborted"
            : "network_error",
        submitted,
      },
    );
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
}
export function money(cents: number = 0) {
  return (cents / 100)
    .toFixed(2)
    .replace(/\.00$/, "")
    .replace(/(\.\d)0$/, "$1");
}
export function formatTime(value: string | null | undefined) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
}
export function formatClock(value: string) {
  if (/^\d{2}:\d{2}/.test(value)) return value.slice(0, 5);
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "待确认"
    : d.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
}
export function statusText(status: string, fulfillmentType?: string) {
  if (status === "ready" && fulfillmentType === "delivery") return "待配送";
  return (
    (
      {
        open: "出摊中",
        paused: "暂歇",
        closed: "已收摊",
        stale: "状态待确认",
        pending: "待接单",
        pending_payment: "待付款",
        delivering: "配送中",
        arrived: "已到交接点",
        preparing: "制作中",
        ready: "待取餐",
        completed: "已完成",
        cancelled: "已取消",
        rejected: "商家已拒单",
        unpaid: "未付款",
        paid: "已收款",
        refunding: "退款处理中",
        refunded: "已退款",
      } as Record<string, string>
    )[status] || status
  );
}
export function confirmedText(value: string | null) {
  if (!value) return "尚未确认位置";
  const m = Math.max(
    0,
    Math.floor((Date.now() - new Date(value).getTime()) / 60000),
  );
  return m < 1
    ? "刚刚确认位置"
    : m < 60
      ? `${m} 分钟前确认位置`
      : `${Math.floor(m / 60)} 小时前确认位置`;
}
export function routeUrl(lat: number, lng: number, name: string) {
  return `https://uri.amap.com/navigation?to=${lng},${lat},${encodeURIComponent(name)}&mode=walk&coordinate=gaode&callnative=0`;
}
