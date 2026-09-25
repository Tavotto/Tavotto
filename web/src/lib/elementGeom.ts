import type { Manifest, ManifestElement } from './api'
import { segIntersectsSeg } from './pathGeom'
import { t } from '@/i18n'
import type { AlignMode } from './geometry'
import {
  flipY,
  layoutBoxes,
  remapBox,
  round4,
  unionBox,
  type AlignItem,
  type Rect4,
} from './axesLayout'
import {
  panelContentSize,
  panelRotation,
  rotateVec,
  type CanvasObject,
  type PanelObject,
  type PanelOverride,
} from '@/types/document'
import { effectiveOverride, isEffectiveOverrideAt } from '@/lib/effectiveOverride'

/**
 * 图内元素的几何代理层。
 *
 * 有些元素自己没有几何属性，位置和大小实际由别的元素决定 —— 典型是 imshow 位图：
 * 它铺满宿主 axes，manifest 里带 `geom_gid` 指向宿主。所有拖拽 / 缩放 / 对齐
 * 都要先把元素换成它的「几何落点」，override 才写得到对的 gid 上。
 */

/** 几何操作真正落到哪个 gid 上 */
export const geomGid = (el: ManifestElement) => el.geom_gid ?? el.gid

/** manifest 里 visible=false 即已被「删除」（非破坏性隐藏） */
export function isElementHidden(el: ManifestElement): boolean {
  return el.editable.some((f) => f.prop === 'visible' && f.value === false)
}

/**
 * 面板未被裁剪时占据的完整显示矩形（mm，**内容坐标系**）——manifest 的分数
 * 坐标就摊在它上面。面板旋转时内容与包围盒长宽互换，且内容以包围盒中心为
 * 中心，所以这里统一按中心推算；调用方画框时整组再绕同一个中心转回去。
 */
export function panelFullRect(panel: PanelObject): { x: number; y: number; w: number; h: number } {
  const c = panel.crop
  const content = panelContentSize(panel)
  const cx = panel.x + panel.w / 2
  const cy = panel.y + panel.h / 2
  return {
    x: cx - content.w / 2 - (c ? (c.x / c.w) * content.w : 0),
    y: cy - content.h / 2 - (c ? (c.y / c.h) * content.h : 0),
    w: content.w / (c?.w ?? 1),
    h: content.h / (c?.h ?? 1),
  }
}

/**
 * 图内元素的中心线吸附候选（页面 mm）：拖画布标注经过面板时，可吸到图内
 * 文字 / 图例 / 子图的水平与垂直中心线上——「箭头对准图里那行字的中心」
 * 是排注记的主要参照。只取有布局意义的元素（可拖动文字类 + 子图），
 * 刻度这类外壳不出线；独立箭头是线不是块，中心线没意义，跳过。
 * 元素被 override 挪过而渲染尚未回来时，中心跟着未落盘的锚点走。
 */
export function elementSnapCandidates(
  panel: PanelObject,
  manifest: Manifest,
): { xs: number[]; ys: number[] } {
  const full = panelFullRect(panel)
  const rot = panelRotation(panel)
  const cx = panel.x + panel.w / 2
  const cy = panel.y + panel.h / 2
  const xs: number[] = []
  const ys: number[] = []
  for (const el of manifest.elements) {
    if (el.gid === 'figure' || isElementHidden(el) || el.arrow_endpoints) continue
    const draggableText = el.draggable && !!el.anchor && !!el.drag_prop
    if (!el.resizable && !draggableText) continue
    let [bx, by] = el.bbox
    const [, , bw, bh] = el.bbox
    if (draggableText) {
      const a = anchorOf(panel, el)!
      bx += a[0] - el.anchor![0]
      by += a[1] - el.anchor![1]
    }
    const mx = full.x + (bx + bw / 2) * full.w
    const my = full.y + (by + bh / 2) * full.h
    const [dx, dy] = rotateVec(mx - cx, my - cy, rot)
    const px = cx + dx
    const py = cy + dy
    // 裁剪窗外的元素在画布上看不见，不在空白处凭空出参考线
    if (px < panel.x || px > panel.x + panel.w || py < panel.y || py > panel.y + panel.h) continue
    xs.push(px)
    ys.push(py)
  }
  return { xs, ys }
}

