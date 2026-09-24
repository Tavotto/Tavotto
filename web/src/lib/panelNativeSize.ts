/**
 * 面板的**原生图幅**与页面尺寸之间的换算：缩放比 = 页面宽 / 原生宽
 * （`lib/preflight.ts` 的 `panelScale`）。
 *
 * 原生图幅的权威在这个变体渲染回来的 manifest `size_mm` 上（图幅不是派生字段，
 * 见 `docs/rules/frontend/project-document-and-autosave.md`），它会在用户没做任何事
 * 的时候变（脚本被改过、换成脚本 figsize）。每一处「原生图幅换了」都必须让页面
 * 尺寸按同一比例跟着走，否则缩放比静默改变——预检按「页面上量到的 pt = 脚本值 ×
 * 缩放比」判字号 / 线宽，比例错了就报一串假问题，一键修复再照着错的比例改图。
 *
 * 这里是那条换算的**唯一出处**：渲染回来时的同步（`useEngineSync`）与撤销 / 重做
 * 时的换基（`documentStore`）都调它，两处各写一遍就会有一处哪天把旋转写漏。
 */
import { produce, type Patch } from 'immer'
import { panelRotation, rotationSwaps, type FigureDocument, type PanelObject } from '@/types/document'

/** 页面坐标里的一个点（mm），与 `documentStore` 的 `TxnAnchor` 同形。 */
export type PagePoint = { x: number; y: number }

/** 页面包围盒的两维各自要不要换算。 */
export type PageDims = { readonly w: boolean; readonly h: boolean }
const BOTH: PageDims = { w: true, h: true }

/**
 * 把面板的原生图幅改成 `wMm × hMm`，页面尺寸按同一比例跟着走、缩放比不变。
 * 传入的是 immer 草稿（或可变对象）。
 *
 * `anchor`：换算时在页面上不动的那一点（手势刻意钉住的对边 / 整图锚点，见
 * `setTxnAnchor`）。不给 = 包围盒左上角，x/y 不动。**只有这一处写 x/y**。
 *
 * `dims`：只换算页面包围盒的哪几维（撤销 / 重做换基用：条目没打回的那一维本来就在
 * 此刻的单位上，再乘一遍就错了）。不给 = 两维都换。
 */
export function syncPanelNativeSize(
  o: PanelObject,
  wMm: number,
  hMm: number,
  opts: { anchor?: PagePoint; dims?: PageDims } = {},
): void {
  const dims = opts.dims ?? BOTH
  // x/y/w/h 是旋转后的页面包围盒：90/270 时内容的长宽是互换的
  const swaps = rotationSwaps(panelRotation(o))
  const [w0, h0] = [o.w, o.h]
  if (o.nativeW > 0 && o.nativeH > 0) {
    // **缩放比不变**：页面上的尺寸跟着原生图幅按同一比例走。只调高、
    // 不调宽的话，磁盘 PDF（`bbox_inches="tight"` 裁过，73.3 mm）换成
    // 脚本 figsize（80 mm）之后缩放比静默变成 0.917——读者量到的每个
    // 字号、线宽都凭空小了 8%，预检据此报出一串假问题，「全部处理」再
    // 照着这个比例把本来合规的图改掉（`addPanel` 按 100% 放入的约定也
    // 就此失效）。裁剪是比例，不用跟着动
    const kx = wMm / o.nativeW
    const ky = hMm / o.nativeH
    if (dims.w) o.w *= swaps ? ky : kx
    if (dims.h) o.h *= swaps ? kx : ky
  } else if (swaps) o.w = o.h * (hMm / wMm)
  else o.h = o.w * (hMm / wMm)
  // 位置按锚点反推：包围盒绕锚点按同一对比例伸缩，锚点在页面上不动
  const anchor = opts.anchor
  if (anchor) {
    if (w0 > 0) o.x = anchor.x + (o.x - anchor.x) * (o.w / w0)
    if (h0 > 0) o.y = anchor.y + (o.y - anchor.y) * (o.h / h0)
  }
  o.nativeW = wMm
  o.nativeH = hMm
}

