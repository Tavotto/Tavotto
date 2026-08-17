/**
 * 滚轮增量归一化：把 `WheelEvent` 的三种 `deltaMode` 统一折算成像素。
 *
 * 规范只保证 deltaX/Y 的**单位**由 deltaMode 给出，不保证是像素：
 * 0=像素、1=行、2=页。Chrome/Safari（含触控板两指滚动与捏合）恒发 0，
 * 一格约 100–120；Firefox 桌面版默认发 1，一格约 3（行）。把行数当像素用，
 * 缩放/平移就慢几十倍——这正是审计里「Firefox 下滚轮几乎失灵」那条
 * （docs/audit/2026-08-17-ux-audit.md，medium 组）。
 *
 * 「一行 = 多少像素」业界没有标准值，实测取值分布在 25–40：
 * d3-zoom 折算 25（line 系数 0.05 ÷ pixel 系数 0.002）、pdf.js 30
 * （MOUSE_PIXELS_PER_LINE）、normalize-wheel（Facebook fixed-data-table 出身，
 * react-virtualized 等一路沿用）40。另一派直接取 16，理由是 CSS 默认字号
 * 1rem=16px、「一行」就是一行正文的高度——但滚轮一格的滚动距离本来就
 * **大于**一行正文（Firefox 一格发 3 行，实际滚动距离与 Chrome 的 100px 同量级），
 * 16 是在解释排版而不是在解释滚轮。
 *
 * 这里取 40：Firefox 一格 3 行 → 120px，与 Chrome 一格 100–120px 同量级，
 * 缩放公式代进去两边都是一格约 1.25–1.30×；取 16 的话一格只有 48px、
 * 约 1.11×，仍然比 Chrome 慢一半以上。
 *
 * **未在真机 Firefox 上校准**（审计与本次修复都在 macOS 沙箱完成，
 * 没有真实 Firefox/Windows 复现）。数值是可调的：手感不对只改这两个常量，
 * 归一化逻辑与缩放步长公式都不必动。
 */

/** deltaMode=1（行）→ 像素：见文件头注释的取值依据，可调 */
export const WHEEL_LINE_PX = 40

/** deltaMode=2（页）拿不到视口高时的兜底页高（normalize-wheel 的 PAGE_HEIGHT） */
export const WHEEL_PAGE_FALLBACK_PX = 800

export interface WheelDelta {
  deltaX: number
  deltaY: number
}

/** 只取归一化用得上的字段，测试里能直接喂普通对象 */
type WheelLike = Pick<WheelEvent, 'deltaMode' | 'deltaX' | 'deltaY'>

/**
 * @param page 页单位的换算基准（画布视口尺寸，px）；缺省退到窗口尺寸
 */
export function normalizeWheel(e: WheelLike, page?: { w: number; h: number }): WheelDelta {
  if (e.deltaMode === 1) {
    return { deltaX: e.deltaX * WHEEL_LINE_PX, deltaY: e.deltaY * WHEEL_LINE_PX }
  }
  if (e.deltaMode === 2) {
    const win = typeof window === 'undefined' ? null : window
    const w = page?.w || win?.innerWidth || WHEEL_PAGE_FALLBACK_PX
    const h = page?.h || win?.innerHeight || WHEEL_PAGE_FALLBACK_PX
    return { deltaX: e.deltaX * w, deltaY: e.deltaY * h }
  }
  // deltaMode=0（像素）：恒等返回，不做任何乘除——Chrome/Safari 与触控板捏合
  // 走的都是这条路，现有手感必须逐位不变。未知 deltaMode 也落这里（按像素当兜底）
  return { deltaX: e.deltaX, deltaY: e.deltaY }
}