/**
 * 图内拖动的吸附候选线（页面 mm）：别的元素墨迹框的左 / 中 / 右与上 / 中 / 下，
 * 外加整张图的四边与中线。「Vacuum 与 Superconductor 左对齐」「两个子图的 x 轴
 * 标题齐平」「图例居中」都靠它。
 *
 * - `moving(gid)` 为真的元素不出线（被拖的那个、它的后代与随行元素——它们
 *   跟着一起动，吸到它们身上等于吸到自己）；
 * - 只取有版面意义的元素（与多选对齐同一判据 `isAlignable`），位图代理跳过
 *   （它的框就是宿主子图的框）；独立箭头是线不是块，跳过；
 * - 必须传**权威** manifest：拿到的是墨迹框，上一版的框会吸到旧位置上；
 * - 面板带旋转 / 翻转时返回 null（分数坐标与页面轴不再平行，与混排对齐同一取舍）。
 */
export function inFigureSnapCandidates(
  panel: PanelObject,
  manifest: Manifest,
  moving: (gid: string) => boolean,
): { xs: number[]; ys: number[] } | null {
  if (panelRotation(panel) || panel.flipH || panel.flipV) return null
  const full = panelFullRect(panel)
  const xs: number[] = []
  const ys: number[] = []
  const push = (b: Rect4) => {
    xs.push(full.x + b[0] * full.w, full.x + (b[0] + b[2] / 2) * full.w, full.x + (b[0] + b[2]) * full.w)
    ys.push(full.y + b[1] * full.h, full.y + (b[1] + b[3] / 2) * full.h, full.y + (b[1] + b[3]) * full.h)
  }
  push([0, 0, 1, 1])
  for (const el of manifest.elements) {
    if (!isAlignable(el) || isElementHidden(el) || el.arrow_endpoints || el.geom_gid) continue
    if (moving(el.gid)) continue
    const box = elementBoxOf(panel, el)
    if (box && box[2] > 0 && box[3] > 0) push(box)
  }
  return { xs, ys }
}

/**
 * 元素此刻的框（figure 分数、top-origin）：子图取 position（请求空间，与对齐同源），
 * 文字 / 图例取墨迹框并跟着未渲染回来的锚点平移。
 */
export function elementBoxOf(panel: PanelObject, el: ManifestElement): Rect4 | null {
  if (el.resizable && !el.geom_gid) {
    const pos = positionOf(panel, el)
    return pos ? flipY(pos) : null
  }
  const anchor = anchorOf(panel, el)
  if (anchor && el.anchor) {
    return [el.bbox[0] + anchor[0] - el.anchor[0], el.bbox[1] + anchor[1] - el.anchor[1], el.bbox[2], el.bbox[3]]
  }
  return [el.bbox[0], el.bbox[1], el.bbox[2], el.bbox[3]]
}

/** 分数坐标框 → 页面 mm 矩形（面板未旋转 / 未翻转时） */
export function fracBoxToMm(panel: PanelObject, b: Rect4): { x: number; y: number; w: number; h: number } {
  const full = panelFullRect(panel)
  return { x: full.x + b[0] * full.w, y: full.y + b[1] * full.h, w: b[2] * full.w, h: b[3] * full.h }
}

/** 承载 position override 的 manifest entry（位图 → 宿主 axes） */
export function geomTarget(
  manifest: Manifest | null | undefined,
  el: ManifestElement,
): ManifestElement {
  if (!el.geom_gid || !manifest) return el
  return manifest.elements.find((e) => e.gid === el.geom_gid) ?? el
}

