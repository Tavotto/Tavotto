/**
 * 图内元素「创建副本」的判据与换算（ADR 0207）：哪些元素第一期能复制成图内副本、副本的 gid 长什么样、
 * 落点（`at`）怎么从源**画出来的锚点**换出来。纯函数，不读 store、不写文档、不发请求：
 * 要写的 override 交给 `store/actions.duplicateFigureElements`，负载 / 分派在 `lib/clipboard`。
 *
 * ## 与引擎的严格同源对（`docs/rules/repo/same-origin-pairs.md`）
 *
 * * `COPY_PROP` / `COPY_GID_RE` ↔ `engine/overrides.COPY_PROP` / `_COPY_GID`；
 * * `copyHostOf` / `isCopySourceGid`（第一期源的闭集）↔ `engine/overrides._copy_source_supported`；
 *   两侧各读一份向量：`tests/golden/figure_copy_source_vectors.json`。
 * * 宿主是 3D 子图 / 色条轴时引擎拒绝（`_ensure_copy`）：前端用 manifest 的宿主元素判（`role === 'axes3d'` / `is_colorbar`），
 *   不等引擎回一条 warning 才发现。
 *
 * ## 落点
 *
 * `at` = 源画出来的锚点（manifest 的 `anchor`：figure 分数、y 向下，文字的 `get_transform().transform(get_position())`）
 * 向页面上的右下错开 `DUPLICATE_OFFSET_MM`（过 `pageToContentVec`，面板旋转 / 翻转时仍是页面右下），
 * 再用源的墨迹框把错开量收回图内；最后换成宿主的分数：
 * 子图：axes 分数，用 manifest **画出来的** `position`（含 `aspect='equal'` 缩小后的实际框，不用 position override 的请求框）；
 * 整图：figsize 的 figure 分数（y 向上）。脚本带 `bbox_inches` 且图幅生效时，manifest 的锚点是图幅分数，要经
   `manifest.frame`（`figsize_mm` / `savefig_mm`）换回 figsize 分数，引擎才按 `transFigure` 落在同一处。
 * 几何只认权威（exact）manifest，调用方负责传对的那一份（ADR 0017）。
 */
import type { Manifest, ManifestElement } from '@/lib/api'
import { effectiveOverride } from '@/lib/effectiveOverride'
import { panelFullRect } from '@/lib/elementGeom'
import { pageToContentVec, panelContentTransform } from '@/lib/panelTransform'
import { DUPLICATE_OFFSET_MM } from '@/lib/units'
import type { PanelObject, PanelOverride } from '@/types/document'

/** 副本那条 override 的 prop 名（`engine/overrides.COPY_PROP`） */
export const COPY_PROP = 'copy_of'

/** 副本 gid：`axes_N.copy_xxxxxxxx` / `fig.copy_xxxxxxxx`（`engine/overrides._COPY_GID`） */
export const COPY_GID_RE = /^(axes_\d+|fig)\.copy_[a-z0-9]{8}$/

/** 副本的 gid（图内副本是前端造的元素，不是脚本里的 artist） */
export const isCopyGid = (gid: string): boolean => COPY_GID_RE.test(gid)

const COPY_ID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

/** 副本 gid 的 8 位随机串；`taken` 里已有的重抽（同一次粘贴里连造几个也不撞） */
export function newCopyGid(host: string, taken: ReadonlySet<string>): string {
  for (;;) {
    let id = ''
    for (let i = 0; i < 8; i++) id += COPY_ID_ALPHABET[(Math.random() * COPY_ID_ALPHABET.length) | 0]
    const gid = `${host}.copy_${id}`
    if (!taken.has(gid)) return gid
  }
}

const AXES_SOURCE_RE = /^(axes_\d+)\.(?:title|title_left|title_right|xlabel|ylabel|texts_\d+)$/
const FIG_SOURCE_RE = /^fig\.texts_\d+$/

/**
 * 第一期能当副本源的 gid 的宿主（`axes_N` / `fig`），不是这个形状返回 null。
 * 与 `engine/overrides._copy_source_supported` 同一个闭集：子图的标题（含左 / 右标题）、轴标题、`ax.text` / `annotate` 的文字，
 * 或整图的 `fig.texts_N`。刻度数字（不是常驻 artist）、图例文字（偏差 issue #1667）、副本自己都不在里面。
 */
export function copyHostOf(gid: string): string | null {
  if (FIG_SOURCE_RE.test(gid)) return 'fig'
  return AXES_SOURCE_RE.exec(gid)?.[1] ?? null
}

/** gid 的形状是第一期能当源的（不看宿主是不是 3D / 色条——那要 manifest） */
export const isCopySourceGid = (gid: string): boolean => copyHostOf(gid) !== null

/** 副本元素自己的宿主（`axes_N` / `fig`），不是副本 gid 返回 null */
const copyHostOfCopyGid = (gid: string): string | null => COPY_GID_RE.exec(gid)?.[1] ?? null

