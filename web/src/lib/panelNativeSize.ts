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
import { produce } from 'immer'
import { panelRotation, rotationSwaps, type FigureDocument, type PanelObject } from '@/types/document'

/** 页面坐标里的一个点（mm），与 `documentStore` 的 `TxnAnchor` 同形。 */
export type PagePoint = { x: number; y: number }

/**
 * 把面板的原生图幅改成 `wMm × hMm`，页面尺寸按同一比例跟着走、缩放比不变。
 * 传入的是 immer 草稿（或可变对象）。
 *
 * `anchor`：换算时在页面上不动的那一点（手势刻意钉住的对边 / 整图锚点，见
 * `setTxnAnchor`）。不给 = 包围盒左上角，x/y 不动。**只有这一处写 x/y**。
 */
export function syncPanelNativeSize(
  o: PanelObject,
  wMm: number,
  hMm: number,
  opts: { anchor?: PagePoint } = {},
): void {
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
    o.w *= swaps ? ky : kx
    o.h *= swaps ? kx : ky
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
  before: readonly [number, number]
  after: readonly [number, number]
}

/** 比较一条历史前后的文档，找出页面尺寸被改过、两侧都在的面板，记下原生图幅。 */
export function sizeBasisOf(before: FigureDocument, after: FigureDocument): SizeBasis[] {
  const prev = new Map<string, PanelObject>()
  for (const o of before.objects) if (o.type === 'panel') prev.set(o.id, o)
  const out: SizeBasis[] = []
  for (const o of after.objects) {
    if (o.type !== 'panel') continue
    const p = prev.get(o.id)
    // 新加 / 删掉的面板：补丁里带着整个对象（w/h 与 nativeW/H 同一份），自洽
    if (!p || (p.w === o.w && p.h === o.h)) continue
    out.push({ id: o.id, before: [p.nativeW, p.nativeH], after: [o.nativeW, o.nativeH] })
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
      // 先让面板「回到」它被记下时的基准，再走与渲染同步同一条换算
      o.nativeW = b[side][0]
      o.nativeH = b[side][1]
      syncPanelNativeSize(o, now[0], now[1])
    }
  })
}