/** 元素当前的 axes position（优先取尚未渲染回来的 override） */
export function positionOf(panel: PanelObject, el: ManifestElement): Rect4 | null {
  const ov = effectiveOverride(panel.overrides, el.gid, 'position')
  if (ov && Array.isArray(ov.value)) return (ov.value as number[]).slice(0, 4) as Rect4
  const f = el.editable.find((x) => x.prop === 'position')
  return Array.isArray(f?.value) ? ((f.value as number[]).slice(0, 4) as Rect4) : null
}

/** 图内独立箭头的当前端点（figure 分数、top-origin；优先尚未渲染回来的 override） */
export function arrowEndpointsOf(
  panel: PanelObject,
  el: ManifestElement,
): [number, number][] | null {
  if (!el.arrow_endpoints || el.arrow_endpoints.length < 2) return null
  const ov = effectiveOverride(panel.overrides, el.gid, 'endpoints_frac')
  if (ov && Array.isArray(ov.value) && ov.value.length === 4) {
    const v = ov.value as number[]
    return [
      [v[0], v[1]],
      [v[2], v[3]],
    ]
  }
  return el.arrow_endpoints
}

/**
 * 线段与矩形是否相交（同一坐标系即可，图内用 figure 分数）：图内独立箭头参与
 * 框选时按线本身算——斜箭头的 bbox 是一大块空白矩形，按 bbox 相交会让离线很远
 * 的框选也圈中它（与画布箭头的沿线命中同语义）。
 */
export function segIntersectsRect(
  a: [number, number],
  b: [number, number],
  r: { x: number; y: number; w: number; h: number },
): boolean {
  const inside = (p: [number, number]) =>
    p[0] >= r.x && p[0] <= r.x + r.w && p[1] >= r.y && p[1] <= r.y + r.h
  if (inside(a) || inside(b)) return true
  const corners: [number, number][] = [
    [r.x, r.y],
    [r.x + r.w, r.y],
    [r.x + r.w, r.y + r.h],
    [r.x, r.y + r.h],
  ]
  // 共线那一格靠 `segIntersectsSeg` 里的区间比对挡住——用叉积乘积 `<= 0`
  // 的老写法会把「四点共线但两段离得老远」判成相交（pathGeom 那份同疾同治）
  for (let i = 0; i < 4; i++) {
    if (segIntersectsSeg(a, b, corners[i], corners[(i + 1) % 4])) return true
  }
  return false
}

/**
 * 可拖动文字 / 图例的当前锚点（top-origin，**优先尚未渲染回来的 override**）。
 *
 * 拖动起手时的基准必须走这里，不能直接读 `el.anchor`：后者来自
 * `usePanelDisplayManifest`，而自己那份变体还没画出来时 `panelRender` 会退回
 * `latest[fileId]`——也就是**上一次提交之前**的 manifest。于是「拖一下、
 * 不等画完再拖一下」的第二次手势以旧锚点起算，而 `setOverride` 是整条替换
 * 语义，第一次在另一个方向上的位移被整个覆盖丢弃：不报错、界面上也看不出来，
 * 用户以为是两次叠加。脚本越重、往返越慢，这个窗口越大。
 */
export function anchorOf(panel: PanelObject, el: ManifestElement): [number, number] | null {
  if (!el.anchor || !el.drag_prop) return null
  const ov = effectiveOverride(panel.overrides, el.gid, el.drag_prop)
  if (ov && Array.isArray(ov.value)) {
    const v = ov.value as number[]
    return [v[0], v[1]]
  }
  return [el.anchor[0], el.anchor[1]]
}

/** 能参与多选对齐的元素：子图（含位图代理）与可拖动的文字 / 图例 */
export function isAlignable(el: ManifestElement): boolean {
  if (el.gid === 'figure') return false
  return !!el.resizable || (el.draggable && !!el.anchor && !!el.drag_prop)
}

