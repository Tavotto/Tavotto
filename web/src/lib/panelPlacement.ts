import type { PageSetup } from '@/types/document'
import { clamp } from './units'

/**
 * 新加的图最多比页面可用区域大多少倍（2026-09-28 用户拍板 1.15）。这是**软上限**：
 * 放上去的尺寸随原图增大平滑逼近它，而不是超过某个阈值就一刀缩到页面大小——
 * 阈值方案（先试的是「130% 以内不缩」）在阈值处必然跳变：129% 的图原样放、131% 的
 * 图缩成 100%，大一点的原图放上去反而小一截。
 */
export const OVERSIZE_CAP = 1.15

/**
 * 软上限曲线：`r` = 原图相对可用区域的倍数（取宽高里更吃紧的那一边），返回放上去的倍数。
 * `r ≤ 1` 原样；之后 `1 + K·tanh((r − 1) / K)`，K = CAP − 1。在 r = 1 处值与斜率都接上
 * （稍大的图几乎不动：105% → 99.8% 缩放、110% → 98.9%），单调递增，r → ∞ 时逼近 CAP。
 */
export function softCap(r: number, cap = OVERSIZE_CAP): number {
  if (r <= 1) return r
  const k = cap - 1
  return 1 + k * Math.tanh((r - 1) / k)
}

/**
 * 新加进画布的图摆多大、摆在哪（纯函数，`addPanel` / `addRuntimePanel` 共用）。
 *
 * **只缩不放、没有跳变**：原图相对页面可用区域（扣掉安全边距）的倍数经 `softCap`
 * 压到最多 `OVERSIZE_CAP` 倍，等比缩放；比页面小的图保持原始尺寸（100%）。改造前一律
 * 按原始尺寸放——科研脚本出的图常常比单栏页面宽得多，加进来就把整张页面盖住，用户
 * 看不到画布在哪（2026-09-28 用户反馈）。伸出页面的那一截由舞台上的页面外遮罩
 * （`canvas/PageOutsideMask`）画淡，导出时本来就会被页框裁掉。缩了之后等效字号变小，
 * 由问题面板的字号检查照常报出来，不在这里另判一遍。
 *
 * 位置：给了落点（拖放）就以落点为中心，否则页面居中。每一维分开处理：装得下的
 * 整条钳进页面；比页面大的那一维在页面上居中，两边均匀伸出，不贴着左上角。
 */
export function placePanelInPage(
  nativeW: number,
  nativeH: number,
  page: Pick<PageSetup, 'w' | 'h' | 'margin'>,
  at?: { x: number; y: number },
): { x: number; y: number; w: number; h: number } {
  const inset = page.margin && page.margin > 0 ? page.margin : 0
  // 边距大到挤没了可用区域（病态页面设置）：退回整页，不能缩成 0
  const boxW = page.w - 2 * inset > 0 ? page.w - 2 * inset : page.w
  const boxH = page.h - 2 * inset > 0 ? page.h - 2 * inset : page.h
  // 更吃紧的那一边决定缩放；原图尺寸不合法就原样放（不除以 0）
  const r = nativeW > 0 && nativeH > 0 ? Math.max(nativeW / boxW, nativeH / boxH) : 0
  const k = r > 1 ? softCap(r) / r : 1
  const w = nativeW * k
  const h = nativeH * k
  const place = (size: number, pageSize: number, center: number) =>
    size <= pageSize ? clamp(center - size / 2, 0, pageSize - size) : (pageSize - size) / 2
  return {
    x: place(w, page.w, at ? at.x : page.w / 2),
    y: place(h, page.h, at ? at.y : page.h / 2),
    w,
    h,
  }
}