/**
 * 一条历史改了某个面板的页面尺寸时，那两个尺寸是**按哪个原生图幅量的**。
 *
 * 撤销栈按 immer 补丁记账：缩放记下的是 `replace w/h`（绝对值），不含 nativeW/H——
 * 缩放没改它们。而原生图幅会在历史之外被静默同步改掉（渲染回来的 size_mm 变了，
 * `useEngineSync` 走 `silent`，不占用户的撤销步数）。此后撤销 / 重做把 w/h 打回
 * 按**旧图幅**量的绝对值，nativeW 却停在新图幅上：同一个变体键、同一个 manifest，
 * 同步器看 nativeW 已经等于 size_mm 不会再补，缩放比从此是错的。
 *
 * 所以每条改了面板 w/h 的历史顺带记下两侧的原生图幅（`before` = 条目之前、
 * `after` = 条目之后）：撤销 / 重做落地后，把打回来的 w/h 从它的基准换算到面板
 * 此刻的原生图幅（`rebaseToCurrentNative`）。运行时状态，不进文档。
 */
export interface SizeBasis {
  id: string
  /** 两侧的原生图幅 */
  before: readonly [number, number]
  after: readonly [number, number]
  /** 两侧的页面包围盒 w/h（条目「拥有」的那几维按它打回，见 `dims`） */
  box: { readonly before: readonly [number, number]; readonly after: readonly [number, number] }
  /**
   * 这条历史**打回**页面包围盒的哪几维：补丁里实际写了的那一维（`writtenPageDims`，
   * 前后值相等但压缩后仍留着的 replace 也算）；外加旋转改变了宽高与原生轴的
   * 对应（0↔90）时两维都算——正方形面板转 90° 的 w/h 数值不变、补丁里没有它们，
   * 可它们对应的原生轴已经互换，撤销时必须按条目那一侧重新摆（Codex #551 P2）。
   * 没打回的那一维从没离开此刻的单位，不换算（Codex #551 P1）。
   */
  dims: PageDims
}

/**
 * 一组正向补丁**实际写了**哪些对象的 w / h（按对象 id）。
 *
 * 判据是补丁里有没有这条路径，不是前后值是否相等：几何事务压缩之后同一路径只留一条，
 * 可一个中途动过、松手时回到原值的 h 仍在两侧留着 `replace h`（前后值相等），撤销 /
 * 重做照样把它打回按当时图幅量的绝对值。路径里的是**数组下标**，补丁按顺序落在
 * 逐步变化的文档上，所以从条目之前的 id 序列起步、按 `objects` 这一层的增删替换
 * 跟着挪；整个对象被写进去（add / replace 一个元素、替换整个数组）也算写了 w / h。
 */
export function writtenPageDims(before: FigureDocument, patches: readonly Patch[]): Map<string, { w: boolean; h: boolean }> {
  const ids: (string | undefined)[] = before.objects.map((o) => o.id)
  const out = new Map<string, { w: boolean; h: boolean }>()
  const mark = (id: string | undefined, w: boolean, h: boolean) => {
    if (id == null) return
    const cur = out.get(id) ?? { w: false, h: false }
    out.set(id, { w: cur.w || w, h: cur.h || h })
  }
  const idOf = (v: unknown) =>
    v != null && typeof v === 'object' && typeof (v as { id?: unknown }).id === 'string'
      ? (v as { id: string }).id
      : undefined
  for (const p of patches) {
    if (p.path[0] !== 'objects') continue
    if (p.path.length === 1) {
      // 整个数组被替换
      const list = Array.isArray(p.value) ? (p.value as unknown[]) : []
      ids.splice(0, ids.length, ...list.map(idOf))
      for (const id of ids) mark(id, true, true)
      continue
    }
    const key = p.path[1]
    if (p.path.length === 2) {
      if (key === 'length') {
        ids.length = Number(p.value)
        continue
      }
      const i = Number(key)
      if (p.op === 'remove') ids.splice(i, 1)
      else {
        const id = idOf(p.value)
        if (p.op === 'add') ids.splice(i, 0, id)
        else ids[i] = id
        mark(id, true, true)
      }
      continue
    }
    const prop = p.path[2]
    if (p.path.length === 3 && (prop === 'w' || prop === 'h')) {
      mark(ids[Number(key)], prop === 'w', prop === 'h')
    }
  }
  return out
}