export interface AlignEntry extends AlignItem {
  label: string
  /** 由新框算出该写哪条 override */
  write: (box: Rect4) => PanelOverride
}

/**
 * 把选中的 gid 列表整理成对齐用的条目：
 * 位图归并到宿主子图，同一几何落点只保留一条。
 * 子图的框从 position 换算（而不是 manifest bbox）—— aspect="equal" 的子图
 * 渲染后会贴合长宽比，bbox 与请求值略有出入，用请求空间算才不会反复回写。
 */
export function alignEntries(
  panel: PanelObject,
  manifest: Manifest,
  gids: string[],
): AlignEntry[] {
  const out: AlignEntry[] = []
  const seen = new Set<string>()

  for (const gid of gids) {
    const el = manifest.elements.find((e) => e.gid === gid)
    if (!el || !isAlignable(el)) continue
    const key = geomGid(el)
    if (seen.has(key)) continue

    if (el.resizable) {
      const target = geomTarget(manifest, el)
      const pos = positionOf(panel, target)
      if (!pos) continue
      seen.add(key)
      out.push({
        key,
        label: target.label,
        resizable: true,
        box: flipY(pos),
        write: (box) => ({ gid: key, prop: 'position', value: flipY(box).map(round4) }),
      })
    } else {
      const anchor = anchorOf(panel, el)
      if (!anchor || !el.anchor) continue
      seen.add(key)
      // bbox 是渲染那一刻的墨迹框；锚点若被 override 挪过，框要跟着挪同样的量
      const box: Rect4 = [
        el.bbox[0] + anchor[0] - el.anchor[0],
        el.bbox[1] + anchor[1] - el.anchor[1],
        el.bbox[2],
        el.bbox[3],
      ]
      const prop = el.drag_prop!
      out.push({
        key,
        label: el.label,
        resizable: false,
        box,
        write: (next) => ({
          gid: key,
          prop,
          value: [
            round4(anchor[0] + next[0] - box[0]),
            round4(anchor[1] + next[1] - box[1]),
          ],
        }),
      })
    }
  }
  return out
}

/**
 * figure 锚定的位置类 override —— 值是 figure 分数、**y 向下**。
 * 与后端 overrides.py 的 `_FRAC_ANCHORED` 同一批；改一边要同步另一边。
 */
const FRAC_ANCHORED_PROPS = new Set(['pos_frac', 'loc_frac', 'endpoints_frac'])

/**
 * **写下去会改变元素几何落点**的属性，唯一出处。
 *
 * 判据只有一条：这个 prop 的当前值在没有 override 时要从 manifest 里读
 * （`positionOf` / `anchorOf` / `size_mm` 都是），因此拿一份退回来的 manifest
 * 当初值就会把上一版的几何写成这一版的（issue #131）。属性页在几何权威缺席时
 * 必须把这些字段整个收掉，而不只是收掉对齐工具条。
 */
export const GEOMETRY_WRITE_PROPS: ReadonlySet<string> = new Set([
  'position',
  'size_mm',
  ...FRAC_ANCHORED_PROPS,
])

/** 拖动子图时跟着走的一条随行改动 */
export interface AxesCompanion {
  gid: string
  /**
   * 预览阶段要不要单独平移它的 SVG 组。子图自己的标题 / 轴标签 / 图例都嵌在
   * `<g id="axes_N">` 里面，宿主组一平移它们已经跟着动了，再来一次就是双倍。
   */
  previewsSeparately: boolean
  /** dfx/dfy 是内容分数位移，**y 向下**（与 contentDelta 的输出同一套） */
  shift: (dfx: number, dfy: number) => PanelOverride
}

