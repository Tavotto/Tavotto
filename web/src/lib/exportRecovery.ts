/**
 * 导出失败之后「点一下就能接着做」的出口：哪几种失败有出口、出口是什么。
 * 只做判断与算数，不碰界面、不发请求——点下去之后重新导出走对话框里同一条 `start()`，
 * 载荷仍只由 `buildExportRequest()` 构造。
 */

/** 同目录再来一次必败的两种：换个位置才是出路 */
const LOCATION_CODES = new Set(['export_dir_unwritable', 'tmp_dir_failed'])

export const isLocationFailure = (code: string | null | undefined): boolean =>
  !!code && LOCATION_CODES.has(code)

export const RASTER_TOO_LARGE = 'export_raster_too_large'

const posInt = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.floor(v) : null

/**
 * 位图超过像素上限（后端带 width / height / max_pixels，是**当前 dpi 下**的宽高）时，
 * 候选档位里「不超上限的最大一档」；算不出（参数缺、没有更低的合法档）= null。
 * 像素数随 dpi 平方缩放，宽高各自向上取整，宁可保守也不让再失败一次。
 */
export function largestDpiWithinLimit(
  params: Record<string, unknown> | null | undefined,
  currentDpi: number,
  candidates: readonly number[],
): number | null {
  const w = posInt(params?.width)
  const h = posInt(params?.height)
  const max = posInt(params?.max_pixels)
  if (w === null || h === null || max === null) return null
  if (!Number.isFinite(currentDpi) || currentDpi <= 0) return null
  const fits = (d: number) => Math.ceil((w * d) / currentDpi) * Math.ceil((h * d) / currentDpi) <= max
  const ok = candidates.filter((d) => d > 0 && d < currentDpi && fits(d))
  return ok.length ? Math.max(...ok) : null
}
