const DIRECT_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const MAX_RAW_BYTES = 25 * 1024 * 1024;
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const FAST_PASSTHROUGH_BYTES = 900 * 1024;
const MAX_EDGE_PX = 1600;

function baseFileName(name: string): string {
  const stripped = (name || "dish").replace(/\.[^/.]+$/, "").trim();
  return stripped || "dish";
}

function canvasToJpegBlob(
  canvas: HTMLCanvasElement,
  quality: number,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob && blob.size > 0) resolve(blob);
        else reject(new Error("照片压缩失败，请换一张 JPG 或 PNG 图片重试。"));
      },
      "image/jpeg",
      quality,
    );
  });
}

async function decodeBrowserImage(
  file: File,
): Promise<{
  width: number;
  height: number;
  draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void;
  close: () => void;
}> {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(file);
    return {
      width: bitmap.width,
      height: bitmap.height,
      draw: (ctx, w, h) => ctx.drawImage(bitmap, 0, 0, w, h),
      close: () => bitmap.close(),
    };
  }
  if (typeof Image === "undefined" || typeof URL?.createObjectURL !== "function") {
    throw new Error("当前环境不支持浏览器端图片缩放。");
  }
  const objectUrl = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("无法读取所选照片。"));
      el.src = objectUrl;
    });
    return {
      width: img.naturalWidth || img.width,
      height: img.naturalHeight || img.height,
      draw: (ctx, w, h) => ctx.drawImage(img, 0, 0, w, h),
      close: () => URL.revokeObjectURL(objectUrl),
    };
  } catch (err) {
    URL.revokeObjectURL(objectUrl);
    throw err;
  }
}

export function scaleDimensions(
  width: number,
  height: number,
  maxEdge = MAX_EDGE_PX,
): { width: number; height: number } {
  if (width <= 0 || height <= 0) return { width: 1, height: 1 };
  const longest = Math.max(width, height);
  if (longest <= maxEdge) return { width, height };
  const ratio = maxEdge / longest;
  return {
    width: Math.max(1, Math.round(width * ratio)),
    height: Math.max(1, Math.round(height * ratio)),
  };
}

/**
 * Compresses large mobile camera photos into a <= 1600px JPEG (< 5 MB) before
 * upload so high-megapixel phone shots succeed quickly even on weak campus Wi-Fi/4G.
 */
export async function prepareDishPhoto(file: File): Promise<File> {
  if (!file || file.size <= 0) {
    throw new Error("请选择有效的商品照片。");
  }
  if (file.size > MAX_RAW_BYTES) {
    throw new Error("照片文件超过 25 MB，请重拍或选择较小图片。");
  }
  const hasImageMime = file.type.startsWith("image/");
  const hasSupportedExt = /\.(?:jpe?g|png|webp)$/i.test(file.name || "");
  if (!hasImageMime && !hasSupportedExt) {
    throw new Error("请选择 JPG、PNG 或 WebP 格式的商品照片。");
  }

  let decoded: Awaited<ReturnType<typeof decodeBrowserImage>> | null = null;
  try {
    decoded = await decodeBrowserImage(file);
  } catch {
    if (DIRECT_TYPES.has(file.type) && file.size <= MAX_UPLOAD_BYTES) {
      return file;
    }
    throw new Error("照片无法读取或格式暂不支持，请使用相机重拍或选择 JPG/PNG 图片。");
  }

  try {
    const { width, height } = decoded;
    if (
      DIRECT_TYPES.has(file.type) &&
      file.size <= FAST_PASSTHROUGH_BYTES &&
      width <= MAX_EDGE_PX &&
      height <= MAX_EDGE_PX
    ) {
      return file;
    }
    if (typeof document === "undefined") {
      if (DIRECT_TYPES.has(file.type) && file.size <= MAX_UPLOAD_BYTES) return file;
      throw new Error("图片超出 5 MB 限制。");
    }
    const target = scaleDimensions(width, height, MAX_EDGE_PX);
    const canvas = document.createElement("canvas");
    canvas.width = target.width;
    canvas.height = target.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      if (DIRECT_TYPES.has(file.type) && file.size <= MAX_UPLOAD_BYTES) return file;
      throw new Error("浏览器暂无法压缩此大图，请裁剪后再试。");
    }
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, target.width, target.height);
    decoded.draw(ctx, target.width, target.height);

    let blob = await canvasToJpegBlob(canvas, 0.85);
    if (blob.size > MAX_UPLOAD_BYTES) {
      blob = await canvasToJpegBlob(canvas, 0.68);
    }
    if (blob.size > MAX_UPLOAD_BYTES) {
      throw new Error("压缩后照片仍超过 5 MB，请重拍或裁剪后再试。");
    }
    return new File([blob], `${baseFileName(file.name)}.jpg`, {
      type: "image/jpeg",
      lastModified: Date.now(),
    });
  } finally {
    decoded.close();
  }
}