/**
 * 拖动 `axesGid` 这个子图时，应当跟着走的随行元素。
 *
 * 为什么需要它：子图的标题 / 轴标签 / 刻度是 Axes 的孩子，`set_position`
 * 一挪天然跟着走，**除非用户先手动摆过它们**——那一刻它们身上多了一条
 * figure 锚定的 override（pos_frac / loc_frac / endpoints_frac），而引擎按
 * 设计会在几何变动后重放这类 override（FigS3 事故的修法，见 overrides.py），
 * 于是它们被钉死在原来的 figure 位置上，子图走了它们不走。
 *
 * 修法不是去掉那个重放（那会把 FigS3 放回来：写回文件的样子 ≠ 重开后重放的
 * 样子），而是**把存着的锚点值本身加上同一个位移**。这样热态与全量重放依旧
 * 逐位一致，撤销也只是一条。
 *
 * 另外那些视觉上是一体、artist 树上却平级的 axes（色条轴、twinx 的孪生轴）
 * 由引擎在 manifest 的 `follow_gids` 里点名——只有那边能看到 matplotlib 的
 * 共享关系。
 */
export function axesCompanions(
  panel: PanelObject,
  manifest: Manifest,
  axesGid: string,
): AxesCompanion[] {
  const host = manifest.elements.find((e) => e.gid === axesGid)
  const followGids = host?.follow_gids ?? []
  const out: AxesCompanion[] = []

  for (const gid of followGids) {
    const other = manifest.elements.find((e) => e.gid === gid)
    if (!other) continue
    const pos = positionOf(panel, other)
    if (!pos) continue
    out.push({
      gid,
      previewsSeparately: true,
      // position 是 bottom-origin：屏幕向下 = y 变小
      shift: (dfx, dfy) => ({
        gid,
        prop: 'position',
        value: [pos[0] + dfx, pos[1] - dfy, pos[2], pos[3]].map(round4),
      }),
    })
  }

  // 宿主与随行 axes 底下、被用户挪过位置的后代
  for (const d of movedDescendants(panel, [axesGid, ...followGids])) {
    out.push({
      gid: d.gid,
      // 后代嵌在所属 axes 的 <g> 里，那个组一平移它们已经跟着动了
      previewsSeparately: false,
      shift: (dfx, dfy) => ({
        gid: d.gid,
        prop: d.prop,
        // pos_frac/loc_frac 是 [x, y]，endpoints_frac 是 [ax, ay, bx, by]，
        // 都是 top-origin：位移直接加
        value: d.nums.map((n, i) => round4(n + (i % 2 === 0 ? dfx : dfy))),
      }),
    })
  }
  return out
}

/** 某些 axes 底下、带着 figure 锚定 override（被用户手动摆过）的后代 */
function movedDescendants(
  panel: PanelObject,
  roots: readonly string[],
): { gid: string; prop: string; nums: number[] }[] {
  const out: { gid: string; prop: string; nums: number[] }[] = []
  for (const [i, o] of panel.overrides.entries()) {
    if (!FRAC_ANCHORED_PROPS.has(o.prop)) continue
    // 被后面同键遮住的旧重复条目不算：引擎用的是最后那条，拿旧值平移会把它挪回旧位置
    if (!isEffectiveOverrideAt(panel.overrides, i)) continue
    if (!roots.some((root) => o.gid.startsWith(`${root}.`))) continue
    const v = o.value
    if (!Array.isArray(v) || (v.length !== 2 && v.length !== 4)) continue
    const nums = v as number[]
    if (nums.some((n) => typeof n !== 'number' || !Number.isFinite(n))) continue
    out.push({ gid: o.gid, prop: o.prop, nums })
  }
  return out
}

