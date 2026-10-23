/**
 * `/api/render` 的取图宽度档位（镜像 `src/tavotto/app.py` 的 `RENDER_BUCKETS`：后端把 `w` 向上取到下一档，
 * 所以这里只是替调用方事先算好「要几档」，档位表本身以后端为准）。导出对话框的预览缩放时按
 * 「显示宽度 × 缩放 × 设备像素比」挑档，放大后换更高分辨率的图，不靠拉伸低档位图。
 */
export const PREVIEW_BUCKETS = [200, 400, 800, 1600, 3200] as const

/** 至少 `px` 个设备像素宽的最小一档；超过最大档就是最大档，不足 1 当作 0 → 最小档 */
export function bucketFor(px: number): number {
  return PREVIEW_BUCKETS.find((b) => b >= px) ?? PREVIEW_BUCKETS[PREVIEW_BUCKETS.length - 1]
}
