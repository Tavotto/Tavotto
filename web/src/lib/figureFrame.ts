/**
 * 图幅（ADR 0098）：脚本按 `savefig(bbox_inches=...)` 存盘的图，对外的「这张图」是 savefig 裁出来的
 * 那个框，不是 figsize。引擎按它渲染、导出、报 manifest；前端的几何一律跟着 manifest 走，不用知道。
 *
 * 这里只管升级前就在版面上的面板（用户 2026-09-26 选 C）：
 *
 * - **迁移**：面板没有 `figureFrame` 记号 = 升级前放上去的。它此刻的样子若来自引擎（有图内修改，
 *   或是 runtime 面板），就补一条 `figure.frame = "figsize"`，引擎照旧按 figsize 出图——版面与导出
 *   与升级前逐字节相同。没有图内修改的 PDF 面板画的是磁盘原件本身（脚本自己存的那份，本来就是
 *   tight 的），不补：补了反而会把它换成引擎按 figsize 画的那张。迁移之后一律打上记号，之后
 *   新加的面板生来带记号、从不补。
 * - **切换**：去掉那条 override，同时把面板在版面上的落位与图内的分数类 override 换算到新图幅里，
 *   **内容在页面上一个点都不动**，只有外框变（原来被切掉的露出来、多余的空白按原件裁掉）。一次
 *   提交，⌘Z 整体撤回。
 */
import type { Manifest } from '@/lib/api'
import type { CropRect, PanelObject, PanelOverride } from '@/types/document'
import { panelFullSize, panelRotation, rotateVec, rotationSwaps } from '@/types/document'
import { hasLegacyFrame, isLegacyFrameOverride } from '@/lib/figureFrameMigration'

export {
  FIGURE_FRAME_VERSION,
  LEGACY_FRAME_OVERRIDE,
  hasLegacyFrame,
  isLegacyFrameOverride,
  migrateFigureFrames,
} from '@/lib/figureFrameMigration'

/** manifest 的 `frame` 字段（引擎 `pathgeom.frame_report`）：只有脚本存盘时裁过的图才有 */
export interface ManifestFrame {
  source: 'savefig'
  /** 此刻渲染用的是不是它（带着 figsize 那条 override 时为 false） */
  active: boolean
  /** figsize（mm） */
  figsize_mm: [number, number]
  /** 脚本存盘的图幅在 figsize 里的位置：[左, 上, 宽, 高]（mm，相对 figsize 左上角，可以为负） */
  savefig_mm: [number, number, number, number]
}

/** 这张面板可以切到脚本存盘的图幅：带着 figsize 那条 override，且引擎说它确实有另一个图幅 */
export function frameSwitchAvailable(panel: PanelObject, manifest: Manifest | null): ManifestFrame | null {
  const frame = manifest?.frame
  if (!frame || frame.source !== 'savefig' || frame.active || !hasLegacyFrame(panel)) return null
  const [gw, gh] = frame.figsize_mm
  const [, , fw, fh] = frame.savefig_mm
  if (!(gw > 0 && gh > 0 && fw > 0 && fh > 0)) return null
  return frame
}

type Frac = { x: number; y: number; w: number; h: number }

/**
 * 把一条 override 的值从 figsize 的分数换到脚本图幅的分数（图内的元素留在原处）。
 * 只有值以图幅为基准的那几条要换（引擎 `overrides._FRAME_RELATIVE`）；其余原样。
 */
function rebaseOverride(o: PanelOverride, f: Frac, frame: ManifestFrame): PanelOverride {
  const v = o.value
  const nums = (n: number) => Array.isArray(v) && v.length === n && v.every((x) => typeof x === 'number')
  // top-origin 分数的一点：x' = (x − 左) / 宽，y' = (y − 上) / 高
  const pt = (x: number, y: number) => [(x - f.x) / f.w, (y - f.y) / f.h]
  if ((o.prop === 'pos_frac' || o.prop === 'loc_frac') && nums(2)) {
    const [x, y] = v as number[]
    return { ...o, value: pt(x, y) }
  }
  if (o.prop === 'endpoints_frac' && nums(4)) {
    const [x0, y0, x1, y1] = v as number[]
    return { ...o, value: [...pt(x0, y0), ...pt(x1, y1)] }
  }
  if (o.prop === 'position' && nums(4)) {
    // axes.position 是 bottom-origin：图幅底边在 figsize 里的分数 = 1 − 上 − 高
    const [l, b, w, h] = v as number[]
    const bottom = 1 - f.y - f.h
    return { ...o, value: [(l - f.x) / f.w, (b - bottom) / f.h, w / f.w, h / f.h] }
  }
  if (o.gid === 'figure' && o.prop === 'size_mm' && nums(2)) {
    // 那条 override 写的是 figsize；换到图幅里就是这个 figsize 对应的图幅尺寸
    return { ...o, value: [frame.savefig_mm[2], frame.savefig_mm[3]] }
  }
  return o
}

export interface FrameSwitchPatch {
  overrides: PanelOverride[]
  x: number
  y: number
  w: number
  h: number
  nativeW: number
  nativeH: number
  crop: CropRect | undefined
}

/**
 * 「改用脚本保存时的图幅」：内容在页面上不动、外框变成新图幅（裁过的面板只在原来的可见范围与
 * 新图幅的交集里）。`frame` 取自这张面板**精确**的 manifest（ADR 0017：几何写操作只认权威）。
 */
export function frameSwitchPatch(panel: PanelObject, frame: ManifestFrame): FrameSwitchPatch {
  const [gw, gh] = frame.figsize_mm
  const [sx, sy, fw, fh] = frame.savefig_mm
  // 新图幅在 figsize 里的分数（top-origin）
  const f: Frac = { x: sx / gw, y: sy / gh, w: fw / gw, h: fh / gh }
  const oldVis: Frac = panel.crop ?? { x: 0, y: 0, w: 1, h: 1 }
  let vis: Frac = f
  if (panel.crop) {
    const x0 = Math.max(oldVis.x, f.x)
    const y0 = Math.max(oldVis.y, f.y)
    const x1 = Math.min(oldVis.x + oldVis.w, f.x + f.w)
    const y1 = Math.min(oldVis.y + oldVis.h, f.y + f.h)
    vis = x1 > x0 && y1 > y0 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : oldVis
  }
  // 内容空间（未旋转、未翻转）里，整张 figsize 在页面上多大
  const full = panelFullSize(panel)
  let dx = (vis.x + vis.w / 2 - (oldVis.x + oldVis.w / 2)) * full.w
  let dy = (vis.y + vis.h / 2 - (oldVis.y + oldVis.h / 2)) * full.h
  if (panel.flipH) dx = -dx
  if (panel.flipV) dy = -dy
  const r = panelRotation(panel)
  const [px, py] = rotateVec(dx, dy, r)
  const cw = vis.w * full.w
  const ch = vis.h * full.h
  const [w, h] = rotationSwaps(r) ? [ch, cw] : [cw, ch]
  const cx = panel.x + panel.w / 2 + px
  const cy = panel.y + panel.h / 2 + py
  const crop = panel.crop
    ? { x: (vis.x - f.x) / f.w, y: (vis.y - f.y) / f.h, w: vis.w / f.w, h: vis.h / f.h }
    : undefined
  return {
    overrides: panel.overrides.filter((o) => !isLegacyFrameOverride(o)).map((o) => rebaseOverride(o, f, frame)),
    x: cx - w / 2,
    y: cy - h / 2,
    w,
    h,
    nativeW: fw,
    nativeH: fh,
    crop,
  }
}