/**
 * 一批 `position` patch 之外，还应当随之写下的随行改动——改子图落位的**平移**手势
 * 共用这一处（单个子图拖动的预览仍用 `axesCompanions` 逐个跟手，写入语义相同）。
 *
 * 2026-09-25 用户报：挪过「(a)」之后再拖子图，标签有时不跟。单个子图的平移一直
 * 带着随行元素，可**多选整组平移**只写了选中的那几条 position——被挪过的标签身上
 * 那条 figure 锚定 override 原地不动，于是「有时跟、有时不跟」，取决于用户当时是
 * 单选还是多选（先点主图、再 ⇧ 点色条一起拖，正是最自然的操作）。
 *
 * 只处理**纯平移**（宽高不变）的 patch：随行 axes（色条、孪生轴）的 position 与宿主 /
 * 随行 axes 底下被挪过的后代，一起加上同一个位移。尺寸变了的一律不带——与单个
 * 子图缩放同一条取舍（`startAxesDrag`）：随行元素该缩到哪里没有可信答案。
 *
 * 已经在 `patches` 里的 (gid, prop) 不重复写：用户把标签和子图一起选中拖，标签
 * 自己那条已经是对的。
 */
export function companionPatchesFor(
  panel: PanelObject,
  manifest: Manifest,
  patches: readonly PanelOverride[],
): PanelOverride[] {
  const taken = new Set(patches.map((p) => `${p.gid}|${p.prop}`))
  const out: PanelOverride[] = []
  for (const p of patches) {
    if (p.prop !== 'position' || !Array.isArray(p.value) || p.value.length < 4) continue
    const el = manifest.elements.find((e) => e.gid === p.gid)
    if (!el) continue
    const from = positionOf(panel, el)
    if (!from) continue
    const to = (p.value as number[]).slice(0, 4)
    if (Math.abs(from[2] - to[2]) > 1e-6 || Math.abs(from[3] - to[3]) > 1e-6) continue
    // position 是 bottom-origin；随行元素的位移按 top-origin（y 向下）给
    const dfx = to[0] - from[0]
    const dfy = from[1] - to[1]
    if (Math.abs(dfx) < 1e-9 && Math.abs(dfy) < 1e-9) continue
    for (const c of axesCompanions(panel, manifest, p.gid)) {
      const patch = c.shift(dfx, dfy)
      const k = `${patch.gid}|${patch.prop}`
      if (taken.has(k)) continue
      taken.add(k)
      out.push(patch)
    }
  }
  return out
}

/**
 * 「装在形状里」判据的容差（pt）。要盖住的是两处量出来的缝：annotate 箭头的端点
 * 是**未扣 shrinkA / shrinkB**（默认 2pt）的锚点，而脚本常把锚点写在框的名义边上、
 * 画出来的圆角框又多出一圈 pad；框里的字偶尔探出边框一点点也还是「框里的字」。
 */
export const PATCH_CARRY_TOL_PT = 3

/** 拖动形状时跟着走的一件内容 */
export interface CarriedItem {
  gid: string
  /**
   * 箭头的哪几端跟着走（[尾, 头]）；文字 / 形状是整体平移，为 null。
   * 两端都在 = 整根平移（预览照样平移 SVG）；只有一端 = 形状变了，预览画虚线。
   */
  ends: [boolean, boolean] | null
  /** 位移后的箭头端点（figure 分数、y 向下）；非箭头为 null */
  endpointsAt: (dfx: number, dfy: number) => [[number, number], [number, number]] | null
  /** dfx/dfy 是内容分数位移，**y 向下**（与 contentDelta 的输出同一套） */
  shift: (dfx: number, dfy: number) => PanelOverride
}

/**
 * 元素的墨迹框，直接取 manifest。调用方给的必须是**几何权威**那一份
 * （`exactPanelManifest`：lastPatches 与文档 overrides 逐字一致），所以不存在
 * 「override 已写、几何还没回来」要补位移的状态——渲染挂起时拖动根本起不了手。
 */
const boxOf = (el: ManifestElement): Rect4 => [el.bbox[0], el.bbox[1], el.bbox[2], el.bbox[3]]

