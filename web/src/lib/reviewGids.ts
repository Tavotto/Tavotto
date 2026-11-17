import type { ElementGeometry, ManifestElement } from '@/lib/api'
import { exactPanelManifest, structuralPanelManifest, type RenderState } from '@/store/renderStore'
import type { CanvasObject, PanelObject } from '@/types/document'

/** `<对象id>::<manifest gid>` → manifest gid；其余原样 */
export const innerGid = (gid: string): string => (gid.includes('::') ? gid.slice(gid.indexOf('::') + 2) : gid)

/**
 * 审阅意见锚定的元素 `gid` 此刻在哪张图里（ADR 0187 §五）：先认面板自己（排版的「每个面板一个框」，gid = 面板对象 id），
 * 再认面板精确 / 结构 manifest 里的图内元素。找不到 = 这个元素在当前画布上已经没有了（→ 列表标「原位置已变化」）。
 */
export function owningPanel(
  objects: readonly CanvasObject[],
  render: Pick<RenderState, 'byKey' | 'latest'>,
  gid: string,
): PanelObject | null {
  // 排版里面板内元素的 gid = `<对象id>::<manifest gid>`（local-api 补充）；单图 / 面板框 = 原样
  const sep = gid.indexOf('::')
  const objId = sep >= 0 ? gid.slice(0, sep) : null
  const inner = sep >= 0 ? gid.slice(sep + 2) : gid
  for (const o of objects) {
    if (o.type !== 'panel') continue
    if (objId !== null && o.id !== objId) continue
    if (o.id === gid) return o
    const m = exactPanelManifest(render, o) ?? structuralPanelManifest(render, o)
    if (m && (m.elements.some((e) => e.gid === inner) || guideBox(m.elements, inner))) return o
  }
  return null
}

/** 画布上有没有面板的元素表还没到（精确 / 结构 manifest 都没有）：此时「元素不在了」不成立，只是还不知道 */
export function manifestsPending(
  objects: readonly CanvasObject[],
  render: Pick<RenderState, 'byKey' | 'latest'>,
): PanelObject[] {
  return objects.filter(
    (o): o is PanelObject => o.type === 'panel' && !(exactPanelManifest(render, o) ?? structuralPanelManifest(render, o)),
  )
}

/**
 * 外部审阅页把子图的边框线 / 网格线 / 刻度线当独立目标点选（ADR 0187 修订 2026-11-17）：它们不是画布元素，
 * gid 是引擎上传时合成的 `<子图 gid>.spine_left` / `.xgrid` / `.ytickmarks`，数据在子图那一项的
 * `spines` / `grid` / `tickmarks` 里。画布不据此建任何可选目标，只给审阅意见的圆点找落点。
 */
const GUIDE_GID = /^(.+)\.(?:spine_(left|right|top|bottom)|([xy])(grid|tickmarks))$/

/** 与引擎 `reviewshare._GUIDE_MIN_THICK` 同值：水平 / 垂直线的框垫出的最小厚度（figure 分数） */
const GUIDE_MIN_THICK = 0.002

export interface GuideRef {
  /** 所属子图的 manifest gid */
  base: string
  kind: 'spine' | 'grid' | 'tickmarks'
  /** 边框线 = 哪一边；网格线 / 刻度线 = 哪条轴 */
  key: 'left' | 'right' | 'top' | 'bottom' | 'x' | 'y'
}

export function parseGuideGid(inner: string): GuideRef | null {
  const m = GUIDE_GID.exec(inner)
  if (!m) return null
  if (m[2]) return { base: m[1], kind: 'spine', key: m[2] as GuideRef['key'] }
  return { base: m[1], kind: m[4] as 'grid' | 'tickmarks', key: m[3] as 'x' | 'y' }
}

/**
 * 合成 gid 现在在子图里的框（figure 分数 `[x, y, w, h]`、y 向下，与上传时同一口径）；
 * 子图不在、那一边不可见 / 网格没开 / 刻度线被关了（数据没有了）都是 null。
 */
export function guideBox(
  elements: readonly Pick<ManifestElement, 'gid' | 'spines' | 'grid' | 'tickmarks'>[],
  inner: string,
): [number, number, number, number] | null {
  const ref = parseGuideGid(inner)
  if (!ref) return null
  const ax = elements.find((e) => e.gid === ref.base)
  if (!ax) return null
  const pts: [number, number][] = []
  if (ref.kind === 'spine') {
    const sp = ax.spines?.[ref.key as 'left']
    if (!sp?.visible) return null
    pts.push(sp.from, sp.to)
  } else {
    const g: ElementGeometry | undefined = ax[ref.kind]?.[ref.key as 'x' | 'y']
    for (const path of g?.paths ?? []) pts.push(...path.points)
  }
  if (pts.length === 0) return null
  const xs = pts.map((q) => q[0])
  const ys = pts.map((q) => q[1])
  let x0 = Math.min(...xs)
  let x1 = Math.max(...xs)
  let y0 = Math.min(...ys)
  let y1 = Math.max(...ys)
  if (x1 - x0 < GUIDE_MIN_THICK) [x0, x1] = [x0 - GUIDE_MIN_THICK / 2, x1 + GUIDE_MIN_THICK / 2]
  if (y1 - y0 < GUIDE_MIN_THICK) [y0, y1] = [y0 - GUIDE_MIN_THICK / 2, y1 + GUIDE_MIN_THICK / 2]
  return [x0, y0, x1 - x0, y1 - y0]
}
