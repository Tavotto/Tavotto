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
  const ov = panel.overrides.find((o) => o.gid === el.gid && o.prop === 'position')
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
  const ov = panel.overrides.find((o) => o.gid === el.gid && o.prop === 'endpoints_frac')
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
 * `usePanelManifest`，而自己那份变体还没画出来时 `panelRender` 会退回
 * `latest[fileId]`——也就是**上一次提交之前**的 manifest。于是「拖一下、
 * 不等画完再拖一下」的第二次手势以旧锚点起算，而 `setOverride` 是整条替换
 * 语义，第一次在另一个方向上的位移被整个覆盖丢弃：不报错、界面上也看不出来，
 * 用户以为是两次叠加。脚本越重、往返越慢，这个窗口越大。
 */
export function anchorOf(panel: PanelObject, el: ManifestElement): [number, number] | null {
  if (!el.anchor || !el.drag_prop) return null
  const ov = panel.overrides.find((o) => o.gid === el.gid && o.prop === el.drag_prop)
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
  const roots = [axesGid, ...followGids]
  for (const o of panel.overrides) {
    if (!FRAC_ANCHORED_PROPS.has(o.prop)) continue
    if (!roots.some((root) => o.gid.startsWith(`${root}.`))) continue
    const v = o.value
    if (!Array.isArray(v) || (v.length !== 2 && v.length !== 4)) continue
    const nums = v as number[]
    if (nums.some((n) => typeof n !== 'number' || !Number.isFinite(n))) continue
    const { gid, prop } = o
    out.push({
      gid,
      // 后代嵌在所属 axes 的 <g> 里，那个组一平移它们已经跟着动了
      previewsSeparately: false,
      shift: (dfx, dfy) => ({
        gid,
        prop,
        // pos_frac/loc_frac 是 [x, y]，endpoints_frac 是 [ax, ay, bx, by]，
        // 都是 top-origin：位移直接加
        value: nums.map((n, i) => round4(n + (i % 2 === 0 ? dfx : dfy))),
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
