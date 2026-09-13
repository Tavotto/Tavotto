/**
 * 图内元素的**归属**：一个 gid 挂在谁下面。
 *
 * 引擎的 gid 是按点分段的路径（`axes_0.legend.texts_0`），但有一处不按段走：刻度
 * 文字 `axes_0.xticklabels_3` 的容器是同轴的刻度组 `axes_0.xticks`（它们在引擎里是
 * 两种伪元素，段名不同）。元素树建树与属性页的面包屑都要回答「这个元素的上一级是
 * 谁」，判据只在这里一份。
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