/**
 * 比较一条历史前后的文档，找出页面尺寸被这条历史写过、两侧都在的面板，记下原生图幅。
 * `patches` 是这条历史**落进栈的**正向补丁（事务压缩之后的那组）。
 */
export function sizeBasisOf(
  before: FigureDocument,
  after: FigureDocument,
  patches: readonly Patch[],
): SizeBasis[] {
  const written = writtenPageDims(before, patches)
  const prev = new Map<string, PanelObject>()
  for (const o of before.objects) if (o.type === 'panel') prev.set(o.id, o)
  const out: SizeBasis[] = []
  for (const o of after.objects) {
    if (o.type !== 'panel') continue
    const p = prev.get(o.id)
    // 新加 / 删掉的面板：补丁里带着整个对象（w/h 与 nativeW/H 同一份），自洽
    if (!p) continue
    const axesSwapped = rotationSwaps(panelRotation(p)) !== rotationSwaps(panelRotation(o))
    const wrote = written.get(o.id)
    const dims = {
      w: axesSwapped || !!wrote?.w || p.w !== o.w,
      h: axesSwapped || !!wrote?.h || p.h !== o.h,
    }
    if (!dims.w && !dims.h) continue
    out.push({
      id: o.id,
      before: [p.nativeW, p.nativeH],
      after: [o.nativeW, o.nativeH],
      box: { before: [p.w, p.h], after: [o.w, o.h] },
      dims,
    })
  }
  return out
}

/**
 * 撤销（`side='before'`）/ 重做（`side='after'`）落地之后：面板的 w/h 是按条目记下的
 * 原生图幅量的，把它换算到面板此刻的原生图幅上，缩放比 = 条目那一侧的缩放比。
 *
 * 条目自己改过 nativeW/H 时（几何事务里并入的同步），补丁已经把它打回了同一侧，
 * 基准与此刻相等、换算系数为 1——不需要单独分支。
 */
export function rebaseToCurrentNative(
  doc: FigureDocument,
  basis: readonly SizeBasis[] | undefined,
  side: 'before' | 'after',
): FigureDocument {
  if (!basis?.length) return doc
  const pending = basis.filter((b) => {
    const o = doc.objects.find((x) => x.id === b.id)
    const [bw, bh] = b[side]
    return (
      o?.type === 'panel' &&
      bw > 0 &&
      bh > 0 &&
      o.nativeW > 0 &&
      o.nativeH > 0 &&
      (o.nativeW !== bw || o.nativeH !== bh)
    )
  })
  if (!pending.length) return doc
  return produce(doc, (d) => {
    for (const b of pending) {
      const o = d.objects.find((x) => x.id === b.id)
      if (o?.type !== 'panel') continue
      const now: [number, number] = [o.nativeW, o.nativeH]
      // 先让面板「回到」它被记下时的那一侧：条目拥有的那几维按记下的值摆好（补丁
      // 已经打回的就是这个值；正方形转 90° 那种补丁里没有的也在这里补上），原生图幅
      // 取那一侧的基准；再走与渲染同步同一条换算，只换这几维。锚点取默认的左上角：
      // 与事务外的 silent 同步同一个锚点，重做那一侧正是它换算过的状态，逐位回得去
      if (b.dims.w) o.w = b.box[side][0]
      if (b.dims.h) o.h = b.box[side][1]
      o.nativeW = b[side][0]
      o.nativeH = b[side][1]
      syncPanelNativeSize(o, now[0], now[1], { dims: b.dims })
    }
  })
}
