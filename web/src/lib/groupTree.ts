import type { CanvasObject } from '@/types/document'

/**
 * 嵌套组与剪切蒙版（ADR 0191）的**唯一判据**：纯函数，不碰 store。
 *
 * 数据只在对象身上：`groupId` = 最内层组（旧构建只认它，照样当一层组用），`groupPath` = 外 → 内的整条链
 * （两层及以上才写），`clipMask` = 「我是我最内层组的剪切路径」。没有文档级的组表：组存在 = 有对象挂在它下面，
 * 所以删对象、换画布、旧构建改 groupId 都不会留下悬空的组。与布局组（`layoutGroups`，行 / 列 / 网格排版约束）
 * 是两回事：布局组只按 `groupId` 认成员，这里不碰它。
 *
 * **作用域**：组内编辑（双击进组）时选择只在那一层里算——`scope` 是正在编辑的组 id，`null` = 画布顶层。
 * 点中一个对象，选中的是它在这一层里的「单元」：直接成员就是它自己，否则是它所在的、紧挨着这一层的那个子组。
 */

/** 对象的组链（外 → 内）。`groupPath` 与 `groupId` 对不上（旧构建改过 groupId）时只认 `[groupId]` */
export function groupPathOf(o: Pick<CanvasObject, 'groupId' | 'groupPath'>): string[] {
  const g = o.groupId
  if (!g) return []
  const p = o.groupPath
  if (
    Array.isArray(p) &&
    p.length >= 2 &&
    p[p.length - 1] === g &&
    p.every((x) => typeof x === 'string' && x.length > 0) &&
    new Set(p).size === p.length
  ) {
    return p
  }
  return [g]
}

/** 把组链写回对象（就地改，可用于 Immer 草稿）：空 = 不在任何组；一层只写 groupId，两层及以上才写 groupPath */
export function setGroupPath(o: CanvasObject, path: readonly string[]): void {
  if (!path.length) {
    delete o.groupId
    delete o.groupPath
    return
  }
  o.groupId = path[path.length - 1]
  if (path.length >= 2) o.groupPath = [...path]
  else delete o.groupPath
}

/** 组单元的键：`g:<组 id>`；对象单元的键就是对象 id */
export const groupKey = (gid: string) => `g:${gid}`
export const isGroupKey = (key: string) => key.startsWith('g:')
export const gidOfKey = (key: string) => key.slice(2)

/** 对象在 `scope` 这一层里属于哪个单元；不在这一层里（组内编辑时组外的对象）= null */
export function unitKeyOf(o: CanvasObject, scope: string | null): string | null {
  const p = groupPathOf(o)
  let k = 0
  if (scope) {
    const i = p.indexOf(scope)
    if (i < 0) return null
    k = i + 1
  }
  return k < p.length ? groupKey(p[k]) : o.id
}

/** 对象在不在 `scope` 这个组里（任意深度）；scope = null 时都在 */
export const inScope = (o: CanvasObject, scope: string | null): boolean =>
  !scope || groupPathOf(o).includes(scope)

/** 一个单元的全部对象 id（文档顺序 = z 序底 → 顶） */
export function unitMembers(objects: readonly CanvasObject[], key: string): string[] {
  if (!isGroupKey(key)) return objects.some((o) => o.id === key) ? [key] : []
  const gid = gidOfKey(key)
  return objects.filter((o) => groupPathOf(o).includes(gid)).map((o) => o.id)
}

/** 组 `gid` 在对象链里所处的前缀（含它自己）；没有对象挂在它下面 = null */
export function prefixOf(objects: readonly CanvasObject[], gid: string): string[] | null {
  for (const o of objects) {
    const p = groupPathOf(o)
    const i = p.indexOf(gid)
    if (i >= 0) return p.slice(0, i + 1)
  }
  return null
}

/** 能当剪切路径的形状：封闭的那几种（直线 / 弧线 / 大括号只有描边，没有「里面」） */
const MASK_SHAPES: ReadonlySet<string> = new Set(['rect', 'ellipse', 'triangle', 'diamond', 'polygon', 'star'])

/** 这个对象能不能当剪切蒙版：自由路径（至少一条画得出的子路径）与封闭形状；文字、图、箭头不行 */
export function canBeMask(o: CanvasObject): boolean {
  if (o.type === 'path') {
    const subs = (o as { subpaths?: unknown }).subpaths
    return Array.isArray(subs) && subs.some((sp) => Array.isArray(sp?.nodes) && sp.nodes.length >= 2)
  }
  return o.type === 'shape' && MASK_SHAPES.has((o as { shape?: string }).shape ?? '')
}

/**
 * 每个组生效的剪切路径：组里（直接成员）标了 `clipMask`、能当蒙版的最上面那个。`clipOff`（蒙版本身被隐藏 = 暂时不裁）的
 * 不算生效；`includeOff` 时也算上（释放蒙版、图层树标「剪切组」用）。
 */
