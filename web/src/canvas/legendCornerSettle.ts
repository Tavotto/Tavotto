import type { Rect4 } from '@/lib/axesLayout'
import { round4 } from '@/lib/axesLayout'
import { fixedCornerOf, type LegendCorner } from '@/lib/legendScale'
import { amendOverrides } from '@/store/actions'
import { useDocumentStore, type HistoryEntry } from '@/store/documentStore'
import { exactPanelRender, renderKeyOf, useRenderStore } from '@/store/renderStore'
import { retargetPreview } from '@/store/svgPreviewStore'
import type { PanelObject } from '@/types/document'
import { effectiveOverride } from '@/lib/effectiveOverride'

/** 对角偏差小于这么多（mm）就不补——比一个屏幕像素还小，补了只多一次渲染 */
export const CORNER_SETTLE_TOL_MM = 0.05

/** 等第一版成图的上限：再没来就放弃补正（渲染失败 / 切走了），不留一个挂着的订阅 */
const SETTLE_TIMEOUT_MS = 30_000

const livePanel = (id: string): PanelObject | null => {
  const o = useDocumentStore.getState().doc.objects.find((x) => x.id === id)
  return o?.type === 'panel' ? o : null
}

/**
 * 图例整体缩放松手之后，**按权威渲染的实测把对角钉回去**（#575 Codex 评审）。
 *
 * `loc_frac` 只钉图例的左下角，而成图的实际倍数与预览不完全相同：往小缩时每行
 * 有个不随字号缩的最小高度（示意线、标记），×0.8 实测高度只缩到 0.925。拖下面
 * 两个角时本该不动的上边于是会漂几个 pt。
 *
 * 做法：第一版成图（`loc_frac` 按预测写的那一版）一到，读它 manifest 里图例的实际框，
 * 量出不动那个角偏了多少（δ）；超过 `CORNER_SETTLE_TOL_MM` 就把 δ 补进**同一条历史**
 * （`amendOverrides`，撤销一次两笔一起退），再渲染一次。
 *
 * 看不到中间那一版：回调挂在渲染 store 上，**早于 React 把那一版换进 DOM**；此刻先把
 * 预览改挂到那一版上、平移 δ、改等补正后的那一版（`retargetPreview`）——那一版的
 * 图例大小已经对了，只差位置，平移 δ 后就是终态的样子。
 *
 * 几何只信权威：量的是**那一版自己**的 exact manifest。manifest 的文字度量与画布不一致
 * 时（#576 修复之前）量到的框本身就偏，补正会越补越偏——依赖 #579。
 *
 * 放弃补正的情形：文档在等待期间又变了（用户接着编辑 / 撤销）、刚才那条已不是最后
 * 一条历史、超时。
 */
export function settleLegendCorner(opts: {
  panelId: string
  gid: string
  corner: LegendCorner
  /** 用户按住的对角应当落在哪（figure 分数、top-origin） */
  fixed: [number, number]
  /** 这次缩放那条历史 */
  entry: HistoryEntry
}): () => void {
  const { panelId, gid, corner, fixed, entry } = opts
  const start = livePanel(panelId)
  if (!start) return () => {}
  const firstKey = renderKeyOf(start)
  let done = false
  let unsub: () => void = () => {}
  const stop = () => {
    if (done) return
    done = true
    unsub()
    window.clearTimeout(timer)
  }
  const check = () => {
    if (done) return
    const panel = livePanel(panelId)
    // 等待期间文档又变了：那一版已经不是用户要的样子，补正无从谈起
    if (!panel || renderKeyOf(panel) !== firstKey) return stop()
    const render = exactPanelRender(useRenderStore.getState(), panel)
    if (!render?.manifest) return
    stop()
    const el = render.manifest.elements.find((e) => e.gid === gid)
    const loc = effectiveOverride(panel.overrides, gid, 'loc_frac')?.value
    if (!el || !Array.isArray(loc) || loc.length !== 2) return
    const actual = fixedCornerOf(el.bbox as Rect4, corner)
    const dx = fixed[0] - actual[0]
    const dy = fixed[1] - actual[1]
    const [wMm, hMm] = render.manifest.size_mm ?? [0, 0]
    if (Math.abs(dx * wMm) < CORNER_SETTLE_TOL_MM && Math.abs(dy * hMm) < CORNER_SETTLE_TOL_MM) return
    // 先确认补得进同一条历史，再改挂预览：补不进去却改挂了，画面会停在一个文档里没有的位置
    const doc = useDocumentStore.getState()
    if (doc.txn || doc.past.at(-1) !== entry) return
    const next = [round4((loc as number[])[0] + dx), round4((loc as number[])[1] + dy)]
    const nextPanel = {
      ...panel,
      overrides: panel.overrides.map((o) =>
        o.gid === gid && o.prop === 'loc_frac' ? { ...o, value: next } : o,
      ),
    }
    retargetPreview(panelId, firstKey, { [gid]: [dx, dy] }, renderKeyOf(nextPanel))
    amendOverrides(panelId, entry, [{ gid, prop: 'loc_frac', value: next }])
  }
  unsub = useRenderStore.subscribe(check)
  const timer = window.setTimeout(stop, SETTLE_TIMEOUT_MS)
  // 那一版可能已经在缓存里（同一个变体刚画过）
  check()
  return stop
}
