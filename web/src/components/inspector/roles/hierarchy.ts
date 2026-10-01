import type { Manifest, ManifestGroup } from '@/lib/api'

/**
 * 图内元素的**归属**：一个 gid 挂在谁下面。
 *
 * 引擎的 gid 是按点分段的路径（`axes_0.legend.texts_0`），但有一处不按段走：刻度
 * 文字 `axes_0.xticklabels_3` 的容器是同轴的刻度组 `axes_0.xticks`（它们在引擎里是
 * 两种伪元素，段名不同）。元素树建树与属性页的面包屑都要回答「这个元素的上一级是
 * 谁」，判据只在这里一份。
 *
 * 路径之上还有一层**显式父级**（manifest 的 `parent_gid`，引擎只按色条声明的宿主写）：
 * 共享色条的子图与色条轴挂在它们的组下，单宿主色条的色条轴挂在宿主子图下。显式的先认，
 * 指向的节点不在这份 manifest 里就回退到路径——`structuralParent` 是两者合起来的那一份。
 * 抽屉（元素树的「文字 / 数据…」聚类）只是视图容器，永远不出现在这里。
 */

/** gid → 父 gid：先看刻度特例，再按段收缩到 manifest 里存在的最近祖先；根是 figure */
export function parentGid(gid: string, has: (gid: string) => boolean): string | null {
  if (gid === 'figure') return null
  const tickm = gid.match(/^(.*)\.([xyz])ticklabels_\d+$/)
  if (tickm && has(`${tickm[1]}.${tickm[2]}ticks`)) return `${tickm[1]}.${tickm[2]}ticks`
  let cur = gid
  while (cur.includes('.')) {
    cur = cur.slice(0, cur.lastIndexOf('.'))
    if (has(cur)) return cur
  }
  return 'figure'
}

/**
 * 面包屑里「子图」与「元素」之间那一级：元素的直接容器，**不是**子图或整张图
 * 的时候才有（图例项 → 图例；刻度文字 → X 轴刻度；柱 → 柱形系列）。子图那一级
 * 面包屑自己会写，整张图是根，都不重复。
 */
export function containerGid(
  gid: string,
  has: (gid: string) => boolean,
  isAxes: (gid: string) => boolean,
): string | null {
  const p = parentGid(gid, has)
  if (!p || p === 'figure' || isAxes(p)) return null
  return p
}

/** 组 gid 的前缀（引擎 `manifest._colorbar_structure`：`group:<色条轴 gid>`） */
export const isGroupGid = (gid: string): boolean => gid.startsWith('group:')

export const groupByGid = (
  manifest: Manifest | null | undefined,
  gid: string,
): ManifestGroup | undefined => manifest?.groups?.find((g) => g.gid === gid)

/**
 * 这份 manifest 的真实父级解析：组挂在整张图下；元素先认显式 `parent_gid`（它指向的
 * 元素或组确实在），否则按 gid 路径（`parentGid`）。元素树建树、面包屑、祖先展开都用它。
 */
export function structuralParent(manifest: Manifest): (gid: string) => string | null {
  const els = new Map(manifest.elements.map((e) => [e.gid, e]))
  const groups = new Set((manifest.groups ?? []).map((g) => g.gid))
  const has = (g: string) => els.has(g) || groups.has(g)
  return (gid) => {
    if (gid === 'figure') return null
    if (groups.has(gid)) return 'figure'
    const explicit = els.get(gid)?.parent_gid
    if (explicit && explicit !== gid && has(explicit)) return explicit
    return parentGid(gid, has)
  }
}

/** 真实祖先链，自根（figure）向下、不含自己。显式父级成环时截断（不会无限走） */
export function ancestorsOf(parentOf: (gid: string) => string | null, gid: string): string[] {
  const out: string[] = []
  const seen = new Set([gid])
  for (let p = parentOf(gid); p && !seen.has(p); p = parentOf(p)) {
    seen.add(p)
    out.unshift(p)
  }
  return out
}