/** 副本 gid 对应的 `copy_of` 值里的源 gid；没有这条记录 / 形状不对 = null */
function srcOfCopy(panel: Pick<PanelObject, 'overrides'>, gid: string): string | null {
  const v = effectiveOverride(panel.overrides, gid, COPY_PROP)?.value
  const src = v && typeof v === 'object' ? (v as { src?: unknown }).src : null
  return typeof src === 'string' ? src : null
}

/**
 * 「创建副本」的出处：新副本按谁建（`copy_of.src`）、样式从哪个 gid 抄。
 * 脚本里的源文字：出处就是它自己；**选中的是副本**（刚粘出来立刻再按 ⌘D 的常见情形）：新副本仍以原来的源为 `src`
 * （引擎的源闭集不含副本），样式、文字、锚点按这只副本此刻的样子——所以连按 ⌘D 是在副本旁边再排一只。
 */
export function duplicationOrigin(
  panel: Pick<PanelObject, 'overrides'>,
  el: Pick<ManifestElement, 'gid'>,
): { host: string; src: string; styleFrom: string } | null {
  const own = copyHostOf(el.gid)
  if (own) return { host: own, src: el.gid, styleFrom: el.gid }
  const host = copyHostOfCopyGid(el.gid)
  const src = host ? srcOfCopy(panel, el.gid) : null
  return host && src && copyHostOf(src) === host ? { host, src, styleFrom: el.gid } : null
}

/**
 * 这个元素能不能复制成图内副本：出处认得（脚本里的第一期源，或一只副本）、是单独一段文字（有 `text_block`）、有画出来的锚点，
 * 且宿主不是 3D 子图 / 色条轴（宿主元素在 manifest 里）。判据只有这一个，⌘C 的负载、弹层的「创建副本」、⌘D 同用。
 */
export function isDuplicableElement(
  panel: Pick<PanelObject, 'overrides'>,
  manifest: Manifest,
  el: ManifestElement,
): boolean {
  const origin = duplicationOrigin(panel, el)
  if (!origin || !el.text_block || !el.anchor) return false
  if (origin.host === 'fig') return true
  const hostEl = manifest.elements.find((e) => e.gid === origin.host)
  return !!hostEl && hostEl.role === 'axes' && !hostEl.is_colorbar
}

/** 源上不抄给副本的 prop：文字（副本自带快照）、位置（副本的位置归 `at`）、可见性（副本一出来总是可见） */
const NOT_CARRIED_PROPS: ReadonlySet<string> = new Set(['text', 'pos_frac', 'visible', COPY_PROP])

/** 源上已有的样式类 override（字体、字号、粗斜、颜色、转角、底框、描边……），原样抄给副本 gid；不带 `identity` */
export function carriedStyleOverrides(
  panel: Pick<PanelObject, 'overrides'>,
  srcGid: string,
  copyGid: string,
): { gid: string; prop: string; value: unknown }[] {
  const out: { gid: string; prop: string; value: unknown }[] = []
  const seen = new Set<string>()
  for (let i = panel.overrides.length - 1; i >= 0; i--) {
    const o = panel.overrides[i]
    if (o.gid !== srcGid || NOT_CARRIED_PROPS.has(o.prop) || seen.has(o.prop)) continue
    seen.add(o.prop) // 重复条目取生效的最后一条
    out.push({ gid: copyGid, prop: o.prop, value: structuredClone(o.value) })
  }
  return out.reverse()
}

/** 副本 gid 上除 `copy_of` 以外的 override（「恢复此元素修改」只清它们，副本留着） */
export const copyStyleOverrides = (overrides: readonly PanelOverride[], gid: string): PanelOverride[] =>
  overrides.filter((o) => o.gid === gid && o.prop !== COPY_PROP)

/** 源此刻显示的文字：override 优先，其次引擎报的可编辑值，最后是 `text_block`（含公式的原文） */
export function shownTextOf(panel: Pick<PanelObject, 'overrides'>, el: ManifestElement): string | null {
  const ov = effectiveOverride(panel.overrides, el.gid, 'text')
  if (typeof ov?.value === 'string') return ov.value
  const field = el.editable.find((f) => f.prop === 'text')?.value
  if (typeof field === 'string') return field
  return typeof el.text_block?.text === 'string' ? el.text_block.text : null
}

/**
 * 页面右下的 `DUPLICATE_OFFSET_MM` 换成 figure 分数里的位移（y 向下）。
 * 先过 `pageToContentVec`：面板旋转 / 翻转时内容里的「右下」不是页面的右下。
 */
export function pageOffsetInFigureFrac(panel: PanelObject, mm = DUPLICATE_OFFSET_MM): [number, number] | null {
  const full = panelFullRect(panel)
  if (!(full.w > 0) || !(full.h > 0)) return null
  const [cx, cy] = pageToContentVec(panelContentTransform(panel), mm, mm)
  return [cx / full.w, cy / full.h]
}