/**
 * 拖动形状（`role === 'patch'`）时装在它里面、该跟着走的内容（2026-09-24，用户的流程图：
 * 拖框时框里的字与连着框的箭头留在原地）。判据纯几何，只看 manifest 与文档：
 *
 * - **文字与形状**：包围盒（带容差）完全落在某个容器的包围盒里、且面积比那个容器小
 *   ——整体平移。嵌套的框因此带着自己的字一起走，拖外层虚线框 = 搬整个模块；
 * - **箭头**（有 `arrow_endpoints` 的）：端点落在某个容器包围盒里（带容差）的那一端
 *   跟着走，两端都在则整根平移——连着两个框的箭头拖其中一个框时被拉长，而不是被扯走。
 *
 * 每件内容写**它自己的**那条 override（pos_frac / endpoints_frac，值 = 当前值 + 位移），
 * 文档里仍是普通 override：重放与写回不需要新机制（与 `axesCompanions` 同一个办法）。
 * `exclude` 是已经在拖的（多选里的成员），锁定的元素不动（`lockedGids`）。
 */
export function patchContents(
  panel: PanelObject,
  manifest: Manifest,
  containerGids: readonly string[],
  exclude: ReadonlySet<string> = new Set(),
): CarriedItem[] {
  const byGid = new Map(manifest.elements.map((e) => [e.gid, e]))
  const containers = containerGids
    .map((g) => byGid.get(g))
    .filter((e): e is ManifestElement => !!e && e.role === 'patch')
    .map((e) => ({ gid: e.gid, box: boxOf(e) }))
  if (!containers.length) return []
  const locked = new Set(panel.lockedGids ?? [])
  const [wMm, hMm] = manifest.size_mm
  const tolPtMm = (PATCH_CARRY_TOL_PT * 25.4) / 72
  const tx = wMm > 0 ? tolPtMm / wMm : 0
  const ty = hMm > 0 ? tolPtMm / hMm : 0
  const within = (p: [number, number], c: Rect4) =>
    p[0] >= c[0] - tx && p[0] <= c[0] + c[2] + tx && p[1] >= c[1] - ty && p[1] <= c[1] + c[3] + ty
  const area = (r: Rect4) => r[2] * r[3]

  const out: CarriedItem[] = []
  for (const el of manifest.elements) {
    if (exclude.has(el.gid) || locked.has(el.gid) || isElementHidden(el)) continue
    if (containers.some((c) => c.gid === el.gid)) continue

    if (el.arrow_endpoints) {
      const pts = arrowEndpointsOf(panel, el)
      if (!pts || pts.length < 2) continue
      const tail: [number, number] = [pts[0][0], pts[0][1]]
      const head: [number, number] = [pts[1][0], pts[1][1]]
      const ends: [boolean, boolean] = [
        containers.some((c) => within(tail, c.box)),
        containers.some((c) => within(head, c.box)),
      ]
      if (!ends[0] && !ends[1]) continue
      const at = (dfx: number, dfy: number): [[number, number], [number, number]] => [
        ends[0] ? [tail[0] + dfx, tail[1] + dfy] : tail,
        ends[1] ? [head[0] + dfx, head[1] + dfy] : head,
      ]
      out.push({
        gid: el.gid,
        ends,
        endpointsAt: at,
        shift: (dfx, dfy) => {
          const [a, b] = at(dfx, dfy)
          return { gid: el.gid, prop: 'endpoints_frac', value: [a[0], a[1], b[0], b[1]].map(round4) }
        },
      })
      continue
    }

    if (el.role !== 'text' && el.role !== 'patch') continue
    const anchor = anchorOf(panel, el)
    if (!anchor || !el.drag_prop) continue
    const box = boxOf(el)
    const inside = containers.some(
      (c) =>
        area(box) < area(c.box) &&
        within([box[0], box[1]], c.box) &&
        within([box[0] + box[2], box[1] + box[3]], c.box),
    )
    if (!inside) continue
    const prop = el.drag_prop
    out.push({
      gid: el.gid,
      ends: null,
      endpointsAt: () => null,
      shift: (dfx, dfy) => ({
        gid: el.gid,
        prop,
        value: [round4(anchor[0] + dfx), round4(anchor[1] + dfy)],
      }),
    })
  }
  return out
}