export function groupMasks(
  objects: readonly CanvasObject[],
  { includeOff = false }: { includeOff?: boolean } = {},
): Map<string, CanvasObject> {
  const out = new Map<string, CanvasObject>()
  for (const o of objects) {
    if (!o.clipMask || !o.groupId || !canBeMask(o) || (o.clipOff && !includeOff)) continue
    const p = groupPathOf(o)
    out.set(p[p.length - 1], o) // 越往后越靠上：上面的覆盖下面的
  }
  return out
}

/** 这个对象是不是某个组正在生效的剪切路径（不画、不导出，只拿来裁剪） */
export function isActiveMask(o: CanvasObject, masks: ReadonlyMap<string, CanvasObject>): boolean {
  return !!o.clipMask && !!o.groupId && masks.get(groupPathOf(o).at(-1)!)?.id === o.id
}

/** 裁这个对象的剪切路径，外 → 内（每一层组各一个，没有蒙版的层跳过；蒙版自己不裁自己），连同它属于哪个组 */
export function clipEntriesOf(
  o: CanvasObject,
  masks: ReadonlyMap<string, CanvasObject>,
): { gid: string; mask: CanvasObject }[] {
  const out: { gid: string; mask: CanvasObject }[] = []
  for (const gid of groupPathOf(o)) {
    const m = masks.get(gid)
    if (m && m.id !== o.id) out.push({ gid, mask: m })
  }
  return out
}

/** 裁这个对象的剪切路径，外 → 内（每一层组各一个，没有蒙版的层跳过；蒙版自己不裁自己） */
export function clipChainOf(o: CanvasObject, masks: ReadonlyMap<string, CanvasObject>): CanvasObject[] {
  return clipEntriesOf(o, masks).map((e) => e.mask)
}

/**
 * 剪切组里已经没有被裁的对象了（删光了 / 拆走了），蒙版不该继续藏着：返回这些蒙版的 id，调用方把它们还原成
 * 普通可见对象（去掉 `clipMask`、取消隐藏）。不在任何组里的 `clipMask` 也算。
 */
export function orphanMasks(objects: readonly CanvasObject[]): string[] {
  const out: string[] = []
  for (const o of objects) {
    if (!o.clipMask) continue
    const gid = groupPathOf(o).at(-1)
    if (!gid || !objects.some((x) => x.id !== o.id && !x.clipMask && groupPathOf(x).includes(gid))) out.push(o.id)
  }
  return out
}

/**
 * 把 `ids` 挪成连续的一段，落在其中最上面那个的位置（Illustrator 成组 / 建蒙版的做法），彼此的先后不变。
 * 返回新数组；本来就连续时原样返回同一个引用。
 */
export function gatherContiguous<T extends { id: string }>(objects: readonly T[], ids: ReadonlySet<string>): T[] {
  const idx = objects.map((o, i) => (ids.has(o.id) ? i : -1)).filter((i) => i >= 0)
  if (idx.length < 2 || idx[idx.length - 1] - idx[0] === idx.length - 1) return objects as T[]
  const members = idx.map((i) => objects[i])
  const rest = objects.filter((o) => !ids.has(o.id))
  // 最上面那个成员之下有几个非成员 = 插入点
  const top = idx[idx.length - 1]
  const below = top + 1 - idx.length
  return [...rest.slice(0, below), ...members, ...rest.slice(below)]
}

/** 图层树的节点：对象，或组（子节点同样按上 → 下） */
export type LayerNode =
  | { kind: 'object'; obj: CanvasObject }
  | { kind: 'group'; gid: string; children: LayerNode[]; mask: CanvasObject | null; count: number }

/**
 * 图层树（顶层在最上面）：组出现在它最上面那个成员的位置，成员挂在下面，嵌套组逐层挂。
 * 节点按「整条前缀」认：旧构建改乱的链（同一个组 id 挂在两个不同的父组下）各自成一棵，不会把对象挂丢。
 */
export function layerTree(objects: readonly CanvasObject[]): LayerNode[] {
  // 暂时不裁（`clipOff`）的剪切组仍标成剪切组，蒙版行仍是「剪切路径」
  const masks = groupMasks(objects, { includeOff: true })
  const root: LayerNode[] = []
  const nodes = new Map<string, Extract<LayerNode, { kind: 'group' }>>()
  for (let i = objects.length - 1; i >= 0; i--) {
    const o = objects[i]
    let list = root
    const path = groupPathOf(o)
    for (let k = 0; k < path.length; k++) {
      const key = path.slice(0, k + 1).join('\u0000')
      let g = nodes.get(key)
      if (!g) {
        g = { kind: 'group', gid: path[k], children: [], mask: masks.get(path[k]) ?? null, count: 0 }
        nodes.set(key, g)
        list.push(g)
      }
      g.count++
      list = g.children
    }
    list.push({ kind: 'object', obj: o })
  }
  return root
}

/**
 * 复制对象时把组链整条换成新 id（就地改副本）：同一次复制里同一个旧组 id 换成同一个新 id（`fresh` 自己记表），
 * 副本不会和原件粘成同一组。旧构建只认 `groupId`：链失效时按一层组换。
 */
export function remapGroupPath(copy: CanvasObject, fresh: (oldGid: string, index: number) => string): void {
  const p = groupPathOf(copy)
  if (!p.length) return
  setGroupPath(copy, p.map((g, i) => fresh(g, i)))
}
