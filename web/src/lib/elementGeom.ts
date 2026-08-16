import type { Manifest, ManifestElement } from './api'
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
import type { PanelObject, PanelOverride } from '@/types/document'

/**
 * 图内元素的几何代理层。
 *
 * 有些元素自己没有几何属性，位置和大小实际由别的元素决定 —— 典型是 imshow 位图：
 * 它铺满宿主 axes，manifest 里带 `geom_gid` 指向宿主。所有拖拽 / 缩放 / 对齐
 * 都要先把元素换成它的「几何落点」，override 才写得到对的 gid 上。
 */

/** 几何操作真正落到哪个 gid 上 */
export const geomGid = (el: ManifestElement) => el.geom_gid ?? el.gid

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

/** 可拖动文字 / 图例的当前锚点（top-origin，优先 override） */
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