export interface AnnotationEntry extends AlignItem {
  label: string
  /** 画布标注对象 id；有它 = 这一条改的是画布对象的 x/y，不是 override */
  objectId: string
}

/** 混排对齐条目：图内元素（写 override）或画布标注（改对象位置） */
export type MixedEntry = AlignEntry | AnnotationEntry

export const isAnnotationEntry = (e: MixedEntry): e is AnnotationEntry => 'objectId' in e

/**
 * 画布标注（文字/箭头/形状）→ 图内对齐条目：框换算成面板内容坐标系的
 * top-origin 分数，与 alignEntries 的元素框同一空间——混排对齐靠它们能
 * 同框排版。面板带旋转/翻转时换算对不上，返回空（调用方按无标注处理）。
 */
export function annotationAlignEntries(
  panel: PanelObject,
  objects: readonly CanvasObject[],
): AnnotationEntry[] {
  if (panelRotation(panel) || panel.flipH || panel.flipV) return []
  const full = panelFullRect(panel)
  const out: AnnotationEntry[] = []
  for (const o of objects) {
    if (o.type === 'panel' || o.hidden) continue
    // 画布标注的名字：文字取内容前 12 字（用户内容，原样插值），
    // 箭头/形状用类型名
    const name =
      o.type === 'text'
        ? t('annotationEntry.text', { ns: 'workspace', text: o.text.slice(0, 12) })
        : t(`annotationEntry.${o.type === 'arrow' ? 'arrow' : 'shape'}`, { ns: 'workspace' })
    out.push({
      key: `obj:${o.id}`,
      objectId: o.id,
      label: t('annotationEntry.label', { ns: 'workspace', name }),
      resizable: false,
      box: [
        (o.x - full.x) / full.w,
        (o.y - full.y) / full.h,
        o.w / full.w,
        o.h / full.h,
      ],
    })
  }
  return out
}

/** 对齐 / 分布的结果，一次写成一批 override */
export function alignPatches(entries: AlignEntry[], mode: AlignMode): PanelOverride[] {
  const boxes = layoutBoxes(entries, mode)
  const out: PanelOverride[] = []
  for (const e of entries) {
    const next = boxes.get(e.key)
    if (next) out.push(e.write(next))
  }
  return out
}

/* -------------------------------------------------------------------------- */
/*  成组缩放                                                                   */
/* -------------------------------------------------------------------------- */

export interface Group {
  entries: AlignEntry[]
  /** 组包围框：各元素 position 换算出的 top-origin 框的并集 */
  box: Rect4
}

/**
 * 能成组缩放的选区：≥2 个、且全部是子图（位图经宿主归并后也算）。
 * 混进文字 / 图例就返回 null —— 那些元素只有锚点没有尺寸，缩放无从谈起。
 */
export function groupOf(entries: AlignEntry[], min = 2): Group | null {
  if (entries.length < min || !entries.every((e) => e.resizable)) return null
  const box = unionBox(entries.map((e) => e.box))
  return box ? { entries, box } : null
}

export const resolveGroup = (panel: PanelObject, manifest: Manifest, gids: string[]) =>
  groupOf(alignEntries(panel, manifest, gids))

/** 组框从 box 变成 next 后，每个元素重映射出的新框 */
export function groupBoxes(group: Group, next: Rect4): Map<string, Rect4> {
  const out = new Map<string, Rect4>()
  for (const e of group.entries) out.set(e.key, remapBox(e.box, group.box, next))
  return out
}

/** 成组缩放的结果，一次写成一批 position override */
export function groupPatches(group: Group, next: Rect4): PanelOverride[] {
  const boxes = groupBoxes(group, next)
  return group.entries.map((e) => e.write(boxes.get(e.key)!))
}
