import { roundCornersEach } from './liveCorners'
import { pathToMm, type MmNode, type MmSubpath } from './pathEdit'
import type { FigureDocument, PathNode, PathObject, PathSubpath } from '@/types/document'

/**
 * 路径实时圆角的记录（ADR 0185 M7，方案 B）。
 *
 * 文档里 `subpaths[i].nodes` 照旧是圆过之后的几何（导出、后端、旧版本都只认它，零改动）；`subpaths[i].liveCorners` 另记圆之前的
 * 尖角锚点（框比例坐标，与 `nodes` 同一套）和每个尖角的半径（相对框的长边）。比例坐标 + 相对半径让移动、等比缩放、旋转、
 * 复制、粘贴、阵列都不用碰它；镜像在 `transform.flipPathLocal` 里与锚点一起翻。
 *
 * **对不上就丢——集中在这一处**：`liveCornerRecord` 把记录里的尖角按记下的半径重新圆一遍，与现在的 `nodes` 逐点比，对不上
 * （非等比缩放、斜切、编辑锚点、路径查找器、简化、旧版本改了几何却原样带着旧记录……）就当没有。所有读记录的地方都只经它；
 * 提交文档时 `pruneLiveCorners`（`documentStore` 的提交流程里调）用同一个判据把对不上的记录删掉，不让它留在文件里。
 */

export type LiveCorners = NonNullable<PathSubpath['liveCorners']>

const scaleOf = (o: Pick<PathObject, 'w' | 'h'>) => Math.max(o.w, o.h, 1e-9)

const toMm = (o: PathObject, q: [number, number]): [number, number] => [o.x + q[0] * o.w, o.y + q[1] * o.h]

function sharpMm(o: PathObject, rec: LiveCorners, closed: boolean): MmSubpath {
  return {
    closed,
    nodes: rec.sharp.map((n) => {
      const p = toMm(o, n.p)
      return { p, in: n.in ? toMm(o, n.in) : p, out: n.out ? toMm(o, n.out) : p, smooth: n.smooth === true }
    }),
  }
}

/** 记录里的半径（mm）按尖角下标展开成 `roundCornersEach` 的表 */
const radiiMap = (si: number, radii: readonly number[], scale: number) =>
  new Map(radii.map((r, i) => [`${si}:${i}`, r * scale] as const).filter(([, r]) => r > 0))

const near = (a: [number, number], b: [number, number], tol: number) => Math.abs(a[0] - b[0]) <= tol && Math.abs(a[1] - b[1]) <= tol

/**
 * 第 `si` 条子路径还有效的圆角记录：尖角（页面 mm、未旋转）与每个尖角的半径（mm）。没有记录 / 对不上 = null。
 * 判据：尖角按半径重新圆出来的锚点、手柄与现在的 `nodes` 逐个相同（容差 = 框长边的 1e-6）。
 */
export function liveCornerRecord(o: PathObject, si: number): { sharp: MmSubpath; radii: number[] } | null {
  const sp = o.subpaths[si]
  const rec = sp?.liveCorners
  if (!rec || !Array.isArray(rec.sharp) || !Array.isArray(rec.radii) || rec.sharp.length !== rec.radii.length || rec.sharp.length < 2) return null
  const scale = scaleOf(o)
  const sharp = sharpMm(o, rec, sp.closed)
  const baked = roundCornersEach([sharp], radiiMap(0, rec.radii, scale))[0]
  const now = pathToMm(o)[si]
  if (!now || baked.nodes.length !== now.nodes.length || baked.closed !== now.closed) return null
  const tol = scale * 1e-6
  for (let i = 0; i < now.nodes.length; i++) {
    const a = baked.nodes[i]
    const b = now.nodes[i]
    if (!near(a.p, b.p, tol) || !near(a.in, b.in, tol) || !near(a.out, b.out, tol)) return null
  }
  return { sharp, radii: rec.radii.map((r) => r * scale) }
}

/** 页面 mm 的尖角 + 半径（mm）→ 记录（`target` 的框比例坐标、相对长边的半径）；半径全是 0 = 不记 */
export function makeLiveCorners(target: PathObject, sharp: MmSubpath, radiiMm: readonly number[]): LiveCorners | undefined {
  if (!radiiMm.some((r) => r > 0)) return undefined
  const scale = scaleOf(target)
  const frac = (q: [number, number]): [number, number] => [(q[0] - target.x) / target.w, (q[1] - target.y) / target.h]
  const same = (a: [number, number], b: [number, number]) => a[0] === b[0] && a[1] === b[1]
  return {
    sharp: sharp.nodes.map(
      (n: MmNode): PathNode => ({
        p: frac(n.p),
        ...(same(n.in, n.p) ? {} : { in: frac(n.in) }),
        ...(same(n.out, n.p) ? {} : { out: frac(n.out) }),
        ...(n.smooth ? { smooth: true as const } : {}),
      }),
    ),
    radii: radiiMm.map((r) => r / scale),
  }
}

/**
 * 提交文档时的集中校验：这一次变了的路径对象（`next` 里与 `before` 同 id 的不是同一个引用），记录对不上就在 `draft` 里删掉。
 * 没变的对象不碰（读的时候 `liveCornerRecord` 照样会判）。`documentStore.produceStamped` 每次提交 / 拖动的每一帧都经过这里。
 */
export function pruneLiveCorners(draft: FigureDocument, before: FigureDocument, next: FigureDocument): void {
  if (!next.objects.some((o) => o.type === 'path' && o.subpaths.some((sp) => sp.liveCorners))) return
  const previous = new Map(before.objects.map((o) => [o.id, o]))
  next.objects.forEach((o, k) => {
    if (o.type !== 'path' || previous.get(o.id) === o || !o.subpaths.some((sp) => sp.liveCorners)) return
    o.subpaths.forEach((sp, si) => {
      if (!sp.liveCorners || liveCornerRecord(o, si)) return
      const d = draft.objects[k]
      if (d && d.type === 'path' && d.subpaths[si]) delete d.subpaths[si].liveCorners
    })
  })
}