/**
 * 错开量收回图内：源的墨迹框 `[x, y, w, h]`（figure 分数，y 向下）加上位移后不许超出 [0, 1]。
 * 源本来就在图外（或比图还大）的那一维不再往外走，也不硬拽回来。
 */
export function retractIntoFigure(
  bbox: readonly [number, number, number, number],
  d: readonly [number, number],
): [number, number] {
  const one = (pos: number, size: number, delta: number) => {
    const hi = Math.max(0, 1 - pos - size) // 往正向最多还能走多远
    const lo = Math.min(0, -pos) // 往负向最多还能走多远
    return delta >= 0 ? Math.min(delta, hi) : Math.max(delta, lo)
  }
  return [one(bbox[0], bbox[2], d[0]), one(bbox[1], bbox[3], d[1])]
}

export interface CopyPlacement {
  /** 宿主 gid：`axes_N` 或 `fig` */
  host: string
  /** `copy_of.src`：脚本里的那段源文字（选中的是副本时仍是原来的源） */
  src: string
  /** 样式从哪个 gid 的 override 抄（源本身，或被复制的那只副本） */
  styleFrom: string
  /** `copy_of.at`：宿主的 axes 分数（子图）/ figure 分数（整图，y 向上） */
  at: [number, number]
  /** `copy_of.text`：粘贴这一刻源显示的文字 */
  text: string
}

/**
 * 源元素 → 副本的落点与文字；元素不能复制、宿主的框 / 面板尺寸算不出来时 null。
 * `manifest` **必须是权威（exact）的那份**——读锚点 / 位置之后要写文档（ADR 0017）。
 */
export function copyPlacementOf(panel: PanelObject, manifest: Manifest, el: ManifestElement): CopyPlacement | null {
  const origin = duplicationOrigin(panel, el)
  if (!origin || !isDuplicableElement(panel, manifest, el) || !el.anchor) return null
  const { host, src, styleFrom } = origin
  const text = shownTextOf(panel, el)
  if (text === null) return null
  const d = pageOffsetInFigureFrac(panel)
  if (!d) return null
  const [dx, dy] = retractIntoFigure(el.bbox, d)
  const x = el.anchor[0] + dx // figure 分数，y 向下
  const y = el.anchor[1] + dy
  if (host === 'fig') {
    const g = frameFracToFigsize(manifest, x, y)
    return g ? finite({ host, src, styleFrom, at: [g[0], 1 - g[1]], text }) : null
  }
  const hostEl = manifest.elements.find((e) => e.gid === host)
  const pos = hostEl ? renderedPositionOf(hostEl) : null
  if (!pos || !(pos[2] > 0) || !(pos[3] > 0)) return null
  // position 是 figure 分数、y 向上（[x0, y0, w, h]）
  return finite({ host, src, styleFrom, at: [(x - pos[0]) / pos[2], (1 - y - pos[1]) / pos[3]], text })
}

/**
 * 宿主子图**画出来的**框（manifest 的 `position`：对外图幅的分数、y 向上，引擎读 `ax.get_position()`，已含 `apply_aspect`）。
 * 不读 position override 的请求框：`aspect='equal'` 时实际框缩在请求框里，而引擎按 `host.transAxes`（实际框）重放 `at`
 * （Codex #2163）。manifest 与锚点同一次渲染，所以两者是同一把尺。
 */
function renderedPositionOf(hostEl: ManifestElement): [number, number, number, number] | null {
  const v = hostEl.editable.find((x) => x.prop === 'position')?.value
  return Array.isArray(v) && v.length >= 4 && v.slice(0, 4).every((n) => typeof n === 'number')
    ? (v.slice(0, 4) as [number, number, number, number])
    : null
}

/**
 * 对外图幅分数（y 向下，manifest 的锚点 / 外框所在的尺）→ figsize 分数（y 向下）。
 * 脚本按 `bbox_inches` 存盘时 manifest 量在 savefig 图幅里（`manifest.frame`，生效时），而引擎把整图副本的 `at` 按
 * `fig.transFigure`（figsize）解读：差一个图幅的偏移与缩放（Codex #2163）。图幅按 figsize 写：脚本里别的东西改了
 * 图幅范围，副本仍挂在图上原来的位置——与副本跟 figsize 走的引擎口径一致。图幅不生效 / 没有图幅 = 同一把尺，原样。
 */
function frameFracToFigsize(manifest: Manifest, fx: number, fy: number): [number, number] | null {
  const f = manifest.frame
  if (!f || f.source !== 'savefig' || !f.active) return [fx, fy]
  const [gw, gh] = f.figsize_mm
  const [left, top, w, h] = f.savefig_mm
  if (!(gw > 0) || !(gh > 0)) return null
  return [(left + fx * w) / gw, (top + fy * h) / gh]
}

function finite(p: CopyPlacement): CopyPlacement | null {
  return p.at.every(Number.isFinite) ? p : null
}
